"""
AI 用量统计 API 路由

端点列表:
  GET /api/usage/summary - 用量汇总（今日/近7天/近30天费用 + 累计 token 数）

说明:
  - 数据来源: ai_usage_logs 表（由 app/services/ai_service.py 在每次调用后写入）
  - today 按当天 0 点起（UTC），week 最近 7 天，month 最近 30 天
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_user
from app.models.ai_usage_log import AIUsageLog

router = APIRouter(dependencies=[Depends(require_user)])


async def _sum_cost_since(db: AsyncSession, since: datetime) -> float:
    """统计 since 之后的费用合计（无记录返回 0.0）"""
    result = await db.execute(
        select(func.coalesce(func.sum(AIUsageLog.cost_usd), 0.0)).where(
            AIUsageLog.created_at >= since
        )
    )
    return float(result.scalar_one())


@router.get("/summary")
async def usage_summary(db: AsyncSession = Depends(get_db)):
    """
    用量汇总。

    Returns:
        today_cost_usd: 当天 0 点（UTC）起的费用
        week_cost_usd: 最近 7 天费用
        month_cost_usd: 最近 30 天费用
        total_tokens: 全部记录的 input + output token 总和
    """
    # 用 naive UTC 与 server_default(now()) 写入的时间戳保持一致（SQLite 兼容）
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    today_cost = await _sum_cost_since(db, today_start)
    week_cost = await _sum_cost_since(db, now - timedelta(days=7))
    month_cost = await _sum_cost_since(db, now - timedelta(days=30))

    tokens_result = await db.execute(
        select(
            func.coalesce(func.sum(AIUsageLog.input_tokens), 0)
            + func.coalesce(func.sum(AIUsageLog.output_tokens), 0)
        )
    )
    total_tokens = int(tokens_result.scalar_one())

    return {
        "today_cost_usd": today_cost,
        "week_cost_usd": week_cost,
        "month_cost_usd": month_cost,
        "total_tokens": total_tokens,
    }
