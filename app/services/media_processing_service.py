"""
本文件用于对新闻页音视频直链执行受限下载、视频抽帧和音频提取。
媒体文件只在临时目录中存在，返回给调用方的是图片数据 URI 与待转写音频字节。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlparse

import aiohttp

from app.core.config import get_settings

logger = logging.getLogger("MediaProcessingService")
settings = get_settings()

_VIDEO_MIME_TYPES = {"video/mp4", "video/webm", "video/quicktime", "video/x-m4v"}
_AUDIO_MIME_TYPES = {
    "audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/mp4",
    "audio/aac", "audio/ogg", "audio/opus", "audio/flac",
}
_VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".m4v"}
_AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".flac"}


@dataclass
class AudioPayload:
    """待发送到语音转写接口的音频内容。"""

    filename: str
    content_type: str
    content: bytes


@dataclass
class MediaEvidence:
    """新闻音视频预处理后的可分析证据，不包含需要持久化的临时文件。"""

    frame_data_uris: List[str] = field(default_factory=list)
    audio_payloads: List[AudioPayload] = field(default_factory=list)
    video_urls: List[str] = field(default_factory=list)
    audio_urls: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)

    def to_public_dict(self, *, transcript_count: int = 0) -> Dict[str, Any]:
        """输出可安全写入数据库的处理信息，不写入媒体二进制数据。"""

        return {
            "video_count": len(self.video_urls),
            "audio_count": len(self.audio_urls),
            "frame_count": len(self.frame_data_uris),
            "transcript_count": max(0, int(transcript_count)),
            "temporary_processing": True,
            "skipped": self.skipped[:8],
        }


class MediaProcessingService:
    """负责下载媒体并通过 FFmpeg 提取视频视觉帧和音频轨道。"""

    def _ffmpeg_path(self) -> Optional[str]:
        """返回可执行 FFmpeg 路径；未安装时返回 None。"""

        configured = str(getattr(settings, "MEDIA_FFMPEG_PATH", "ffmpeg") or "ffmpeg").strip()
        return configured if Path(configured).is_file() or shutil.which(configured) else None

    @staticmethod
    def _deduplicate_urls(values: Optional[Sequence[Any]], limit: int) -> List[str]:
        """过滤空值并限制单条新闻需要下载的媒体 URL 数量。"""

        result: List[str] = []
        for value in values or []:
            url = str(value or "").strip()
            if url.startswith(("http://", "https://")) and url not in result:
                result.append(url)
            if len(result) >= max(0, int(limit)):
                break
        return result

    @staticmethod
    def _suffix_for(url: str, content_type: str, *, fallback: str) -> str:
        """根据 URL 或 MIME 类型生成安全的临时文件扩展名。"""

        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix:
            return suffix
        mapping = {
            "video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov",
            "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav",
            "audio/mp4": ".m4a", "audio/ogg": ".ogg", "audio/opus": ".opus",
        }
        return mapping.get(content_type, fallback)

    async def _download_media(
        self,
        session: aiohttp.ClientSession,
        url: str,
        *,
        kind: str,
    ) -> Optional[tuple[bytes, str]]:
        """以字节上限下载一个媒体直链，并校验状态码和 MIME 类型。"""

        max_bytes = max(1024, int(getattr(settings, "MEDIA_DOWNLOAD_MAX_BYTES", 50 * 1024 * 1024)))
        allowed_mimes = _VIDEO_MIME_TYPES if kind == "video" else _AUDIO_MIME_TYPES
        allowed_suffixes = _VIDEO_SUFFIXES if kind == "video" else _AUDIO_SUFFIXES
        try:
            async with session.get(url, allow_redirects=True, proxy=str(settings.CRAWLER_PROXY or "").strip() or None) as response:
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                suffix = Path(urlparse(str(response.url)).path).suffix.lower()
                if response.status != 200 or (content_type not in allowed_mimes and suffix not in allowed_suffixes):
                    logger.debug("跳过%s媒体：status=%s, mime=%s, url=%s", kind, response.status, content_type or "unknown", url)
                    return None
                chunks: List[bytes] = []
                size = 0
                async for chunk in response.content.iter_chunked(64 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        logger.info("跳过超出大小限制的%s媒体：%s", kind, url)
                        return None
                    chunks.append(chunk)
                if not chunks:
                    return None
                return b"".join(chunks), content_type or ("video/mp4" if kind == "video" else "audio/mpeg")
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            logger.debug("下载%s媒体失败：%s (%s)", kind, url, exc)
            return None

    async def _run_ffmpeg(self, *args: str) -> bool:
        """异步执行一次受限 FFmpeg 命令，失败时记录简短诊断。"""

        ffmpeg = self._ffmpeg_path()
        if not ffmpeg:
            logger.warning("未找到 FFmpeg，跳过视频抽帧与音轨提取")
            return False
        try:
            process = await asyncio.create_subprocess_exec(
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=90)
            if process.returncode != 0:
                logger.debug("FFmpeg 处理失败：%s", stderr.decode("utf-8", errors="ignore")[:300])
                return False
            return True
        except (OSError, asyncio.TimeoutError) as exc:
            logger.debug("FFmpeg 无法执行：%s", exc)
            return False

    async def _extract_video_evidence(self, video_path: Path, workdir: Path) -> tuple[List[str], Optional[AudioPayload]]:
        """从单个视频中均匀抽取关键帧，并尽量提取用于转写的单声道音轨。"""

        max_seconds = max(1, int(getattr(settings, "MEDIA_VIDEO_MAX_SECONDS", 120)))
        max_frames = max(1, int(getattr(settings, "MEDIA_VIDEO_MAX_FRAMES", 8)))
        frame_pattern = workdir / "frame-%02d.jpg"
        fps = max_frames / float(max_seconds)
        frames_ok = await self._run_ffmpeg(
            "-t", str(max_seconds), "-i", str(video_path), "-vf", f"fps={fps}",
            "-frames:v", str(max_frames), "-q:v", "4", str(frame_pattern),
        )
        frames: List[str] = []
        if frames_ok:
            for frame in sorted(workdir.glob("frame-*.jpg"))[:max_frames]:
                content = frame.read_bytes()
                if content:
                    frames.append(f"data:image/jpeg;base64,{base64.b64encode(content).decode('ascii')}")

        audio_path = workdir / "video-audio.wav"
        audio_ok = await self._run_ffmpeg(
            "-t", str(max_seconds), "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_s16le", str(audio_path),
        )
        audio = None
        if audio_ok and audio_path.exists() and audio_path.stat().st_size > 0:
            audio = AudioPayload("video-audio.wav", "audio/wav", audio_path.read_bytes())
        return frames, audio

    async def prepare_evidence(
        self,
        videos: Optional[Sequence[Any]],
        audios: Optional[Sequence[Any]],
    ) -> MediaEvidence:
        """下载新闻音视频并生成模型可用的关键帧和音频载荷，处理结束自动删除文件。"""

        evidence = MediaEvidence(
            video_urls=self._deduplicate_urls(videos, int(getattr(settings, "MEDIA_FETCH_MAX_VIDEOS", 2))),
            audio_urls=self._deduplicate_urls(audios, int(getattr(settings, "MEDIA_FETCH_MAX_AUDIOS", 2))),
        )
        if not bool(getattr(settings, "MEDIA_ANALYSIS_ENABLED", True)) or not (evidence.video_urls or evidence.audio_urls):
            return evidence

        timeout = aiohttp.ClientTimeout(total=max(3.0, float(getattr(settings, "MEDIA_DOWNLOAD_TIMEOUT_SECONDS", 30.0))))
        headers = {"User-Agent": str(getattr(settings, "AI_USER_AGENT", "TrendSonar/0.2.8"))}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            with tempfile.TemporaryDirectory(prefix="sourcesonar-media-") as temp_dir:
                workdir = Path(temp_dir)
                for index, url in enumerate(evidence.video_urls):
                    downloaded = await self._download_media(session, url, kind="video")
                    if not downloaded:
                        evidence.skipped.append(f"video_download:{url}")
                        continue
                    content, content_type = downloaded
                    path = workdir / f"video-{index}{self._suffix_for(url, content_type, fallback='.mp4')}"
                    path.write_bytes(content)
                    video_workdir = workdir / f"video-work-{index}"
                    video_workdir.mkdir(parents=True, exist_ok=True)
                    frames, audio = await self._extract_video_evidence(path, video_workdir)
                    evidence.frame_data_uris.extend(frames)
                    if audio:
                        evidence.audio_payloads.append(audio)
                for index, url in enumerate(evidence.audio_urls):
                    downloaded = await self._download_media(session, url, kind="audio")
                    if not downloaded:
                        evidence.skipped.append(f"audio_download:{url}")
                        continue
                    content, content_type = downloaded
                    evidence.audio_payloads.append(
                        AudioPayload(f"audio-{index}{self._suffix_for(url, content_type, fallback='.mp3')}", content_type, content)
                    )
        return evidence


media_processing_service = MediaProcessingService()
