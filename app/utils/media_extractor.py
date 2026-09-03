"""
本文件用于从新闻页面 HTML 中提取新闻图片资源链接，
供抓取流程在保存新闻正文时同步采集封面图与正文配图。

主要函数:
- `is_supported_image_url`: 判断链接是否为可用的图片直链
- `extract_image_urls_from_html`: 从 HTML 源码中提取图片链接
- `extract_media_from_crawl_result`: 从 crawl4ai / 轻量抓取的返回结果中提取媒体信息
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, unquote

from bs4 import BeautifulSoup

# 常见的图片扩展名（用作链接有效性判断的依据）
_IMAGE_EXT_RE = re.compile(
    r"\.(?:jpe?g|png|gif|webp|bmp|avif|svg)(?:[?#]|$)",
    re.IGNORECASE,
)

# 即使图片地址来自图片标签，也不应把明显的网页或脚本地址当作图片入库
_NON_IMAGE_FILE_EXT_RE = re.compile(
    r"\.(?:html?|php|aspx?|jsp|js|css|m3u8|mp4|webm|mov|avi)$",
    re.IGNORECASE,
)

# 常见的图片占位符 / 追踪像素，命中则过滤，避免把低价值图当作新闻配图
_IMAGE_PLACEHOLDER_HINTS = (
    "pixel",
    "spacer",
    "transparent",
    "placeholder",
    "blank.gif",
    "1x1",
    "loading.gif",
    "data:image",
)

# WhatsApp 等社交平台常见的图片前端资源前缀（多用于压缩图而非正文图）
_EXTERNAL_IMAGE_PREFIXES = (
    "https://external-content.duckduckgo.com",
    "https://styles.redditmedia.com",
    "https://v.redd.it",
)

# 常见图床广告位 CDN 片段，命中则过滤
_IMAGE_AD_HINTS = ("advert", "advertise", "banner-", "logo.", "avatar", "communityicon", "profileicon")

# 常见的正文图片选择器，用于让正文配图优先于页面导航、作者头像等资源入库
_ARTICLE_IMAGE_SELECTORS = (
    "article, main, [role='main'], .article-content, .article_content, "
    ".post-content, .post_content, .entry-content, .rich_media_content, "
    ".news-content, #article, #content"
)

# 新闻站点常用的原图、懒加载和响应式图片属性，按优先级从高到低排列
_IMAGE_URL_ATTRIBUTES = (
    "data-original",
    "data-src",
    "data-lazy-src",
    "data-original-src",
    "data-actualsrc",
    "data-url",
    "data-ks-lazyload",
    "src",
)

_IMAGE_SRCSET_ATTRIBUTES = ("data-srcset", "data-original-set", "srcset")


def is_supported_image_url(url: Optional[str], *, allow_extensionless: bool = False) -> bool:
    """
    输入:
    - `url`: 图片链接
    - `allow_extensionless`: 是否接受无图片扩展名的链接

    输出:
    - 是否为可用的图片直链

    作用:
    - 用于过滤 `.html` 分页链接、脚本生成的占位图及明显广告位；
      图片标签、Open Graph 元数据和响应式图片地址可安全地允许无扩展名 CDN 链接。
    """

    if not url:
        return False
    cleaned = url.strip()
    if not cleaned or cleaned.startswith("data:"):
        return False
    path = urlparse(cleaned).path
    if _NON_IMAGE_FILE_EXT_RE.search(path):
        return False
    if not allow_extensionless and not _IMAGE_EXT_RE.search(path):
        return False
    lowered = cleaned.lower()
    if any(hint in lowered for hint in _IMAGE_PLACEHOLDER_HINTS):
        return False
    return True


def _extract_srcset_urls(srcset: str) -> List[str]:
    """
    输入:
    - `srcset`: HTML 图片响应式候选地址字符串

    输出:
    - 按清晰度从高到低排列的图片地址列表

    作用:
    - 解析 `srcset` / `data-srcset`，优先保留高分辨率图片，兼容懒加载站点。
    """

    candidates: List[Tuple[float, str]] = []
    for item in (srcset or "").split(","):
        parts = item.strip().split()
        if not parts:
            continue
        score = 1.0
        if len(parts) > 1:
            descriptor = parts[-1].lower()
            try:
                if descriptor.endswith("w"):
                    score = float(descriptor[:-1])
                elif descriptor.endswith("x"):
                    score = float(descriptor[:-1]) * 10000
            except ValueError:
                pass
        candidates.append((score, parts[0]))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [url for _, url in candidates]


def _collect_tag_image_urls(tag: Any, base_url: str) -> List[str]:
    """
    输入:
    - `tag`: BeautifulSoup 图片或 source 标签
    - `base_url`: 页面基准地址

    输出:
    - 已补全为绝对地址的候选图片链接

    作用:
    - 统一读取普通图片、懒加载图片和 `srcset` 响应式图片地址，避免只命中占位图。
    """

    result: List[str] = []
    for attribute in _IMAGE_URL_ATTRIBUTES:
        normalized = _normalize_image_url(str(tag.get(attribute) or ""), base_url)
        if normalized:
            result.append(normalized)
    for attribute in _IMAGE_SRCSET_ATTRIBUTES:
        for candidate in _extract_srcset_urls(str(tag.get(attribute) or "")):
            normalized = _normalize_image_url(candidate, base_url)
            if normalized:
                result.append(normalized)
    return result


def _normalize_image_url(href: str, base_url: str) -> Optional[str]:
    """
    输入:
    - `href`: HTML 中读取到的图片地址（可能是相对路径）
    - `base_url`: 页面基准地址

    输出:
    - 补齐为绝对地址后的图片链接；无法解析时返回 None

    作用:
    - 兼容相对路径、协议相对地址（`//`）以及少见的无协议地址。
    """

    if not href:
        return None
    href = href.strip().strip("\"'")
    if not href or href.startswith("data:"):
        return None
    try:
        # 先解码 HTML 实体（如 &amp; -> &），避免把实体串进 URL 导致图片加载失败
        import html as _html
        href = _html.unescape(href)
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
        if parsed.scheme not in {"http", "https"}:
            return None
        return absolute
    except (ValueError, AttributeError):
        return None


def extract_image_urls_from_html(
    html: str,
    base_url: Optional[str] = None,
    *,
    max_images: int = 12,
) -> List[str]:
    """
    输入:
    - `html`: 页面 HTML 源码
    - `base_url`: 页面地址（用于补齐相对路径）
    - `max_images`: 最多返回的图片数量

    输出:
    - 去重后的图片绝对地址列表

    作用:
    - 从 `<img>`、`<picture>`、`meta[property=og:image]`、CSS background 中收集图片，
      优先返回文章正文区域内的图片，其次为封面图。
    """

    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")

    candidates: List[str] = []
    base = base_url or ""

    # 1. 优先收集文章正文区域（article/main 等）内的 img
    article_nodes = soup.select(_ARTICLE_IMAGE_SELECTORS)
    for node in article_nodes:
        for img in node.find_all("img"):
            candidates.extend(_collect_tag_image_urls(img, base))
        for source in node.find_all("source"):
            candidates.extend(_collect_tag_image_urls(source, base))

    # 2. 全页 img 兜底
    for img in soup.find_all("img"):
        candidates.extend(_collect_tag_image_urls(img, base))
    for source in soup.select("picture source"):
        candidates.extend(_collect_tag_image_urls(source, base))

    # 3. meta og:image / twitter:image
    for meta in soup.find_all("meta"):
        prop = (meta.get("property") or meta.get("name") or "").lower()
        if prop in {"og:image", "og:image:url", "twitter:image", "twitter:image:src", "image"}:
            content = meta.get("content") or ""
            normalized = _normalize_image_url(content, base)
            if normalized:
                candidates.append(normalized)

    # 4. 视频封面通常就是新闻正文的重要配图
    for video in soup.find_all("video"):
        poster = _normalize_image_url(str(video.get("poster") or ""), base)
        if poster:
            candidates.append(poster)

    # 5. CSS background 图片兜底
    for style in soup.find_all(style=True):
        css = style.get("style") or ""
        for match in re.findall(r"url\((['\"]?)([^)'\"]+)\1\)", css):
            normalized = _normalize_image_url(match[1], base)
            if normalized:
                candidates.append(normalized)

    # 6. 去重 + 过滤低价值图
    seen: set[str] = set()
    result: List[str] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if not is_supported_image_url(candidate, allow_extensionless=True):
            continue
        lowered = candidate.lower()
        if any(prefix in lowered for prefix in _EXTERNAL_IMAGE_PREFIXES):
            continue
        if any(hint in lowered for hint in _IMAGE_AD_HINTS):
            continue
        result.append(candidate)
        if len(result) >= max_images:
            break

    return result


def extract_media_from_crawl_result(
    result: Any,
    base_url: Optional[str] = None,
    *,
    max_images: int = 12,
) -> Dict[str, List[str]]:
    """
    输入:
    - `result`: crawl4ai 抓取结果对象（含 html / markdown 等属性）
    - `base_url`: 页面地址，用于补齐相对路径
    - `max_images`: 最多返回的图片数量

    输出:
    - `{"images": [...]}` 多媒体图片列表

    作用:
    - 统一从 crawl4ai 返回结果中提取媒体图片，避免各抓取路径各自实现。
    """

    images: List[str] = []
    html = None
    if result is not None:
        if isinstance(result, str):
            html = result
        else:
            html = getattr(result, "html", None)

    if html:
        images = extract_image_urls_from_html(str(html), base_url=base_url, max_images=max_images)
    else:
        # 没有 HTML 时，尝试从 markdown 图片语法中提取
        markdown = None
        if result is not None and not isinstance(result, str):
            md = getattr(result, "markdown", None)
            markdown = getattr(md, "raw_markdown", None) if md is not None else None
            if not markdown:
                markdown = str(md or "")
        if markdown:
            for match in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", markdown):
                normalized = _normalize_image_url(match.strip(), base_url or "")
                if normalized and is_supported_image_url(normalized, allow_extensionless=True):
                    images.append(normalized)
            images = list(dict.fromkeys(images))[:max_images]

    return {"images": images}


def pick_cover_image(image_urls: Optional[List[str]]) -> Optional[str]:
    """
    输入:
    - `image_urls`: 图片链接列表

    输出:
    - 封面图链接；无可用图片时返回 None

    作用:
    - 为前端卡片缩略图选择第一张有效图片。
    """

    if not image_urls:
        return None
    for url in image_urls:
        if is_supported_image_url(url, allow_extensionless=True):
            return url
    return None
