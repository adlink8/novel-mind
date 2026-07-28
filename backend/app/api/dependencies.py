"""Shared API authorization dependencies."""

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User


async def require_owned_novel(
    novel_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> Novel:
    result = await db.execute(select(Novel).where(Novel.id == novel_id))
    novel = result.scalar_one_or_none()
    if not novel or (
        novel.owner_id != current_user.id and not current_user.is_superuser
    ):
        raise HTTPException(status_code=404, detail="小说不存在")
    return novel
