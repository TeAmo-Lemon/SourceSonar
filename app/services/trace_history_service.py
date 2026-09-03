"""
本文件用于管理事件溯源历史：保存分析快照、查询单条记录并生成历史列表。
主要对象:
- `TraceHistoryService`: 溯源历史业务服务
- `trace_history_service`: 全局服务实例
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.trace_record import TraceRecord


class TraceHistoryService:
    """
    输入:
    - 数据库会话、溯源参数与分析结果

    输出:
    - 可供 API 使用的持久化记录与序列化结果

    作用:
    - 将溯源历史的数据库操作从分析服务和接口层中分离。
    """

    async def save_result(
        self,
        db: AsyncSession,
        *,
        event: str,
        news_id: Optional[int],
        language: str,
        days: int,
        result: Dict[str, Any],
    ) -> TraceRecord:
        """
        输入:
        - `db`: 数据库会话
        - `event`: 用户输入或新闻标题
        - `news_id`: 可选本地新闻 ID
        - `language`: 媒体语言
        - `days`: 分析窗口天数
        - `result`: 完整溯源分析结果

        输出:
        - 已写入会话并取得主键的溯源记录

        作用:
        - 保存不可变的分析快照；事务提交由接口层统一负责。
        """

        label = str(result.get("query_label") or event or result.get("query") or "未命名事件").strip()
        record = TraceRecord(
            event=label[:200],
            news_id=news_id,
            language=str(language or "zh")[:8],
            days=max(1, min(int(days or 14), 30)),
            query=str(result.get("query") or ""),
            status=str(result.get("status") or "ok")[:20],
            result=deepcopy(result),
        )
        db.add(record)
        await db.flush()
        return record

    async def get_record(self, db: AsyncSession, record_id: int) -> Optional[TraceRecord]:
        """
        输入:
        - `db`: 数据库会话
        - `record_id`: 溯源记录 ID

        输出:
        - 匹配的溯源记录；不存在时返回 None

        作用:
        - 为刷新恢复和历史记录点击提供完整快照。
        """

        return await db.get(TraceRecord, record_id)

    async def list_records(self, db: AsyncSession, *, limit: int = 20) -> List[TraceRecord]:
        """
        输入:
        - `db`: 数据库会话
        - `limit`: 最大返回数量

        输出:
        - 按创建时间倒序排列的溯源记录

        作用:
        - 为页面展示最近历史提供轻量列表，不返回完整文章快照。
        """

        safe_limit = max(1, min(int(limit or 20), 100))
        result = await db.execute(
            select(TraceRecord).order_by(desc(TraceRecord.created_at), desc(TraceRecord.id)).limit(safe_limit)
        )
        return list(result.scalars().all())

    def serialize_summary(self, record: TraceRecord) -> Dict[str, Any]:
        """
        输入:
        - `record`: 溯源历史 ORM 对象

        输出:
        - 不含完整结果的历史摘要

        作用:
        - 控制历史列表响应体大小，同时展示事件、范围和结果规模。
        """

        snapshot = record.result if isinstance(record.result, dict) else {}
        overview = snapshot.get("overview") if isinstance(snapshot.get("overview"), dict) else {}
        return {
            "id": record.id,
            "event": record.event,
            "news_id": record.news_id,
            "language": record.language,
            "days": record.days,
            "status": record.status,
            "article_count": int(overview.get("total") or 0),
            "created_at": record.created_at.isoformat() if record.created_at else None,
        }

    def serialize_result(self, record: TraceRecord) -> Dict[str, Any]:
        """
        输入:
        - `record`: 溯源历史 ORM 对象

        输出:
        - 完整分析快照及其记录元信息

        作用:
        - 让前端用与实时分析相同的数据结构恢复全部图表和列表。
        """

        snapshot = deepcopy(record.result) if isinstance(record.result, dict) else {}
        snapshot["record"] = self.serialize_summary(record)
        return snapshot


trace_history_service = TraceHistoryService()
