"""
本文件用于构造新闻多模态分析所需的 OpenAI 兼容消息，并统一清洗图片地址。
主要函数:
- `normalize_multimodal_image_urls`: 清洗、去重并限制多模态输入图片
- `build_multimodal_chat_messages`: 构造包含文本与图片的对话消息
"""

from __future__ import annotations

import asyncio
import base64
from io import BytesIO
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlparse

import aiohttp
from PIL import Image

from app.utils.media_extractor import is_supported_image_url

_SUPPORTED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_UNSUPPORTED_IMAGE_HOSTS = {"styles.redditmedia.com", "v.redd.it"}
_UNSUPPORTED_MEDIA_SUFFIXES = (".m3u8", ".mp4", ".webm", ".mov", ".avi")


def is_data_image_uri(value: str) -> bool:
    """判断值是否为允许发送到硅基流动的 base64 图片数据 URI。"""

    lowered = value.lower()
    return lowered.startswith(tuple(f"data:{mime};base64," for mime in _SUPPORTED_IMAGE_MIME_TYPES))


def _is_valid_image_binary(content: bytes) -> bool:
    """校验下载结果确实是完整可读取的图片，避免把截断响应发送给模型。"""

    try:
        with Image.open(BytesIO(content)) as image:
            image.verify()
        return True
    except (OSError, ValueError):
        return False


def normalize_multimodal_image_urls(
    image_urls: Optional[Sequence[Any]],
    *,
    max_images: int,
) -> List[str]:
    """
    输入:
    - `image_urls`: 新闻图片地址序列
    - `max_images`: 最多保留的图片数量

    输出:
    - 去重、过滤后的公开 HTTP(S) 图片地址列表

    作用:
    - 避免把空值、本地路径、重复链接、占位图或明显非图片资源发送给多模态模型。
    """

    limit = max(0, int(max_images))
    if not image_urls or limit <= 0:
        return []

    normalized: List[str] = []
    seen: set[str] = set()
    for raw_url in image_urls:
        url = str(raw_url or "").strip()
        parsed = urlparse(url)
        if (
            not url.startswith(("http://", "https://"))
            or url in seen
            or not is_supported_image_url(url, allow_extensionless=True)
            or parsed.netloc.lower() in _UNSUPPORTED_IMAGE_HOSTS
            or parsed.path.lower().endswith(_UNSUPPORTED_MEDIA_SUFFIXES)
        ):
            continue
        seen.add(url)
        normalized.append(url)
        if len(normalized) >= limit:
            break
    return normalized


async def prepare_multimodal_image_inputs(
    image_urls: Optional[Sequence[Any]],
    *,
    max_images: int,
    max_bytes: int,
    timeout_seconds: float,
    headers: Optional[Dict[str, str]] = None,
    proxy: Optional[str] = None,
) -> List[str]:
    """
    输入:
    - `image_urls`: 新闻抓取阶段保存的候选图片地址
    - `max_images`: 最多保留的可用图片数
    - `max_bytes`: 单张图片的最大下载字节数
    - `timeout_seconds`: 单张图片下载超时秒数
    - `headers`/`proxy`: 可选下载请求配置

    输出:
    - 硅基流动可直接读取的 base64 图片数据 URI 列表

    作用:
    - 先验证图片能被本服务实际下载且 MIME 合法，再以内联数据发送，避免第三方图床防盗链或无效 URL 使整次多模态请求失败。
    """

    target_count = max(0, int(max_images))
    if not image_urls or target_count <= 0:
        return []

    inline_images: List[str] = []
    for raw_url in image_urls:
        value = str(raw_url or "").strip()
        if is_data_image_uri(value) and value not in inline_images:
            inline_images.append(value)
        if len(inline_images) >= target_count:
            return inline_images

    candidates = normalize_multimodal_image_urls(
        image_urls,
        max_images=max((target_count - len(inline_images)) * 4, target_count),
    )
    if not candidates:
        return inline_images

    safe_bytes = max(1, int(max_bytes))
    timeout = aiohttp.ClientTimeout(total=max(1.0, float(timeout_seconds)))
    request_headers = {"Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"}
    if headers:
        request_headers.update(headers)
    data_uris: List[str] = list(inline_images)

    async with aiohttp.ClientSession(timeout=timeout, headers=request_headers) as session:
        for url in candidates:
            try:
                async with session.get(url, allow_redirects=True, proxy=proxy or None) as response:
                    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    if response.status != 200 or content_type not in _SUPPORTED_IMAGE_MIME_TYPES:
                        continue
                    chunks: List[bytes] = []
                    size = 0
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        size += len(chunk)
                        if size > safe_bytes:
                            break
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    if not content or size > safe_bytes or not _is_valid_image_binary(content):
                        continue
                    encoded = base64.b64encode(content).decode("ascii")
                    data_uris.append(f"data:{content_type};base64,{encoded}")
                    if len(data_uris) >= target_count:
                        break
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                continue
    return data_uris


def build_multimodal_chat_messages(
    prompt: str,
    system_prompt: str = "",
    *,
    image_urls: Optional[Sequence[Any]] = None,
    max_images: int = 3,
    image_detail: str = "auto",
) -> List[Dict[str, Any]]:
    """
    输入:
    - `prompt`: 新闻分析用户提示词
    - `system_prompt`: 新闻分析系统提示词
    - `image_urls`: 新闻图片地址序列
    - `max_images`: 最多发送的图片数量
    - `image_detail`: 图片细节级别，可选 auto、low、high

    输出:
    - 可直接传给 `/chat/completions` 的多模态消息列表

    作用:
    - 按硅基流动 OpenAI 兼容格式组合文本与 `image_url` 内容块；无有效图片时仍返回合法纯文本消息。
    """

    user_prompt = str(prompt or "").strip()
    if not user_prompt:
        raise ValueError("多模态分析请求内容为空")

    detail = str(image_detail or "auto").strip().lower()
    if detail not in {"auto", "low", "high"}:
        detail = "auto"

    images: List[str] = []
    for raw_url in image_urls or []:
        value = str(raw_url or "").strip()
        if is_data_image_uri(value):
            images.append(value)
        if len(images) >= max_images:
            break
    if not images:
        images = normalize_multimodal_image_urls(image_urls, max_images=max_images)
    user_content: List[Dict[str, Any]] = [
        {
            "type": "image_url",
            "image_url": {"url": url, "detail": detail},
        }
        for url in images
    ]
    if images:
        user_prompt = (
            f"{user_prompt}\n\n"
            f"本次附带 {len(images)} 张新闻相关图片。请按输入顺序综合识别画面、图中文字和情绪线索，"
            "并与标题、正文相互核验；不要根据图片猜测无法确认的身份或事实。"
        )
    user_content.append({"type": "text", "text": user_prompt})

    messages: List[Dict[str, Any]] = []
    system = str(system_prompt or "").strip()
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user_content})
    return messages
