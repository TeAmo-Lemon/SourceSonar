"""
本文件用于封装 NewsAPI.org（https://newsapi.org/docs）全球新闻检索服务：
- 提供 v2/everything 端点的异步访问（查询构建见 trace_service，本文件只负责 HTTP 与限流）
- 内置进程内限流：请求最小间隔 + 滚动 24 小时日预算，避免超出免费额度
- 提供结果短缓存，相同查询在 TTL 内直接命中缓存，不重复消耗额度
- 提供新闻源域名/名称到国家（语区）的推断，供“全球新闻雷达”可视化使用
主要类/对象:
- `NewsApiClient`: NewsAPI 客户端
- `newsapi_client`: 全局单例实例
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlparse

import aiohttp

from app.core.config import get_settings
from app.core.logger import logger
from app.utils.ttl_cache import TtlMemoryCache

# NewsAPI v2 基础地址
NEWSAPI_BASE_URL = "https://newsapi.org/v2"

# 两次实际 HTTP 请求之间的最小间隔（秒）。
# NewsAPI 免费/开发者版每日额度仅 100 次，不设激进并发，保持保守节奏
MIN_REQUEST_INTERVAL_SECONDS = 3.0

# 滚动 24 小时内的请求预算（次）。
# 官方每日上限 100 次，这里进一步预留裕量，避免被上游限流
DAILY_REQUEST_BUDGET = 60

# 分析结果短缓存有效期（秒）。
# 同一事件短时间内重复分析直接复用缓存，保护 NewsAPI 额度
RESULT_CACHE_TTL_SECONDS = 15 * 60

# 单次分析允许的最大请求数（everything 单页最多 100 条，默认 1 次即可）
MAX_REQUESTS_PER_ANALYSIS = 2

# NewsAPI 支持的媒体语言取值（https://newsapi.org/docs/endpoints/everything）
SUPPORTED_LANGUAGES = {"ar", "de", "en", "es", "fr", "he", "it", "nl", "no", "pt", "ru", "sv", "ud", "zh"}
# 未识别出国家的文章按语区归组，展示更诚实的“媒体覆盖”信息
LANGUAGE_REGION_LABELS: Dict[str, str] = {
    "zh": "中文媒体",
    "en": "英文媒体",
    "ar": "阿拉伯语媒体",
    "de": "德语媒体",
    "fr": "法语媒体",
    "es": "西语媒体",
    "pt": "葡语媒体",
    "ru": "俄语媒体",
    "ja": "日语媒体",
    "ko": "韩语媒体",
}

# 常见国际媒体域名 -> 国家（用于推断报道来源的地理归属）
_DOMAIN_COUNTRY: Dict[str, str] = {
    # 中文媒体
    "news.cn": "中国",
    "xinhuanet.com": "中国",
    "chinadaily.com.cn": "中国",
    "cgtn.com": "中国",
    "people.com.cn": "中国",
    "globaltimes.cn": "中国",
    "scmp.com": "中国",
    "caixin.com": "中国",
    "ifeng.com": "中国",
    "163.com": "中国",
    "sina.com.cn": "中国",
    # 美国
    "cnn.com": "美国",
    "nytimes.com": "美国",
    "washingtonpost.com": "美国",
    "bloomberg.com": "美国",
    "apnews.com": "美国",
    "abcnews.go.com": "美国",
    "nbcnews.com": "美国",
    "foxnews.com": "美国",
    "thehill.com": "美国",
    "time.com": "美国",
    "forbes.com": "美国",
    "cnbc.com": "美国",
    "businessinsider.com": "美国",
    "theverge.com": "美国",
    "techcrunch.com": "美国",
    "wired.com": "美国",
    "usatoday.com": "美国",
    "politico.com": "美国",
    "theatlantic.com": "美国",
    "huffpost.com": "美国",
    "npr.org": "美国",
    "latimes.com": "美国",
    "wsj.com": "美国",
    "axios.com": "美国",
    "reuters.com": "英国",
    # 英国
    "bbc.com": "英国",
    "bbc.co.uk": "英国",
    "theguardian.com": "英国",
    "telegraph.co.uk": "英国",
    "independent.co.uk": "英国",
    "dailymail.co.uk": "英国",
    "ft.com": "英国",
    "sky.com": "英国",
    "skynews.com": "英国",
    "thetimes.co.uk": "英国",
    "economist.com": "英国",
    # 日本
    "nhk.or.jp": "日本",
    "kyodonews.net": "日本",
    "nikkei.com": "日本",
    "mainichi.jp": "日本",
    "asahi.com": "日本",
    "japantimes.co.jp": "日本",
    # 韩国
    "yonhapnewstv.co.kr": "韩国",
    "koreaherald.com": "韩国",
    "kbs.co.kr": "韩国",
    # 俄罗斯
    "tass.com": "俄罗斯",
    "tass.ru": "俄罗斯",
    "rt.com": "俄罗斯",
    "sputniknews.com": "俄罗斯",
    "ria.ru": "俄罗斯",
    # 法国
    "afp.com": "法国",
    "lemonde.fr": "法国",
    "france24.com": "法国",
    "lefigaro.fr": "法国",
    # 德国
    "dw.com": "德国",
    "spiegel.de": "德国",
    "bild.de": "德国",
    # 意大利
    "ansa.it": "意大利",
    "repubblica.it": "意大利",
    # 西班牙
    "efe.com": "西班牙",
    "elpais.com": "西班牙",
    # 爱尔兰
    "rte.ie": "爱尔兰",
    # 加拿大
    "cbc.ca": "加拿大",
    "theglobeandmail.com": "加拿大",
    "ctvnews.ca": "加拿大",
    # 澳大利亚
    "abc.net.au": "澳大利亚",
    "smh.com.au": "澳大利亚",
    "theage.com.au": "澳大利亚",
    # 印度
    "timesofindia.indiatimes.com": "印度",
    "hindustantimes.com": "印度",
    "ndtv.com": "印度",
    "thehindu.com": "印度",
    # 阿联酋
    "gulfnews.com": "阿联酋",
    "khaleejtimes.com": "阿联酋",
    "thenationalnews.com": "阿联酋",
    "aljazeera.com": "卡塔尔",
    # 沙特
    "arabnews.com": "沙特",
    "alarabiya.net": "沙特",
    # 新加坡
    "straitstimes.com": "新加坡",
    "channelnewsasia.com": "新加坡",
    # 马来西亚
    "thestar.com.my": "马来西亚",
    # 印度尼西亚
    "thejakartapost.com": "印度尼西亚",
    # 泰国
    "bangkokpost.com": "泰国",
    "nationthailand.com": "泰国",
    # 越南
    "vnexpress.net": "越南",
    # 菲律宾
    "rappler.com": "菲律宾",
    "inquirer.net": "菲律宾",
    # 以色列
    "haaretz.com": "以色列",
    "jpost.com": "以色列",
    "timesofisrael.com": "以色列",
    # 伊朗
    "tehrantimes.com": "伊朗",
    "presstv.ir": "伊朗",
    # 土耳其
    "aa.com.tr": "土耳其",
    "hurriyetdailynews.com": "土耳其",
    "dailysabah.com": "土耳其",
    # 巴西
    "folha.uol.com.br": "巴西",
    "oglobo.globo.com": "巴西",
    # 墨西哥
    "eluniversal.com.mx": "墨西哥",
    # 阿根廷
    "clarin.com": "阿根廷",
    "lanacion.com.ar": "阿根廷",
    # 瑞士
    "swissinfo.ch": "瑞士",
}

# 新闻源名称关键字 -> 国家（域名推断失败时的补充手段）
_SOURCE_KEYWORD_COUNTRY: List[Tuple[str, str]] = [
    ("xinhua", "中国"),
    ("新华", "中国"),
    ("china daily", "中国"),
    ("cgtn", "中国"),
    ("people's daily", "中国"),
    ("scmp", "中国"),
    ("associated press", "美国"),
    ("ap news", "美国"),
    ("cnn", "美国"),
    ("npr", "美国"),
    ("bloomberg", "美国"),
    ("the new york times", "美国"),
    ("usa today", "美国"),
    ("fox news", "美国"),
    ("reuters", "英国"),
    ("bbc", "英国"),
    ("the guardian", "英国"),
    ("al jazeera", "卡塔尔"),
    ("nhk", "日本"),
    ("kyodo", "日本"),
    ("yonhap", "韩国"),
    ("tass", "俄罗斯"),
    ("rt news", "俄罗斯"),
    ("afp", "法国"),
    ("dw", "德国"),
    ("deutsche welle", "德国"),
]


class NewsApiBudgetError(Exception):
    """
    输入:
    - `message`: 预算不足时的提示信息

    输出:
    - 预算异常对象

    作用:
    - 当日预算耗尽、或与上游限流冲突时抛出，由 API 层转成 429 响应。
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NewsApiError(Exception):
    """
    输入:
    - `message`: 错误描述
    - `code`: NewsAPI 返回的错误码（如 rateLimited / invalidKey）

    输出:
    - NewsAPI 上游错误对象

    作用:
    - 封装 NewsAPI 接口返回的非 200 错误，便于 API 层统一映射状态码。
    """

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.code = code or ""


def _parse_iso_time(value: str) -> Optional[datetime]:
    """
    输入:
    - `value`: ISO 8601 时间字符串（如 2026-08-26T05:42:36Z）

    输出:
    - 时区感知的 datetime；解析失败返回 None

    作用:
    - 统一解析 NewsAPI 文章时间，遇非法值不中断整体分析。
    """

    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _normalize_article(raw: Dict[str, Any], language: str = "") -> Optional[Dict[str, Any]]:
    """
    输入:
    - `raw`: NewsAPI 返回的单篇文章原始字典
    - `language`: 本次查询使用的语言代码（用于语区兜底）

    输出:
    - 规范化后的文章字典；标题或链接缺失时返回 None

    作用:
    - 抽取文章关键字段并补齐国家/语区归属，供前端和分析层统一使用。
    """

    title = (raw.get("title") or "").strip()
    url = (raw.get("url") or "").strip()
    if not title or not url:
        return None

    source = raw.get("source") or {}
    return {
        "title": title,
        "description": (raw.get("description") or "").strip(),
        "url": url,
        "image": raw.get("urlToImage") or "",
        "author": (raw.get("author") or "").strip(),
        "source_id": (source.get("id") or "").strip(),
        "source_name": (source.get("name") or "未知来源").strip(),
        "content": (raw.get("content") or "").strip()[:500],
        "published_at": raw.get("publishedAt") or "",
    }


def _host_country(host: str) -> Optional[str]:
    """
    输入:
    - `host`: 文章 URL 的主机名（小写）

    输出:
    - 命中映射表的国家名，未命中返回 None

    作用:
    - 通过常见媒体域名推断报道来源国家。
    """

    host = (host or "").lower()
    while host.startswith("www.") or host.startswith("m."):
        if host.startswith("www."):
            host = host[4:]
        elif host.startswith("m."):
            host = host[2:]
        else:
            break
    for domain, country in _DOMAIN_COUNTRY.items():
        if host == domain or host.endswith("." + domain):
            return country
    return None


def _source_name_country(source_name: str) -> Optional[str]:
    """
    输入:
    - `source_name`: NewsAPI 返回的来源名称

    输出:
    - 命中关键字映射的国家名，未命中返回 None

    作用:
    - 域名无法推断时，用来源名称关键字补充国家判断。
    """

    name = (source_name or "").lower()
    for keyword, country in _SOURCE_KEYWORD_COUNTRY:
        if keyword in name:
            return country
    return None


def infer_country(article: Dict[str, Any], language: str = "") -> str:
    """
    输入:
    - `article`: 规范化后的文章字典
    - `language`: 查询语言代码

    输出:
    - 文章归属国家或语区标签（如“中国”“英文媒体”）

    作用:
    - 依次尝试域名、来源名称推断国家，失败后按媒体语言归组，
      保证“全球新闻雷达”每个样本都有归属。
    """

    host = urlparse(article.get("url") or "").netloc
    country = _host_country(host) or _source_name_country(article.get("source_name") or "")
    if country:
        return country
    lang = (language or "").strip().lower()
    return LANGUAGE_REGION_LABELS.get(lang, "其他语区")


def build_trace_query(event_text: str, keywords: Optional[List[str]] = None) -> str:
    """
    输入:
    - `event_text`: 用户输入的事件/主题文本
    - `keywords`: 可选的新闻关键词/实体列表（来自本地数据库）

    输出:
    - NewsAPI q 参数查询串（短语加引号、用 OR 连接以扩大召回）

    作用:
    - 从事件标题或关键词构建“宽召回”查询，帮助获取该事件的广泛媒体报道。
    """

    tokens: List[str] = []
    if keywords:
        seen: set[str] = set()
        for kw in keywords:
            text = str(kw or "").strip()
            if not text or len(text) < 2:
                continue
            lower = text.lower()
            if lower in seen:
                continue
            seen.add(lower)
            tokens.append(text)
            if len(tokens) >= 4:
                break

    if not tokens:
        tokens = _derive_tokens_from_text(event_text)

    tokens = tokens[:4]
    if not tokens:
        cleaned = str(event_text or "").strip()[:60]
        if not cleaned:
            return ""
        return f'"{cleaned}"'
    return " OR ".join(f'"{token}"' for token in tokens)


def _derive_tokens_from_text(text: str) -> List[str]:
    """
    输入:
    - `text`: 事件文本（可为中文或英文）

    输出:
    - 从文本提取的查询词列表（最多 4 个）

    作用:
    - 无现成关键词时的兜底：中文按标点切分取核心片段，英文取核心实义词。
      片段过长时截断，避免整体短语过于苛刻导致召回为零。
    """

    cleaned = str(text or "").strip()
    if not cleaned:
        return []

    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in cleaned)
    if has_cjk:
        segments = [seg.strip() for seg in re.split(r"[，。；、,.!?！？;:：\s]+", cleaned) if seg.strip()]
        tokens: List[str] = []
        total = 0
        for seg in segments:
            token = seg if len(seg) <= 12 else seg[:12]
            if tokens and total + len(token) > 24:
                break
            tokens.append(token)
            total += len(token)
            if len(tokens) >= 4:
                break
        return tokens if tokens else [cleaned[:24]]

    stopwords = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "at", "from", "by", "is", "as", "it", "its", "that", "this", "has", "have", "was", "were", "be", "been"}
    words = [w for w in re.split(r"[^A-Za-z0-9\-']+", cleaned.lower()) if w]
    words = [w for w in words if w not in stopwords and len(w) >= 3]
    return words[:4] if words else [cleaned[:24]]


class NewsApiClient:
    """
    输入:
    - 无（配置从 `settings` 读取：NEWSAPI_API_KEY / CRAWLER_PROXY）

    输出:
    - NewsAPI 客户端实例，提供受限流的异步查询

    作用:
    - 以保守的节奏调用 NewsAPI，缓存结果并维护滚动日预算，
      保证额度有限的场景下稳定可用。
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._api_key: str = (self._settings.NEWSAPI_API_KEY or "").strip()
        self._proxy: Optional[str] = (self._settings.CRAWLER_PROXY or "").strip() or None
        self._lock = asyncio.Lock()
        self._last_request_at: float = 0.0
        self._request_times: Deque[float] = deque(maxlen=DAILY_REQUEST_BUDGET + 64)
        self._cache: TtlMemoryCache[Dict[str, Any]] = TtlMemoryCache(
            ttl_seconds=RESULT_CACHE_TTL_SECONDS,
            max_size=64,
        )

    @property
    def configured(self) -> bool:
        """
        输入:
        - 无

        输出:
        - NEWSAPI_API_KEY 是否已配置

        作用:
        - 供 API 层和前端判断功能是否可用。
        """

        return bool(self._api_key)

    @property
    def remaining_budget(self) -> int:
        """
        输入:
        - 无

        输出:
        - 滚动 24 小时内尚可发起的请求次数

        作用:
        - 前端展示剩余额度提示，避免用户频繁触发分析。
        """

        self._prune_budget()
        return max(0, DAILY_REQUEST_BUDGET - len(self._request_times))

    def _prune_budget(self) -> None:
        """
        输入:
        - 无

        输出:
        - 无

        作用:
        - 移除滚动 24 小时窗口之外的请求时间戳。
        """

        cutoff = time.monotonic() - 24 * 3600
        while self._request_times and self._request_times[0] < cutoff:
            self._request_times.popleft()

    async def _acquire_slot(self) -> None:
        """
        输入:
        - 无

        输出:
        - 无（在拿到许可后返回）

        作用:
        - 在进程内串行化 NewsAPI 请求：校验日预算并保证请求最小间隔。
        """

        async with self._lock:
            self._prune_budget()
            if len(self._request_times) >= DAILY_REQUEST_BUDGET:
                raise NewsApiBudgetError(
                    f"NewsAPI 当日请求预算（{DAILY_REQUEST_BUDGET} 次）已用尽，请明天再试或稍后再进行新的分析。"
                )
            wait = MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request_at = time.monotonic()
            self._request_times.append(self._last_request_at)

    def _cache_key(
        self,
        *,
        query: str,
        language: Optional[str],
        from_date: str,
        to_date: str,
        sort_by: str,
        page_size: int,
        page: int,
    ) -> tuple[str, str, str, str, str, int, int]:
        """
        输入:
        - 与新闻查询相关的全部入参

        输出:
        - 稳定的缓存键元组

        作用:
        - 相同查询在 TTL 内命中缓存，避免重复消耗 NewsAPI 额度。
        """

        return (
            query.strip().lower(),
            (language or "").lower(),
            from_date,
            to_date,
            sort_by,
            page_size,
            page,
        )

    async def fetch_everything(
        self,
        *,
        query: str,
        language: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        sort_by: str = "publishedAt",
        page_size: int = 100,
        page: int = 1,
        use_cache: bool = True,
    ) -> Dict[str, Any]:
        """
        输入:
        - `query`: NewsAPI q 查询串
        - `language`: 媒体语言代码（zh/en/...，None 表示不限）
        - `from_date`/`to_date`: ISO 日期范围（YYYY-MM-DD）
        - `sort_by`: 排序方式（publishedAt/relevancy/popularity）
        - `page_size`: 每页条数（最大 100）
        - `page`: 页码（最大 100）
        - `use_cache`: 是否使用短缓存

        输出:
        - NewsAPI everything 端点响应字典（含 articles 列表与元信息）

        作用:
        - 以受限流的方式查询全球新闻文章列表，并返回标准化的响应结构。
        """

        if not self._api_key:
            raise NewsApiError("NewsAPI 未配置，请在 .env 中设置 NEWSAPI_API_KEY", code="notConfigured")

        page_size = max(1, min(int(page_size or 100), 100))
        page = max(1, min(int(page or 1), 100))
        from_date = from_date or ""
        to_date = to_date or ""
        cache_key = self._cache_key(
            query=query,
            language=language,
            from_date=from_date,
            to_date=to_date,
            sort_by=sort_by,
            page_size=page_size,
            page=page,
        )
        if use_cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                cached["from_cache"] = True
                return cached

        await self._acquire_slot()

        params: Dict[str, str] = {
            "q": query,
            "sortBy": sort_by,
            "pageSize": str(page_size),
            "page": str(page),
            "apiKey": self._api_key,
        }
        if language:
            params["language"] = language
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        url = f"{NEWSAPI_BASE_URL}/everything?{urlencode(params)}"
        request_start = time.monotonic()
        data: Dict[str, Any] = {}
        try:
            timeout = aiohttp.ClientTimeout(total=45)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, proxy=self._proxy) as resp:
                    try:
                        payload_raw = await resp.json(content_type=None)
                    except (ValueError, TypeError) as exc:
                        raise NewsApiError(
                            f"NewsAPI 返回了无法解析的响应（HTTP {resp.status}）", code="badResponse"
                        ) from exc
                    data = payload_raw if isinstance(payload_raw, dict) else {}

                    if resp.status != 200 or data.get("status") == "error":
                        code = data.get("code") or ""
                        message = data.get("message") or f"NewsAPI HTTP {resp.status}"
                        if resp.status == 429 or code in {"rateLimited", "maximumResultsReached"}:
                            raise NewsApiBudgetError(f"NewsAPI 上游限流：{message}")
                        raise NewsApiError(message, code=code)
        except (NewsApiBudgetError, NewsApiError):
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise NewsApiError(f"无法连接 NewsAPI（{exc.__class__.__name__}），请检查网络或代理配置", code="network") from exc

        elapsed_ms = round((time.monotonic() - request_start) * 1000, 1)
        articles_raw = data.get("articles") or []
        articles: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()
        for raw in articles_raw:
            article = _normalize_article(raw, language=language or "")
            if not article:
                continue
            url_key = article["url"].split("#")[0].rstrip("/")
            if url_key in seen_urls:
                continue
            seen_urls.add(url_key)
            article["country"] = infer_country(article, language or "")
            published = _parse_iso_time(article.get("published_at") or "")
            article["published_dt"] = published.isoformat() if published else ""
            articles.append(article)

        payload: Dict[str, Any] = {
            "status": "ok",
            "total_results": int(data.get("totalResults") or len(articles)),
            "returned_count": len(articles),
            "page": page,
            "page_size": page_size,
            "query": query,
            "language": language or "",
            "elapsed_ms": elapsed_ms,
            "articles": articles,
            "from_cache": False,
        }
        self._cache.set(cache_key, payload)
        return payload


# 全局单例
newsapi_client = NewsApiClient()