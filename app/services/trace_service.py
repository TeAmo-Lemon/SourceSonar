"""
本文件用于实现“事件溯源”分析服务：基于 NewsAPI 检索到的全球报道，
生成“全球新闻雷达 + 事件传播轨迹”所需的结构化数据（不调用大模型，纯规则聚合）。
主要对象:
- `TraceService`: 溯源分析服务
- `trace_service`: 全局单例实例
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import logger
from app.models.news import News
from app.services.newsapi_service import (
    MAX_REQUESTS_PER_ANALYSIS,
    NewsApiBudgetError,
    NewsApiError,
    build_trace_query,
    newsapi_client,
)

# 默认分析窗口（天），NewsAPI 仅索引最近约一个月，这里同样限制
DEFAULT_TRACE_DAYS = 14
MAX_TRACE_DAYS = 30

# 雷达图最多展示的国家/语区数
RADAR_TOP_N = 8
# 雷达图按媒体展示时最多展示的媒体数
RADAR_MEDIA_TOP_N = 8
# 来源分布条形图最多展示数
SOURCE_TOP_N = 12
# 传播轨迹按媒体展示时最多展示的行数（媒体过多时按首发时间截断）
SPREAD_MEDIA_TOP_N = 14


def _parse_dt(value: Any) -> Optional[datetime]:
    """
    输入:
    - `value`: 论文时间字符串或 datetime

    输出:
    - 时区感知的 datetime；无法解析返回 None

    作用:
    - 统一解析并归一化到 UTC，保证轨迹时间轴比较口径一致。
    """

    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value or "")
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _day_key(dt: datetime) -> str:
    """
    输入:
    - `dt`: datetime

    输出:
    - UTC 日期字符串（YYYY-MM-DD）

    作用:
    - 统一按天聚合报道量的日期口径。
    """

    return dt.strftime("%Y-%m-%d")


class TraceService:
    """
    输入:
    - 事件文本/本地新闻 ID、语言、分析窗口等参数

    输出:
    - 溯源分析数据结构（概览、雷达、轨迹、里程碑、文章列表）

    作用:
    - 编排 NewsAPI 检索与本地数据的分析聚合，输出前端可直接渲染的数据。
    """

    async def analyze_event(
        self,
        *,
        event: str = "",
        news_id: Optional[int] = None,
        language: str = "zh",
        days: int = DEFAULT_TRACE_DAYS,
        db: Optional[AsyncSession] = None,
    ) -> Dict[str, Any]:
        """
        输入:
        - `event`: 用户输入的事件/主题文本
        - `news_id`: 本地新闻 ID（可选；传入时优先用其关键词构建查询）
        - `language`: 媒体语言（zh/en/all）
        - `days`: 分析窗口天数（1-30）
        - `db`: 数据库会话（仅 news_id 需要）

        输出:
        - 溯源分析结果字典：
          - overview: 概览指标
          - radar: 全球新闻雷达（国家/语区 -> 报道量）
          - source_dist: 来源分布
          - timeline: 每日报道量与累计曲线
          - spread: 各国家按时间的传播轨迹散点数据
          - milestones: 传播里程碑列表
          - narrative: 规则生成的传播节奏描述
          - articles: 报道列表（按时间倒序）
          - meta: 查询与额度元信息

        作用:
        - 检索该事件在全球媒体的报道并完成传播分析聚合。
        """

        cleaned_event = str(event or "").strip()
        keywords: List[str] = []
        if not cleaned_event and not news_id:
            raise ValueError("请提供事件文本或选择一条热点新闻")

        # 有本地新闻 ID 时，优先使用其关键词/实体，能显著提升检索精度
        if news_id and db is not None:
            news = await db.get(News, news_id)
            if news is not None:
                if not cleaned_event:
                    cleaned_event = news.title or ""
                keywords = [str(k) for k in ((news.keywords or []) + (news.entities or []))][:8]

        lang_code = None if language in {"", "all"} else language
        days = max(1, min(int(days or DEFAULT_TRACE_DAYS), MAX_TRACE_DAYS))

        query = build_trace_query(cleaned_event, keywords)
        if not query:
            raise ValueError("无法从事件文本中提取有效关键词")

        # NewsAPI 仅保留近一个月数据，这里按窗口截断
        to_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        from_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

        # 先尝试主查询（按发布时间排序，默认只取第一页 100 条）
        payload: Dict[str, Any] = {
            "status": "error",
            "query": query,
            "query_label": cleaned_event[:60],
            "meta": {
                "language": language,
                "days": days,
                "from_date": from_date,
                "to_date": to_date,
                "news_id": news_id,
                "api_configured": newsapi_client.configured,
                "remaining_budget": newsapi_client.remaining_budget,
            },
            "overview": {},
            "radar": {"indicators": [], "values": []},
            "radar_media": {"indicators": [], "values": []},
            "source_dist": [],
            "timeline": {"dates": [], "counts": [], "cumulative": []},
            "spread": {"countries": [], "series": []},
            "spread_media": {"sources": [], "series": []},
            "milestones": [],
            "narrative": [],
            "articles": [],
        }

        try:
            result = await newsapi_client.fetch_everything(
                query=query,
                language=lang_code,
                from_date=from_date,
                to_date=to_date,
                sort_by="publishedAt",
                page_size=100,
                page=1,
            )
        except (NewsApiBudgetError, NewsApiError) as exc:
            payload["meta"]["error"] = getattr(exc, "message", str(exc))
            payload["meta"]["error_kind"] = "budget" if isinstance(exc, NewsApiBudgetError) else "api"
            logger.warning(f"溯源分析失败: {getattr(exc, 'message', exc)}")
            return payload

        articles = result.get("articles") or []
        payload["meta"].update(
            {
                "total_results": result.get("total_results") or 0,
                "returned_count": len(articles),
                "request_count": 1,
                "from_cache": bool(result.get("from_cache")),
                "elapsed_ms_api": result.get("elapsed_ms"),
                "remaining_budget": newsapi_client.remaining_budget,
            }
        )

        # 优先按发布时间倒序保证“最新动态”展示；轨迹聚合统一在内部按时间升序处理
        articles.sort(key=lambda a: _parse_dt(a.get("published_dt") or a.get("published_at")) or datetime.min)

        if not articles:
            payload["overview"] = {"total": 0}
            payload["meta"]["reason"] = "empty"
            return payload

        payload["status"] = "ok"
        payload["meta"]["reason"] = "ok"
        payload["articles"] = self._serialize_articles(articles)
        payload["overview"] = self._build_overview(articles)
        payload["radar"] = self._build_radar(articles)
        payload["radar_media"] = self._build_radar_by_media(articles)
        payload["source_dist"] = self._build_source_dist(articles)
        payload["timeline"] = self._build_timeline(articles)
        payload["spread"] = self._build_spread(articles)
        payload["spread_media"] = self._build_spread_by_media(articles)
        payload["milestones"] = self._build_milestones(articles)
        payload["narrative"] = self._build_narrative(payload)
        return payload

    def _serialize_articles(self, articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        输入:
        - `articles`: 按时间升序排列的文章列表

        输出:
        - 面向前端展示的文章字典列表（按时间倒序，最新在前）

        作用:
        - 精简字段并统一时间格式，避免把所有原始字段交给前端。
        """

        items = []
        for article in reversed(articles):
            published = _parse_dt(article.get("published_dt") or article.get("published_at"))
            items.append(
                {
                    "title": article.get("title") or "",
                    "url": article.get("url") or "",
                    "description": article.get("description") or "",
                    "source_name": article.get("source_name") or "未知来源",
                    "country": article.get("country") or "其他",
                    "published_at": published.isoformat() if published else "",
                }
            )
        return items

    def _build_overview(self, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        输入:
        - `articles`: 按时间升序排列的文章列表

        输出:
        - 概览指标字典

        作用:
        - 汇总报道总数、覆盖国家数、来源数、时间跨度与首发信息。
        """

        countries = {a.get("country") or "其他" for a in articles}
        sources = {a.get("source_name") or "" for a in articles if a.get("source_name")}
        times = [_parse_dt(a.get("published_dt") or a.get("published_at")) for a in articles]
        times = [t for t in times if t is not None]

        first_dt = min(times) if times else None
        last_dt = max(times) if times else None
        span_hours = round((last_dt - first_dt).total_seconds() / 3600, 1) if first_dt and last_dt else 0.0

        first_articles = [a for a in articles if (_parse_dt(a.get("published_dt")) or datetime.max) == (first_dt or datetime.min)]
        first_article = first_articles[0] if first_articles else articles[0]

        return {
            "total": len(articles),
            "countries_count": len(countries),
            "countries": sorted(countries),
            "sources_count": len(sources),
            "span_hours": span_hours,
            "first_at": first_dt.isoformat() if first_dt else "",
            "last_at": last_dt.isoformat() if last_dt else "",
            "first_article": {
                "title": first_article.get("title") or "",
                "url": first_article.get("url") or "",
                "source_name": first_article.get("source_name") or "",
                "country": first_article.get("country") or "",
            },
            "request_count": len(set(a.get("url") or "" for a in articles)),
        }

    def _build_radar(self, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        输入:
        - `articles`: 文章列表

        输出:
        - 全球新闻雷达图数据（indicator + values）

        作用:
        - 按国家/语区聚合报道量，取 Top N 生成雷达图指标。
        """

        counter: Counter[str] = Counter()
        for article in articles:
            counter[(article.get("country") or "其他")] += 1

        top = counter.most_common(RADAR_TOP_N)
        indicators = [{"name": name, "max": max(1, max(counter.values()))} for name, _ in top]
        values = [{"name": name, "value": int(count)} for name, count in top]
        return {"indicators": indicators, "values": values}

    def _build_radar_by_media(self, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        输入:
        - `articles`: 文章列表

        输出:
        - 按媒体聚合的雷达图数据（indicator = 媒体来源）

        作用:
        - 生成“全球媒体雷达”：以媒体报道量为口径，展示事件的主要发声媒体。
        """

        counter: Counter[str] = Counter()
        for article in articles:
            counter[(article.get("source_name") or "未知来源")] += 1

        top = counter.most_common(RADAR_MEDIA_TOP_N)
        max_value = max(1, max(counter.values()))
        indicators = [{"name": name, "max": max_value} for name, _ in top]
        values = [{"name": name, "value": int(count)} for name, count in top]
        return {"indicators": indicators, "values": values, "max": max_value}

    def _build_source_dist(self, articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        输入:
        - `articles`: 文章列表

        输出:
        - 来源分布条形图数据

        作用:
        - 统计报道来源 Top N，展示事件的主要发声媒体。
        """

        counter: Counter[str] = Counter()
        for article in articles:
            name = article.get("source_name") or "未知来源"
            counter[name] += 1
        return [{"name": name, "value": int(count)} for name, count in counter.most_common(SOURCE_TOP_N)]

    def _build_timeline(self, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        输入:
        - `articles`: 按时间升序排列的文章列表

        输出:
        - 每日报道量与累计报道量（用于传播趋势图）

        作用:
        - 按 UTC 天聚合报道条数与累计值。
        """

        daily: Counter[str] = Counter()
        for article in articles:
            dt = _parse_dt(article.get("published_dt") or article.get("published_at"))
            daily[_day_key(dt)] += 1

        dates = sorted(daily.keys())
        counts = [daily[d] for d in dates]
        cumulative: List[int] = []
        running = 0
        for count in counts:
            running += count
            cumulative.append(running)
        return {"dates": dates, "counts": counts, "cumulative": cumulative}

    def _build_spread(self, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        输入:
        - `articles`: 按时间升序排列的文章列表

        输出:
        - 事件传播轨迹散点数据（按国家/语区）：
          - `countries`: 国家/语区列表（按首次报道时间排序，首发在最下方便于阅读扩散方向）
          - `series`: 每个国家的时间-报道量散点序列

        作用:
        - 用“纵轴=国家/语区、横轴=时间”的散点图呈现事件从首发国向全球扩散的波次。
        """

        country_first: Dict[str, datetime] = {}
        points: Dict[str, Counter[int]] = defaultdict(Counter)
        for article in articles:
            country = article.get("country") or "其他"
            dt = _parse_dt(article.get("published_dt") or article.get("published_at"))
            if dt is None:
                continue
            bucket_ms = int(dt.timestamp() * 1000)
            if country not in country_first or dt < country_first[country]:
                country_first[country] = dt
            points[country][bucket_ms] += 1

        # 按首次报道时间排序：首发国排最下方，后报道的依次向上，符合“由下而上扩散”的阅读习惯
        ordered = sorted(country_first.keys(), key=lambda c: country_first[c])
        display = list(reversed(ordered))
        index_map = {country: index for index, country in enumerate(display)}
        series = []
        for country in display:
            data = [
                {"value": [ts_ms, index_map[country], count]}
                for ts_ms, count in sorted(points[country].items())
            ]
            series.append({"name": country, "data": data})

        return {
            "countries": display,
            "series": series,
            "first_at": country_first[ordered[0]].isoformat() if ordered else "",
        }

    def _build_spread_by_media(self, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        输入:
        - `articles`: 按时间升序排列的文章列表

        输出:
        - 事件传播轨迹散点数据（按媒体）：
          - `sources`: 媒体列表（按首次报道时间排序，首发在最下方）
          - `series`: 每个媒体的逐篇报道散点序列，携带国家与标题便于悬停查看

        作用:
        - 用“纵轴=媒体、横轴=时间”的散点图呈现事件在媒体间的首发与跟进顺序，
          比按国家聚合更能看出“哪家媒体先报、谁在跟进”。
        """

        source_first: Dict[str, datetime] = {}
        articles_by_source: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for article in articles:
            source = article.get("source_name") or "未知来源"
            dt = _parse_dt(article.get("published_dt") or article.get("published_at"))
            if dt is None:
                continue
            if source not in source_first or dt < source_first[source]:
                source_first[source] = dt
            articles_by_source[source].append(article)

        # 首发媒体排最下方，后报道的依次向上；
        # 媒体过多时优先保留“最早响应”的前 N 家（传播轨迹关心谁先跟进，而非谁最晚）
        ordered = sorted(source_first.keys(), key=lambda s: source_first[s])
        display = list(reversed(ordered[:SPREAD_MEDIA_TOP_N]))
        index_map = {source: index for index, source in enumerate(display)}
        series = []
        for source in display:
            data = []
            for article in sorted(articles_by_source[source], key=lambda a: _parse_dt(a.get("published_dt")) or datetime.min):
                dt = _parse_dt(article.get("published_dt") or article.get("published_at"))
                if dt is None:
                    continue
                title = (article.get("title") or "").strip()
                if len(title) > 80:
                    title = title[:80] + "…"
                data.append(
                    {
                        "value": [
                            int(dt.timestamp() * 1000),
                            index_map[source],
                            1,
                            article.get("country") or "其他",
                            title,
                        ]
                    }
                )
            if data:
                series.append({"name": source, "data": data})

        return {
            "sources": display,
            "series": series,
            "first_at": source_first[ordered[0]].isoformat() if ordered else "",
        }

    def _build_milestones(self, articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        输入:
        - `articles`: 按时间升序排列的文章列表

        输出:
        - 传播里程碑列表（时间、来源、国家、标题等）

        作用:
        - 抽取事件传播的关键节点：首发、24 小时扩散、峰值日、覆盖广度。
        """

        times = [_parse_dt(a.get("published_dt") or a.get("published_at")) for a in articles]
        times = [t for t in times if t is not None]
        first_dt = min(times) if times else None
        last_dt = max(times) if times else None
        if not first_dt or not last_dt:
            return []

        milestones: List[Dict[str, Any]] = []

        first_article = articles[0]
        # 首发报道
        milestones.append(
            {
                "kind": "first",
                "label": "首发报道",
                "time": first_dt.isoformat(),
                "text": f"{first_article.get('source_name') or '未知来源'}（{first_article.get('country') or '未知'}）首次报道该事件",
                "title": first_article.get("title") or "",
                "url": first_article.get("url") or "",
            }
        )

        # 第一个 24 小时内的扩散情况
        hours_24 = first_dt + timedelta(hours=24)
        first_day_articles = [a for a in articles if (_parse_dt(a.get("published_dt")) or datetime.min) <= hours_24]
        countries_24h = {a.get("country") or "其他" for a in first_day_articles}
        total_countries = {a.get("country") or "其他" for a in articles}
        milestones.append(
            {
                "kind": "spread24",
                "label": "24 小时扩散",
                "time": hours_24.isoformat(),
                "text": (
                    f"首发后 24 小时内新增覆盖 {len(countries_24h)} 个媒体区"
                    f"（共覆盖 {len(total_countries)} 个），报道 {len(first_day_articles)} 篇"
                    f"（占全部 {len(articles)} 篇的 {round(len(first_day_articles) * 100 / max(1, len(articles)), 1)}%）"
                ),
            }
        )

        # 峰值日
        daily: Counter[str] = Counter()
        for article in articles:
            dt = _parse_dt(article.get("published_dt") or article.get("published_at"))
            daily[_day_key(dt)] += 1
        peak_day, peak_count = daily.most_common(1)[0] if daily else ("", 0)
        milestones.append(
            {
                "kind": "peak",
                "label": "传播峰值",
                "time": f"{peak_day}T00:00:00+00:00" if peak_day else "",
                "text": (
                    f"{peak_day} 单日报道量最大（{peak_count} 篇，"
                    f"占全部报道的 {round(peak_count * 100 / max(1, len(articles)), 1)}%）"
                    if peak_day
                    else "报道量未形成明显峰值"
                ),
            }
        )

        # 扩散周期
        span_hours = (last_dt - first_dt).total_seconds() / 3600
        if span_hours < 24:
            span_text = f"{round(span_hours, 1)} 小时"
        elif span_hours < 24 * 7:
            span_text = f"{round(span_hours / 24, 1)} 天"
        else:
            span_text = f"{round(span_hours / 24 / 7, 1)} 周"
        milestones.append(
            {
                "kind": "span",
                "label": "传播周期",
                "time": last_dt.isoformat(),
                "text": f"从首发到最后一条报道持续 {span_text}（{first_dt.strftime('%m-%d %H:%M')} → {last_dt.strftime('%m-%d %H:%M')} UTC）",
            }
        )

        return milestones

    def _build_narrative(self, payload: Dict[str, Any]) -> List[str]:
        """
        输入:
        - `payload`: 已完成聚合的溯源分析结果

        输出:
        - 规则的传播节奏描述语句列表

        作用:
        - 用可见的指标拼接成易读摘要，不消耗大模型额度。
        """

        overview = payload.get("overview") or {}
        radar = payload.get("radar") or {}
        spread = payload.get("spread") or {}
        if not overview.get("total"):
            return []

        narrative: List[str] = []
        first = overview.get("first_article") or {}
        narrative.append(
            f"事件由 {first.get('source_name') or '未知媒体'}（{first.get('country') or '未知'}）首发，"
            f"检索窗口内共覆盖 {overview.get('countries_count', 0)} 个媒体区、"
            f"{overview.get('sources_count', 0)} 家来源，合计 {overview.get('total', 0)} 篇相关报道。"
        )

        top_country = radar.get("values", [{}])[0].get("name") if radar.get("values") else ""
        if top_country:
            narrative.append(f"报道量最大的媒体区为「{top_country}」，是该事件在全球传播的主要阵地。")

        milestones = payload.get("milestones") or []
        peak = next((m for m in milestones if m.get("kind") == "peak"), None)
        spread24 = next((m for m in milestones if m.get("kind") == "spread24"), None)
        if spread24:
            narrative.append((spread24.get("text") or "") + "，说明事件在发酵初期扩散较快。")
        if peak and peak.get("text") and "未形成" not in peak.get("text", ""):
            narrative.append(peak.get("text") + "，之后关注度逐步回落。")

        if spread.get("countries"):
            top3 = "、".join(spread["countries"][:3])
            tail = spread["countries"][3:]
            if tail:
                top3 += f" 等 {len(spread['countries'])} 个媒体区"
            narrative.append(f"按首次报道时间排序，传播路径大致为：{top3}。")
        return narrative


# 全局单例
trace_service = TraceService()