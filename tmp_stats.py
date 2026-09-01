import os
os.environ["DEBUG"] = "true"
import asyncio
from datetime import datetime, timedelta
from sqlalchemy import select, func
from app.core.database import AsyncSessionLocal
from app.models.news import News

async def main():
    now = datetime.now()
    async with AsyncSessionLocal() as db:
        total = (await db.execute(select(func.count()).select_from(News))).scalar()
        with_img = (await db.execute(select(func.count()).select_from(News).where(News.images.isnot(None)).where(News.images != []))).scalar()
        print("total:", total, "with_images:", with_img)
        for hours in (24, 48, 72):
            since = now - timedelta(hours=hours)
            cnt = (await db.execute(select(func.count()).select_from(News).where(News.publish_date >= since))).scalar()
            wimg = (await db.execute(select(func.count()).select_from(News).where(News.publish_date >= since).where(News.images.isnot(None)).where(News.images != []))).scalar()
            print(f"last {hours}h: total={cnt} with_images={wimg}")
        # 按来源统计最近72h有图比例
        since = now - timedelta(hours=72)
        rows = (await db.execute(
            select(News.source, func.count(), func.sum(func.length(func.coalesce(func.cast(News.images, __import__('sqlalchemy').String), ''))))
            .where(News.publish_date >= since)
            .group_by(News.source)
            .order_by(func.count().desc())
        )).all()
        # 简单点：按来源统计条数和有图数
        from sqlalchemy import case
        rows2 = (await db.execute(
            select(News.source, func.count(), func.sum(case((News.images.isnot(None), 1), else_=0)))
            .where(News.publish_date >= since)
            .group_by(News.source)
            .order_by(func.count().desc())
        )).all()
        print("--- source stats (last 72h) ---")
        for r in rows2:
            print(r[0], "total=", r[1], "with_img=", r[2])

asyncio.run(main())
