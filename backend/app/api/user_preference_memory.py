"""Owner-scoped user preference memory API."""

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import require_user
from app.models import User
from app.schemas.user_preference_memory import UserPreferenceMemoryList
from app.services.user_preference_memory import (
    delete_all_preference_memories,
    delete_preference_memory,
    list_preference_memories,
)

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("", response_model=UserPreferenceMemoryList)
async def list_memories(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> UserPreferenceMemoryList:
    items = await list_preference_memories(db, owner_id=current_user.id)
    return UserPreferenceMemoryList(items=items, total=len(items))


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memory(
    memory_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> Response:
    deleted = await delete_preference_memory(
        db, owner_id=current_user.id, memory_id=memory_id
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="记忆不存在")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_memories(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> Response:
    await delete_all_preference_memories(db, owner_id=current_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
