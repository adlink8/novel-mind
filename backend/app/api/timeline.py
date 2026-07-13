"""Durable, owner-scoped timeline orchestration and spoiler-safe reads."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.models.analysis import AnalysisRun
from app.models.timeline import TimelineOverride
from app.schemas.timeline import (
    TimelineEditRequest, TimelineEnvelope, TimelineOrdering, TimelinePreferenceRequest,
    TimelineRollbackRequest, TimelineRunResponse, TimelineVersionSource, TimelineVersionView,
)
from app.services.timeline.promotion import ManifestValidationError, StalePointerError, rollback_version
from app.services.timeline.query import build_version_view

router = APIRouter(dependencies=[Depends(require_user)])


def _run_response(row: AnalysisRun) -> TimelineRunResponse:
    return TimelineRunResponse(id=row.id, novel_id=row.novel_id, version_id=row.version_id,
                               status=row.status, status_reason=row.status_reason,
                               progress=row.progress or {}, cancel_requested=row.cancel_requested,
                               updated_at=row.updated_at)


async def _owned_run(db: AsyncSession, owner_id: int, novel_id: int) -> AnalysisRun:
    row = await db.scalar(select(AnalysisRun).where(
        AnalysisRun.owner_id == owner_id, AnalysisRun.novel_id == novel_id,
        AnalysisRun.active_key == "active",
    ))
    if row is None:
        raise HTTPException(status_code=404, detail="analysis run not found")
    return row


@router.post("/{novel_id}/start-or-resume", response_model=TimelineRunResponse)
async def start_or_resume(novel: Novel = Depends(require_owned_novel), db: AsyncSession = Depends(get_db),
                          current_user: User = Depends(require_user)):
    row = await db.scalar(select(AnalysisRun).where(
        AnalysisRun.owner_id == current_user.id, AnalysisRun.novel_id == novel.id,
        AnalysisRun.active_key == "active",
    ).with_for_update())
    if row is None:
        row = AnalysisRun(owner_id=current_user.id, novel_id=novel.id,
                          active_key="active", status="pending", progress={})
        db.add(row)
        await db.flush()
    await db.commit()
    await db.refresh(row)
    return _run_response(row)


@router.get("/{novel_id}/status", response_model=TimelineRunResponse)
async def status(novel: Novel = Depends(require_owned_novel), db: AsyncSession = Depends(get_db),
                 current_user: User = Depends(require_user)):
    return _run_response(await _owned_run(db, current_user.id, novel.id))


@router.post("/{novel_id}/cancel", response_model=TimelineRunResponse)
async def cancel(novel: Novel = Depends(require_owned_novel), db: AsyncSession = Depends(get_db),
                 current_user: User = Depends(require_user)):
    row = await _owned_run(db, current_user.id, novel.id)
    row.status, row.cancel_requested = "cancelled", True
    await db.commit(); await db.refresh(row)
    return _run_response(row)


@router.post("/{novel_id}/resume", response_model=TimelineRunResponse)
async def resume(novel: Novel = Depends(require_owned_novel), db: AsyncSession = Depends(get_db),
                 current_user: User = Depends(require_user)):
    row = await _owned_run(db, current_user.id, novel.id)
    row.status, row.cancel_requested = "pending", False
    await db.commit(); await db.refresh(row)
    return _run_response(row)


@router.get("/{novel_id}", response_model=TimelineEnvelope)
async def get_timeline(novel: Novel = Depends(require_owned_novel), db: AsyncSession = Depends(get_db),
                       current_user: User = Depends(require_user),
                       ordering: TimelineOrdering = TimelineOrdering.NARRATIVE,
                       person: str | None = Query(default=None, max_length=100),
                       causal: bool = False, full_book: bool = False):
    common = dict(session=db, novel=novel, owner_id=current_user.id, ordering=ordering,
                  person=person, include_causal=causal, request_full_book=full_book)
    return TimelineEnvelope(
        active=await build_version_view(source=TimelineVersionSource.ACTIVE, **common),
        running_candidate=await build_version_view(source=TimelineVersionSource.RUNNING_CANDIDATE, **common),
    )


@router.get("/{novel_id}/versions/{version_id}", response_model=TimelineVersionView)
async def get_version(version_id: int, novel: Novel = Depends(require_owned_novel),
                      db: AsyncSession = Depends(get_db), current_user: User = Depends(require_user),
                      ordering: TimelineOrdering = TimelineOrdering.NARRATIVE,
                      person: str | None = Query(default=None, max_length=100), causal: bool = False,
                      full_book: bool = False):
    active = await build_version_view(db, novel=novel, owner_id=current_user.id,
        source=TimelineVersionSource.ACTIVE, ordering=ordering, person=person,
        include_causal=causal, request_full_book=full_book)
    candidate = await build_version_view(db, novel=novel, owner_id=current_user.id,
        source=TimelineVersionSource.RUNNING_CANDIDATE, ordering=ordering, person=person,
        include_causal=causal, request_full_book=full_book)
    result = next((view for view in (active, candidate) if view and view.version_id == version_id), None)
    if result is None:
        raise HTTPException(status_code=404, detail="timeline version not found")
    return result


@router.post("/{novel_id}/rollback")
async def rollback(data: TimelineRollbackRequest, novel: Novel = Depends(require_owned_novel),
                   db: AsyncSession = Depends(get_db), current_user: User = Depends(require_user)):
    try:
        pointer = await rollback_version(db, owner_id=current_user.id, novel_id=novel.id,
                                         target_version_id=data.target_version_id,
                                         expected_revision=data.expected_revision)
    except StalePointerError as exc:
        raise HTTPException(status_code=409, detail="timeline pointer changed") from exc
    except ManifestValidationError as exc:
        raise HTTPException(status_code=422, detail="timeline version cannot be activated") from exc
    return {"version_id": pointer.version_id, "revision": pointer.revision}


@router.put("/{novel_id}/events/{logical_event_id}")
async def edit_event(logical_event_id: str, data: TimelineEditRequest,
                     novel: Novel = Depends(require_owned_novel), db: AsyncSession = Depends(get_db),
                     current_user: User = Depends(require_user)):
    current = await db.scalar(select(TimelineOverride).where(
        TimelineOverride.owner_id == current_user.id, TimelineOverride.novel_id == novel.id,
        TimelineOverride.logical_event_id == logical_event_id,
        TimelineOverride.field_name == data.field_name, TimelineOverride.status == "active",
    ).order_by(TimelineOverride.id.desc()).limit(1))
    if current is not None:
        current.status = "superseded"
    row = TimelineOverride(owner_id=current_user.id, novel_id=novel.id,
        logical_event_id=logical_event_id, field_name=data.field_name,
        value=data.value, supersedes_id=current.id if current else None)
    db.add(row); await db.commit(); await db.refresh(row)
    return {"override_id": row.id, "logical_event_id": logical_event_id,
            "field_name": data.field_name, "provenance": "manual"}


@router.put("/{novel_id}/preference")
async def set_preference(data: TimelinePreferenceRequest, novel: Novel = Depends(require_owned_novel),
                         db: AsyncSession = Depends(get_db)):
    novel.reading_progress = {**(novel.reading_progress or {}), "timeline_full_book": data.full_book}
    await db.commit()
    return {"full_book": data.full_book}


# Legacy compatibility: extraction now means durable start/resume; old edit path remains scoped by lookup.
@router.post("/{novel_id}/extract", response_model=TimelineRunResponse)
async def extract_timeline(novel: Novel = Depends(require_owned_novel), db: AsyncSession = Depends(get_db),
                           current_user: User = Depends(require_user)):
    return await start_or_resume(novel, db, current_user)
