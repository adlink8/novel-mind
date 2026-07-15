"""
Phase 10 owner-scoped multi-conversation and message API.

Every route starts from require_owned_novel. Child resources are scoped by
owner + novel + conversation and return 404 for inaccessible IDs.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.schemas.reader_chat import (
    ConversationCreate,
    ConversationDetail,
    ConversationListItem,
    ConversationPatch,
    GenerationJobView,
    MessageAccepted,
    MessageCreate,
    MessageView,
)
from app.services.reader_chat.conversations import conversation_service
from app.services.reader_chat.worker import dispatch_reader_chat_job

router = APIRouter(dependencies=[Depends(require_user)])


@router.get(
    "/{novel_id}/conversations",
    response_model=dict,
)
async def list_conversations(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    status_filter: str | None = Query(default=None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> dict:
    items, total = await conversation_service.list_conversations(
        db,
        novel=novel,
        owner_id=current_user.id,
        status=status_filter,
        skip=skip,
        limit=limit,
    )
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.post(
    "/{novel_id}/conversations",
    response_model=ConversationDetail,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
    data: ConversationCreate,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> ConversationDetail:
    return await conversation_service.create_conversation(
        db, novel=novel, owner_id=current_user.id, data=data
    )


@router.get(
    "/{novel_id}/conversations/{conversation_id}",
    response_model=ConversationDetail,
)
async def get_conversation(
    conversation_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> ConversationDetail:
    return await conversation_service.get_conversation(
        db,
        novel=novel,
        owner_id=current_user.id,
        conversation_id=conversation_id,
    )


@router.patch(
    "/{novel_id}/conversations/{conversation_id}",
    response_model=ConversationDetail,
)
async def patch_conversation(
    conversation_id: int,
    data: ConversationPatch,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> ConversationDetail:
    return await conversation_service.patch_conversation(
        db,
        novel=novel,
        owner_id=current_user.id,
        conversation_id=conversation_id,
        data=data,
    )


@router.delete(
    "/{novel_id}/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_conversation(
    conversation_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> Response:
    await conversation_service.delete_conversation(
        db,
        novel=novel,
        owner_id=current_user.id,
        conversation_id=conversation_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{novel_id}/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    after_sequence: int = Query(0, ge=0),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
) -> dict:
    items, total = await conversation_service.list_messages(
        db,
        novel=novel,
        owner_id=current_user.id,
        conversation_id=conversation_id,
        after_sequence=after_sequence,
        skip=skip,
        limit=limit,
    )
    return {
        "items": [item.model_dump(mode="json") for item in items],
        "total": total,
        "skip": skip,
        "limit": limit,
        "after_sequence": after_sequence,
    }


@router.post(
    "/{novel_id}/conversations/{conversation_id}/messages",
    response_model=MessageAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_message(
    conversation_id: int,
    data: MessageCreate,
    background_tasks: BackgroundTasks,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> MessageAccepted:
    accepted = await conversation_service.create_message_safe(
        db,
        novel=novel,
        owner_id=current_user.id,
        conversation_id=conversation_id,
        data=data,
    )
    # Durable job: dispatch after request commits (BackgroundTasks + get_db commit).
    if accepted.job.status.value in ("queued", "running"):
        background_tasks.add_task(dispatch_reader_chat_job, accepted.job.id)
    return accepted


@router.get(
    "/{novel_id}/conversations/{conversation_id}/jobs/{job_id}",
    response_model=GenerationJobView,
)
async def get_job(
    conversation_id: int,
    job_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> GenerationJobView:
    return await conversation_service.get_job(
        db,
        novel=novel,
        owner_id=current_user.id,
        conversation_id=conversation_id,
        job_id=job_id,
    )


@router.post(
    "/{novel_id}/conversations/{conversation_id}/jobs/{job_id}/cancel",
    response_model=GenerationJobView,
)
async def cancel_job(
    conversation_id: int,
    job_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> GenerationJobView:
    return await conversation_service.cancel_job(
        db,
        novel=novel,
        owner_id=current_user.id,
        conversation_id=conversation_id,
        job_id=job_id,
    )


@router.post(
    "/{novel_id}/conversations/{conversation_id}/jobs/{job_id}/retry",
    response_model=GenerationJobView,
)
async def retry_job(
    conversation_id: int,
    job_id: int,
    background_tasks: BackgroundTasks,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
) -> GenerationJobView:
    view = await conversation_service.retry_job(
        db,
        novel=novel,
        owner_id=current_user.id,
        conversation_id=conversation_id,
        job_id=job_id,
    )
    if view.status.value in ("queued", "running"):
        background_tasks.add_task(dispatch_reader_chat_job, view.id)
    return view


# Silence unused import warnings for OpenAPI model refs used by response_model.
_ = (ConversationListItem, MessageView)
