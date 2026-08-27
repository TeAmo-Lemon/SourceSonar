"""
本文件用于识别并过滤“无法访问 / 需登录”类无效新闻内容：
- 抓取阶段（crawler_service._process_meta）命中即丢弃，避免垃圾入库
- 已入库的垃圾可通过 pipeline_service.cleanup_blocked_news 定期清理
主要函数:
- `is_media_file_url`: 判断链接是否为纯媒体文件直链（图片/视频/音频等）
- `is_blocked_access_text`: 判断文本是否命中登录墙/访问受限提示特征
- `is_blocked_access_item`: 综合判断一条新闻是否属于无效内容
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

# 纯媒体/附件文件直链的扩展名（正常新闻的规范链接不应是这些文件本身）
_MEDIA_FILE_EXT_RE = re.compile(
    r"\.(?:jpe?g|png|gif|webp|bmp|svg|avif|ico|mp4|webm|mov|m4v|mp3|wav|flac|pdf|zip|rar|7z|tar|gz|apk|exe|dmg|iso)(?:[?#]|$)",
    re.IGNORECASE,
)

# 登录墙 / 访问受限提示的强特征短语（命中即视为无效内容）。
# 只保留“登录墙专属 / 明确拒绝访问”的措辞；
# 泛化的“需登录/登录后才能”等交给组合规则（须同时出现受限词），
# 避免误杀“推特将要求用户登录后才能查看推文”这类报道登录墙政策的真实新闻。
_BLOCKED_ACCESS_PHRASES = (
    # 中文：登录墙专属措辞
    "请登录",
    "请先登录",
    "登录账户",
    "登录入口",
    "登录页面",
    "需登录reddit",
    "开发者令牌",
    # 中文：明确拒绝/阻止访问的措辞
    "拒绝访问",
    "访问被拒绝",
    "访问被阻止",
    "被阻止访问",
    "拒绝访问该",
    "禁止访问该",
    # 英文：登录墙专属措辞
    "login required",
    "please login",
    "please log in",
    "you must be logged in",
    "must log in",
    "must sign in",
    "sign in required",
    "developer token",
    # 英文：明确阻止/限流
    "access denied",
    "access blocked",
    "you are blocked",
    "blocked from",
    "rate limited",
    "request blocked",
)

# 组合规则用的弱信号词（需与限制词、无能为力词一起出现才判定）
_LOGIN_HINTS = ("登录", "登陆", "sign in", "log in", "login")
_RESTRICT_HINTS = ("受限", "无法访问", "阻止", "拒绝访问", "被封", "封禁", "屏蔽", "blocked", "denied", "restricted", "banned", "forbidden")
_INABILITY_HINTS_ZH = ("需", "请", "要求", "必须", "无法", "不能", "被", "阻止")
_INABILITY_HINTS_EN = ("please", "must", "required", "need", "to continue", "unable", "cannot", "you are")
# 组合规则仅适用于短文本（登录墙提示通常很短，避免误伤正常长文）
_COMBINED_MAX_LENGTH = 150

# 组合规则用的弱信号词（需与限制词、无能为力词一起出现才判定）
_LOGIN_HINTS = ("登录", "登陆", "sign in", "log in", "login")
_RESTRICT_HINTS = ("受限", "无法访问", "阻止", "访问被拒绝", "拒绝访问", "被封", "封禁", "屏蔽", "blocked", "denied", "restricted", "banned", "forbidden")
_INABILITY_HINTS_ZH = ("需", "请", "要求", "必须", "无法", "不能", "被", "阻止")
_INABILITY_HINTS_EN = ("please", "must", "required", "need", "to continue", "unable", "cannot", "you are")
# 组合规则仅适用于短文本（登录墙提示通常很短，避免误伤正常长文）
_COMBINED_MAX_LENGTH = 150


def is_media_file_url(url: Optional[str]) -> bool:
    """
    输入:
    - `url`: 新闻链接

    输出:
    - 是否为纯媒体/附件文件直链

    作用:
    - 正常新闻的规范链接是网页地址；以图片/视频/压缩包等文件结尾的多为解析垃圾。
    """

    if not url:
        return False
    path = urlparse(url).path
    return bool(_MEDIA_FILE_EXT_RE.search(path))


def is_blocked_access_text(text: Optional[str]) -> bool:
    """
    输入:
    - `text`: 待检测的标题/摘要/正文文本

    输出:
    - 是否命中登录墙或访问受限提示特征

    作用:
    - 强特征短语直接命中；弱信号采用“登录词 + 受限词 + 无能为力词”组合且文本较短的规则，
      在保证召回的同时尽量降低对正常新闻的误杀。
    """

    if not text:
        return False
    lowered = text.lower()
    if any(phrase in lowered for phrase in _BLOCKED_ACCESS_PHRASES):
        return True

    # 组合规则：短文本中同时出现登录词与受限词，并带有“需/请/无法/被”等无能为力措辞
    if len(text) <= _COMBINED_MAX_LENGTH:
        has_login = any(hint in lowered for hint in _LOGIN_HINTS)
        has_restrict = any(hint in lowered for hint in _RESTRICT_HINTS)
        if has_login and has_restrict:
            if any(hint in lowered for hint in _INABILITY_HINTS_ZH) or any(hint in lowered for hint in _INABILITY_HINTS_EN):
                return True
    return False


def is_blocked_access_item(
    title: Optional[str],
    summary: Optional[str],
    content: Optional[str],
    url: Optional[str],
    *,
    heat: Optional[float] = None,
    heat_guard: float = 10.0,
) -> bool:
    """
    输入:
    - `title`/`summary`/`content`: 新闻的标题、摘要与正文
    - `url`: 新闻链接
    - `heat`: 新闻热度（可选）
    - `heat_guard`: 热度保护阈值；热度不低于该值的新闻即使命中文本特征也不判为垃圾

    输出:
    - 是否属于“无法访问 / 需登录”类无效内容

    作用:
    - 抓取阶段与清理阶段共用的统一判定入口：
      图片/附件直链直接判垃圾；文本命中特征时，高热度真实新闻（可能只是提及登录话题）予以保护。
    """

    if is_media_file_url(url):
        return True
    texts = [t for t in (title, summary, content) if t]
    if not texts:
        return False
    if any(is_blocked_access_text(t) for t in texts):
        if heat is not None and float(heat) >= heat_guard:
            return False
        return True
    return False