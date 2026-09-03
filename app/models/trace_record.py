"""
本文件用于定义事件溯源历史记录模型，持久化每次溯源的参数与完整分析快照。
主要类:
- `TraceRecord`: 数据库 `trace_records` 表的 ORM 映射
"""

from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Integer, String, Text

from app.core.database import Base


class TraceRecord(Base):
    """
    输入:
    - 事件文本、本地新闻 ID、语言、时间窗口与完整分析结果

    输出:
    - 可跨页面刷新恢复的数据库溯源记录

    作用:
    - 保存每次溯源分析快照，避免结果只存在于浏览器内存中。
    """

    __tablename__ = "trace_records"

    id = Column(Integer, primary_key=True, index=True)
    event = Column(String(200), nullable=False, index=True)
    news_id = Column(Integer, nullable=True, index=True)
    language = Column(String(8), nullable=False, default="zh")
    days = Column(Integer, nullable=False, default=14)
    query = Column(Text, nullable=False, default="")
    status = Column(String(20), nullable=False, default="ok", index=True)
    result = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.now, nullable=False, index=True)
