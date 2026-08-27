"""
本文件用于提供“事件溯源”相关 API：配置状态查询与溯源分析。
主要函数:
- `get_trace_status`: 查询 NewsAPI 配置与剩余额度
- `trace_analyze`: 对事件/主题执行全球新闻雷达与传播轨迹分析
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.services.newsapi_service import DAILY_REQUEST_BUDGET, NewsApiBudgetError, NewsApiError, newsapi_client
from app.services.trace_service import trace_service

router = APIRouter(prefix="/api/trace", tags=["trace"])

# 允许的媒体语言取值（all 表示不限语言）
ALLOWED_LANGUAGES = {"zh", "en", "all"}


class TraceAnalyzeRequest(BaseModel):
    """
    输入:
    - `event`: 事件/主题文本
    - `news_id`: 本地热点新闻 ID（可选）
    - `language`: 媒体语言（zh/en/all）
    - `days`: 分析窗口天数（1-30）

    输出:
    - 溯源分析请求体

    作用:
    - 校验并承载前端提交的溯源分析参数。
    """

    event: str = Field(default="", max_length=200)
    news_id: Optional[int] = Field(default=None, ge=1)
    language: str = Field(default="zh", max_length=8)
    days: int = Field(default=14, ge=1, le=30)


@router.get("/status")
async def get_trace_status():
    """
    输入:
    - 无

    输出:
    - NewsAPI 配置状态与剩余请求预算

    作用:
    - 供页面初始化时判断“溯源”功能是否可用，并提示剩余额度。
    """

    settings = get_settings()
    return {
        "configured": newsapi_client.configured,
        "api_key_source": "env" if newsapi_client.configured else "missing",
        "daily_budget": DAILY_REQUEST_BUDGET,
        "remaining_budget": newsapi_client.remaining_budget,
        "proxy_configured": bool((settings.CRAWLER_PROXY or "").strip()),
    }


@router.post("/analyze")
async def trace_analyze(payload: TraceAnalyzeRequest, db: AsyncSession = Depends(get_db)):
    """
    输入:
    - `payload`: 溯源分析请求体（事件文本或热点新闻 ID）
    - `db`: 数据库会话（依赖注入，用于读取热点新闻的关键词）

    输出:
    - 溯源分析结果（概览、雷达、轨迹、里程碑、文章列表）

    作用:
    - 通过 NewsAPI 检索事件在全球媒体的报道，并完成传播轨迹聚合。
    """

    if not newsapi_client.configured:
        raise HTTPException(status_code=400, detail="NewsAPI 未配置：请在 .env 中设置 NEWSAPI_API_KEY 后重启应用")

    event_text = (payload.event or "").strip()
    if not event_text and payload.news_id is None:
        raise HTTPException(status_code=400, detail="请提供事件文本或选择一条热点新闻")

    language = (payload.language or "zh").strip().lower()
    if language not in ALLOWED_LANGUAGES:
        raise HTTPException(status_code=400, detail=f"language 取值仅支持 {sorted(ALLOWED_LANGUAGES)}")

    try:
        result = await trace_service.analyze_event(
            event=event_text,
            news_id=payload.news_id,
            language=language,
            days=payload.days,
            db=db,
        )
    except NewsApiBudgetError as exc:
        raise HTTPException(status_code=429, detail=exc.message) from exc
    except NewsApiError as exc:
        raise HTTPException(status_code=502, detail=exc.message) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if result.get("meta", {}).get("error"):
        error = result["meta"]["error"]
        # 本地/上游额度耗尽时返回 429，其余 NewsAPI 错误返回 502
        status_code = 429 if result["meta"].get("error_kind") == "budget" else 502
        raise HTTPException(status_code=status_code, detail=error)

    return result