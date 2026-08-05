"""Shared API authorization dependencies."""

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_agent_actor
from app.models import Novel, User


async def require_owned_novel(
    novel_id: int,
    db: AsyncSession = Depends(get_db),
    actor: User | object = Depends(require_agent_actor),
) -> Novel:
    result = await db.execute(select(Novel).where(Novel.id == novel_id))
    novel = result.scalar_one_or_none()
    owner_id = getattr(actor, "id", None)
    is_superuser = getattr(actor, "is_superuser", False)
    if not novel or (novel.owner_id != owner_id and not is_superuser):
        raise HTTPException(status_code=404, detail="小说不存在")
    return novel
