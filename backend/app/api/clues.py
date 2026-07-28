"""Owner-scoped clue run/query/reanalysis/human action endpoints."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.models.clue import (
    ClueActivePointer,
    ClueAnalysisRun,
    ClueAnalysisVersion,
    ClueBudgetLedger,
    ClueEvidenceRef,
)
from app.schemas.clue import (
    ClueHumanActionRequest,
    ClueOverrideAction,
    ClueRunResponse,
    ClueVersionSource,
)
from app.services.clues.overrides import (
    human_adjust_link,
    human_annotate,
    human_confirm,
    human_reject,
)
from app.services.clues.query import (
    build_clue_envelope,
    build_clue_version_view,
    clue_detail_panels,
)
from app.services.clues.versions import (
    ManifestValidationError,
    StalePointerError,
    compare_machine_versions,
    rollback_version,
)
from app.services.clues.worker import dispatch_clue_run

router = APIRouter(dependencies=[Depends(require_user)])


def _run_response(row: ClueAnalysisRun) -> ClueRunResponse:
    return ClueRunResponse(
        id=row.id,
        novel_id=row.novel_id,
        version_id=row.version_id,
        status=row.status,  # type: ignore[arg-type]
        status_reason=row.status_reason,
        progress=row.progress or {},
        cancel_requested=row.cancel_requested,
        updated_at=row.updated_at,
    )


async def _owned_run(db: AsyncSession, owner_id: int, novel_id: int) -> ClueAnalysisRun:
    row = await db.scalar(
        select(ClueAnalysisRun).where(
            ClueAnalysisRun.owner_id == owner_id,
            ClueAnalysisRun.novel_id == novel_id,
            ClueAnalysisRun.active_key == "active",
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="clue analysis run not found")
    return row


@router.post("/{novel_id}/start-or-resume", response_model=ClueRunResponse)
async def start_or_resume(
    background_tasks: BackgroundTasks,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Start or resume durable clue analysis for an owned novel."""

    from app.services.analysis_service import ensure_hierarchy

    build_id = await ensure_hierarchy(db, novel, force=False)
    if not build_id:
        raise HTTPException(
            status_code=400,
            detail="无法准备场景层级：小说可能尚无章节，请先完成导入",
        )

    row = await db.scalar(
        select(ClueAnalysisRun)
        .where(
            ClueAnalysisRun.owner_id == current_user.id,
            ClueAnalysisRun.novel_id == novel.id,
            ClueAnalysisRun.active_key == "active",
        )
        .with_for_update()
    )
    if row is None:
        row = await db.scalar(
            select(ClueAnalysisRun)
            .where(
                ClueAnalysisRun.owner_id == current_user.id,
                ClueAnalysisRun.novel_id == novel.id,
                ClueAnalysisRun.status == "completed",
                ClueAnalysisRun.version_id.is_not(None),
            )
            .order_by(ClueAnalysisRun.id.desc())
            .limit(1)
        )
    if row is None:
        row = ClueAnalysisRun(
            owner_id=current_user.id,
            novel_id=novel.id,
            active_key="active",
            status="pending",
            progress={},
        )
        db.add(row)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            build_id = await ensure_hierarchy(db, novel, force=False)
            if not build_id:
                raise HTTPException(
                    status_code=400,
                    detail="无法准备场景层级：小说可能尚无章节，请先完成导入",
                )
            row = await db.scalar(
                select(ClueAnalysisRun).where(
                    ClueAnalysisRun.owner_id == current_user.id,
                    ClueAnalysisRun.novel_id == novel.id,
                    ClueAnalysisRun.active_key == "active",
                )
            )
            if row is None:
                raise

    if row.status in (
        "paused_dependency",
        "paused_budget",
        "failed",
        "cancelled",
        "pending",
    ):
        row.status = "pending"
        row.cancel_requested = False
        row.status_reason = None
        row.active_key = "active"
        from app.services.clues.worker import production_runtime

        policy = production_runtime().budget_policy
        ledger = await db.scalar(
            select(ClueBudgetLedger).where(ClueBudgetLedger.run_id == row.id)
        )
        if ledger is not None:
            ledger.max_calls = max(ledger.max_calls, policy.max_calls)
            ledger.max_input_tokens = max(
                ledger.max_input_tokens, policy.max_input_tokens
            )
            ledger.max_output_tokens = max(
                ledger.max_output_tokens, policy.max_output_tokens
            )
            if Decimal(ledger.max_cost_usd) < policy.max_cost_usd:
                ledger.max_cost_usd = policy.max_cost_usd
            ledger.reserved_calls = 0
            ledger.reserved_input_tokens = 0
            ledger.reserved_output_tokens = 0
            ledger.reserved_cost_usd = Decimal("0")

    await db.commit()
    await db.refresh(row)
    if row.status != "completed":
        background_tasks.add_task(dispatch_clue_run, row.id)
    return _run_response(row)


@router.get("/{novel_id}/status", response_model=ClueRunResponse)
async def status(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    return _run_response(await _owned_run(db, current_user.id, novel.id))


@router.post("/{novel_id}/cancel", response_model=ClueRunResponse)
async def cancel(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    row = await _owned_run(db, current_user.id, novel.id)
    row.cancel_requested = True
    row.status = "cancelled"
    await db.commit()
    await db.refresh(row)
    return _run_response(row)


@router.post("/{novel_id}/resume", response_model=ClueRunResponse)
async def resume(
    background_tasks: BackgroundTasks,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    from app.services.analysis_service import ensure_hierarchy

    build_id = await ensure_hierarchy(db, novel, force=False)
    if not build_id:
        raise HTTPException(
            status_code=400,
            detail="无法准备场景层级：小说可能尚无章节，请先完成导入",
        )
    row = await _owned_run(db, current_user.id, novel.id)
    row.status = "pending"
    row.cancel_requested = False
    row.status_reason = None
    await db.commit()
    await db.refresh(row)
    background_tasks.add_task(dispatch_clue_run, row.id)
    return _run_response(row)


@router.post("/{novel_id}/reanalyze", response_model=ClueRunResponse)
async def reanalyze(
    background_tasks: BackgroundTasks,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Start a new candidate run for reanalysis; preserves human overrides."""

    from app.services.analysis_service import ensure_hierarchy

    build_id = await ensure_hierarchy(db, novel, force=False)
    if not build_id:
        raise HTTPException(
            status_code=400,
            detail="无法准备场景层级：小说可能尚无章节，请先完成导入",
        )

    existing = await db.scalar(
        select(ClueAnalysisRun)
        .where(
            ClueAnalysisRun.owner_id == current_user.id,
            ClueAnalysisRun.novel_id == novel.id,
            ClueAnalysisRun.active_key == "active",
        )
        .with_for_update()
    )
    if existing is not None and existing.status in {"running", "pending"}:
        raise HTTPException(status_code=409, detail="clue analysis already running")
    if existing is not None:
        existing.active_key = None
        if existing.status not in {"completed", "failed", "cancelled"}:
            existing.status = "cancelled"
            existing.cancel_requested = True

    row = ClueAnalysisRun(
        owner_id=current_user.id,
        novel_id=novel.id,
        active_key="active",
        status="pending",
        progress={"reanalysis": True},
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    background_tasks.add_task(dispatch_clue_run, row.id)
    return _run_response(row)


@router.get("/{novel_id}")
async def get_clues(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    full_book: bool = False,
    character_id: int | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
):
    return await build_clue_envelope(
        db,
        novel=novel,
        owner_id=current_user.id,
        request_full_book=full_book,
        character_id=character_id,
        status_filter=status_filter,
    )


@router.get("/{novel_id}/versions/{version_id}")
async def get_version(
    version_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    full_book: bool = False,
    character_id: int | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
):
    view = await build_clue_version_view(
        db,
        novel=novel,
        owner_id=current_user.id,
        source=ClueVersionSource.HISTORY,
        request_full_book=full_book,
        character_id=character_id,
        status_filter=status_filter,
        version_id=version_id,
    )
    if view is None:
        raise HTTPException(status_code=404, detail="clue version not found")
    return view


@router.get("/{novel_id}/versions/{version_id}/clues/{logical_clue_id}")
async def get_clue_detail(
    version_id: int,
    logical_clue_id: str,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    full_book: bool = False,
):
    panels = await clue_detail_panels(
        db,
        novel=novel,
        owner_id=current_user.id,
        version_id=version_id,
        logical_clue_id=logical_clue_id,
        request_full_book=full_book,
    )
    if panels is None:
        raise HTTPException(status_code=404, detail="clue not found")
    return panels


@router.get("/{novel_id}/compare")
async def compare_versions(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    from_version_id: int = Query(...),
    to_version_id: int = Query(...),
):
    for vid in (from_version_id, to_version_id):
        version = await db.get(ClueAnalysisVersion, vid)
        if (
            version is None
            or version.owner_id != current_user.id
            or version.novel_id != novel.id
        ):
            raise HTTPException(status_code=404, detail="clue version not found")
    return await compare_machine_versions(
        db, from_version_id=from_version_id, to_version_id=to_version_id
    )


@router.post("/{novel_id}/rollback")
async def rollback(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
    target_version_id: int = Query(...),
    expected_revision: int = Query(...),
):
    try:
        pointer = await rollback_version(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            target_version_id=target_version_id,
            expected_revision=expected_revision,
        )
    except StalePointerError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ManifestValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "version_id": pointer.version_id,
        "revision": pointer.revision,
        "manifest_checksum": pointer.manifest_checksum,
    }


@router.post("/{novel_id}/clues/{logical_clue_id}/actions")
async def human_action(
    logical_clue_id: str,
    body: ClueHumanActionRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Protected confirm/reject/annotate/adjust-link on an owned novel clue."""

    pointer = await db.scalar(
        select(ClueActivePointer).where(
            ClueActivePointer.owner_id == current_user.id,
            ClueActivePointer.novel_id == novel.id,
        )
    )
    if pointer is None:
        raise HTTPException(status_code=404, detail="no active clue version")
    version_id = pointer.version_id

    # Ownership: logical clue must exist in this version/scope.
    from app.models.clue import MachineClue

    machine = await db.scalar(
        select(MachineClue).where(
            MachineClue.owner_id == current_user.id,
            MachineClue.novel_id == novel.id,
            MachineClue.version_id == version_id,
            MachineClue.logical_clue_id == logical_clue_id,
        )
    )
    if machine is None:
        raise HTTPException(status_code=404, detail="clue not found")

    author = current_user.username or f"user:{current_user.id}"
    evidence_rows = list(
        (
            await db.scalars(
                select(ClueEvidenceRef).where(
                    ClueEvidenceRef.version_id == version_id,
                    ClueEvidenceRef.logical_clue_id == logical_clue_id,
                    ClueEvidenceRef.role == "cue",
                )
            )
        ).all()
    )
    evidence = [
        {
            "evidence_id": e.evidence_id,
            "role": e.role,
            "chapter_id": e.chapter_id,
            "narrative_chapter_number": e.narrative_chapter_number,
            "source_start": e.source_start,
            "source_end": e.source_end,
            "content_hash": e.content_hash,
            "excerpt": e.excerpt,
        }
        for e in evidence_rows
    ]

    try:
        if body.action == ClueOverrideAction.CONFIRM:
            override, _ = await human_confirm(
                db,
                owner_id=current_user.id,
                novel_id=novel.id,
                version_id=version_id,
                logical_clue_id=logical_clue_id,
                author=author,
                reason=body.reason,
                evidence=evidence,
            )
        elif body.action == ClueOverrideAction.REJECT:
            override, _ = await human_reject(
                db,
                owner_id=current_user.id,
                novel_id=novel.id,
                version_id=version_id,
                logical_clue_id=logical_clue_id,
                author=author,
                reason=body.reason,
                evidence=[],
            )
        elif body.action == ClueOverrideAction.ANNOTATE:
            override = await human_annotate(
                db,
                owner_id=current_user.id,
                novel_id=novel.id,
                version_id=version_id,
                logical_clue_id=logical_clue_id,
                author=author,
                reason=body.reason,
                note=body.note or "",
            )
        elif body.action == ClueOverrideAction.ADJUST_LINK:
            override = await human_adjust_link(
                db,
                owner_id=current_user.id,
                novel_id=novel.id,
                version_id=version_id,
                logical_clue_id=logical_clue_id,
                author=author,
                reason=body.reason,
                link=body.link or {},
            )
        else:
            raise HTTPException(status_code=400, detail="unsupported action")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await db.commit()
    return {
        "override_id": override.id,
        "action": override.action,
        "logical_clue_id": logical_clue_id,
        "version_id": version_id,
        "status": override.status,
        "supersedes_id": override.supersedes_id,
    }
