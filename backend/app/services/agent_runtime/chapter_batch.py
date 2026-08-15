"""Bounded, resumable orchestration for per-chapter ``analyze-chapter`` runs.

The existing ``skill_runs`` table is the durable source of truth.  A batch is
identified by a deterministic manifest stored in each run's frozen manifest;
the model-facing input contains only one chapter.  Only the configured window
is materialized at a time, so a 400-chapter request never becomes a 400-item
model prompt or a 400-run initial dispatch burst.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agent_runtime import SkillRegistry, SkillRun, SkillVersion
from app.models.novel import Chapter, Novel
from app.services.agent_runtime import registry as registry_service
from app.services.reader_chat.context_queryplan import resolve_progress_snapshot
from app.services.tool_connectors.service import freeze_connector_versions

MAX_CONCURRENCY_WINDOW = 32
CHAPTER_BATCH_ORIGIN = "chapter_batch"
CHAPTER_BATCH_MANIFEST_KEY = "chapter_batch"


class ChapterBatchError(ValueError):
    """Stable validation/coordination error for the chapter-batch boundary."""


@dataclass(frozen=True)
class ChapterRef:
    id: int
    chapter_number: int


@dataclass(frozen=True)
class ChapterRunSpec:
    chapter_id: int
    chapter_number: int
    input: dict[str, Any]


@dataclass(frozen=True)
class ChapterBatchPlan:
    batch_id: str
    owner_id: int
    novel_id: int
    cutoff_chapter: int
    concurrency_window: int
    chapters: list[ChapterRef]
    next_window: list[ChapterRunSpec]
    pending_chapter_ids: list[int]

    @property
    def total(self) -> int:
        return len(self.chapters)


def _validate_window(concurrency_window: int) -> int:
    value = int(concurrency_window)
    if value < 1 or value > MAX_CONCURRENCY_WINDOW:
        raise ChapterBatchError(
            f"concurrency_window must be between 1 and {MAX_CONCURRENCY_WINDOW}"
        )
    return value


def _batch_id(
    *, owner_id: int, novel_id: int, cutoff_chapter: int, skill_version_id: int, chapters: Iterable[ChapterRef]
) -> str:
    payload = ":".join(
        [
            str(owner_id),
            str(novel_id),
            str(cutoff_chapter),
            str(skill_version_id),
            ",".join(f"{item.id}:{item.chapter_number}" for item in chapters),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _chapter_question(*, novel_id: int, chapter_number: int, cutoff_chapter: int) -> str:
    return (
        f"分析小说{novel_id}第{chapter_number}章的人物、事件、线索与连续性；"
        f"cutoff={cutoff_chapter}。先调用get_chapter读取正文，定位原文时用"
        "get_evidence_span。最终仅输出符合analyze-chapter schema的单个JSON对象，"
        "不要Markdown或解释。"
    )


def _chapter_run_specs(
    *, novel_id: int, cutoff_chapter: int, batch_id: str, chapters: list[ChapterRef]
) -> list[ChapterRunSpec]:
    specs: list[ChapterRunSpec] = []
    for item in chapters:
        question = _chapter_question(
            novel_id=novel_id,
            chapter_number=item.chapter_number,
            cutoff_chapter=cutoff_chapter,
        )
        specs.append(
            ChapterRunSpec(
                chapter_id=item.id,
                chapter_number=item.chapter_number,
                input={
                    "novel_id": novel_id,
                    "chapter_id": item.id,
                    "chapter_number": item.chapter_number,
                    "batch_id": batch_id,
                    "question": question,
                    "execution_prompt": question,
                },
            )
        )
    return specs


def build_chapter_batch_plan(
    *,
    owner_id: int,
    novel_id: int,
    cutoff_chapter: int,
    chapters: list[ChapterRef],
    concurrency_window: int,
    skill_version_id: int = 0,
) -> ChapterBatchPlan:
    """Build a deterministic bounded plan from server-resolved real chapters."""

    window = _validate_window(concurrency_window)
    if owner_id < 1 or novel_id < 1 or cutoff_chapter < 1:
        raise ChapterBatchError("owner_id, novel_id and cutoff_chapter must be positive")
    normalized = sorted(chapters, key=lambda item: (item.chapter_number, item.id))
    if not normalized:
        raise ChapterBatchError("no real chapters matched the requested batch")
    if any(item.id < 1 or item.chapter_number < 1 for item in normalized):
        raise ChapterBatchError("chapter references must be positive")
    if any(item.chapter_number > cutoff_chapter for item in normalized):
        raise ChapterBatchError("requested chapter exceeds reading cutoff")
    if len({item.id for item in normalized}) != len(normalized):
        raise ChapterBatchError("duplicate chapter id in resolved batch")

    batch_id = _batch_id(
        owner_id=owner_id,
        novel_id=novel_id,
        cutoff_chapter=cutoff_chapter,
        skill_version_id=skill_version_id,
        chapters=normalized,
    )
    specs = _chapter_run_specs(
        novel_id=novel_id,
        cutoff_chapter=cutoff_chapter,
        batch_id=batch_id,
        chapters=normalized,
    )
    return ChapterBatchPlan(
        batch_id=batch_id,
        owner_id=owner_id,
        novel_id=novel_id,
        cutoff_chapter=cutoff_chapter,
        concurrency_window=window,
        chapters=normalized,
        next_window=specs[:window],
        pending_chapter_ids=[item.chapter_id for item in specs[window:]],
    )


async def _active_analyze_chapter_version(
    db: AsyncSession, *, owner_id: int, novel_id: int
) -> SkillVersion | None:
    registry = await db.scalar(
        select(SkillRegistry).where(
            SkillRegistry.owner_id == owner_id,
            SkillRegistry.novel_id == novel_id,
            SkillRegistry.name == "analyze-chapter",
            SkillRegistry.status == "active",
        )
    )
    if registry is None:
        return None
    return await db.scalar(
        select(SkillVersion)
        .where(
            SkillVersion.registry_id == registry.id,
            SkillVersion.status == "active",
        )
        .order_by(SkillVersion.id.desc())
    )


async def _resolve_real_chapters(
    db: AsyncSession,
    *,
    novel_id: int,
    cutoff_chapter: int,
    chapter_start: int | None,
    chapter_end: int | None,
    chapter_ids: list[int] | None,
) -> list[ChapterRef]:
    if chapter_ids is not None:
        wanted = list(dict.fromkeys(int(value) for value in chapter_ids))
        if not wanted:
            raise ChapterBatchError("chapter_ids must not be empty")
        rows = list(
            (
                await db.scalars(
                    select(Chapter).where(
                        Chapter.novel_id == novel_id,
                        Chapter.id.in_(wanted),
                    )
                )
            ).all()
        )
        found = {row.id for row in rows}
        missing = sorted(set(wanted) - found)
        if missing:
            raise ChapterBatchError("chapter_ids contain chapters outside this novel")
        refs = [ChapterRef(id=row.id, chapter_number=row.chapter_number) for row in rows]
    else:
        if chapter_start is None or chapter_end is None:
            raise ChapterBatchError("chapter_start and chapter_end are required")
        if chapter_start > chapter_end:
            raise ChapterBatchError("chapter_start must not exceed chapter_end")
        if chapter_start > cutoff_chapter:
            raise ChapterBatchError("chapter_start exceeds reading cutoff")
        rows = list(
            (
                await db.scalars(
                    select(Chapter).where(
                        Chapter.novel_id == novel_id,
                        Chapter.chapter_number >= chapter_start,
                        Chapter.chapter_number <= min(chapter_end, cutoff_chapter),
                    )
                )
            ).all()
        )
        refs = [ChapterRef(id=row.id, chapter_number=row.chapter_number) for row in rows]
    refs.sort(key=lambda item: (item.chapter_number, item.id))
    if not refs:
        raise ChapterBatchError("no real chapters matched the requested batch")
    if any(item.chapter_number > cutoff_chapter for item in refs):
        raise ChapterBatchError("requested chapter exceeds reading cutoff")
    return refs


def _manifest_for_plan(plan: ChapterBatchPlan, *, requested: dict[str, Any]) -> dict[str, Any]:
    return {
        CHAPTER_BATCH_MANIFEST_KEY: {
            "version": 1,
            "batch_id": plan.batch_id,
            "owner_id": plan.owner_id,
            "novel_id": plan.novel_id,
            "cutoff_chapter": plan.cutoff_chapter,
            "concurrency_window": plan.concurrency_window,
            "chapter_ids": [item.id for item in plan.chapters],
            "chapter_numbers": [item.chapter_number for item in plan.chapters],
            "requested": requested,
        }
    }


def _batch_manifest(run: SkillRun) -> dict[str, Any] | None:
    raw = dict(run.frozen_manifest or {}).get(CHAPTER_BATCH_MANIFEST_KEY)
    return dict(raw) if isinstance(raw, dict) else None


def _accepted_manifest(run: SkillRun) -> dict[str, Any]:
    """Copy only accept-time batch lineage into a fresh immutable attempt."""

    source = dict(run.frozen_manifest or {})
    return {
        key: source[key]
        for key in (
            CHAPTER_BATCH_MANIFEST_KEY,
            "runtime_manifest_checksum",
            "connector_versions",
        )
        if key in source
    }


def _latest_runs_by_chapter(runs: list[SkillRun]) -> dict[int, SkillRun]:
    latest: dict[int, SkillRun] = {}
    for row in sorted(runs, key=lambda item: item.id):
        chapter_id = (row.input or {}).get("chapter_id")
        if chapter_id is not None:
            latest[int(chapter_id)] = row
    return latest


async def _create_retry_attempts(
    db: AsyncSession,
    *,
    runs: list[SkillRun],
    by_chapter: dict[int, SkillRun],
    concurrency_window: int,
    active: int,
) -> tuple[int, list[int]]:
    """Queue fresh attempts; terminal attempts and their frozen manifests stay immutable."""

    created: list[int] = []
    for previous in sorted(by_chapter.values(), key=lambda item: item.id):
        if active >= concurrency_window:
            break
        if previous.status not in ("failed", "cancelled"):
            continue
        attempt = SkillRun(
            owner_id=previous.owner_id,
            novel_id=previous.novel_id,
            skill_version_id=previous.skill_version_id,
            status="queued",
            status_reason="chapter_batch_resumed",
            input=dict(previous.input or {}),
            input_hash=previous.input_hash,
            frozen_manifest=_accepted_manifest(previous),
            budget_snapshot=dict(previous.budget_snapshot or {}),
            internal_token_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
            retry_count=int(previous.retry_count or 0) + 1,
            origin=CHAPTER_BATCH_ORIGIN,
        )
        db.add(attempt)
        await db.flush()
        chapter_id = int((attempt.input or {})["chapter_id"])
        by_chapter[chapter_id] = attempt
        runs.append(attempt)
        created.append(attempt.id)
        active += 1
    return active, created


def _plan_from_manifest(run: SkillRun, manifest: dict[str, Any]) -> ChapterBatchPlan:
    chapter_ids = list(manifest.get("chapter_ids") or [])
    chapter_numbers = list(manifest.get("chapter_numbers") or [])
    if not chapter_ids or len(chapter_ids) != len(chapter_numbers):
        raise ChapterBatchError("chapter batch manifest chapter set is invalid")
    if int(manifest.get("owner_id") or 0) != run.owner_id:
        raise ChapterBatchError("chapter batch manifest owner mismatch")
    if int(manifest.get("novel_id") or 0) != run.novel_id:
        raise ChapterBatchError("chapter batch manifest novel mismatch")
    return ChapterBatchPlan(
        batch_id=str(manifest.get("batch_id") or ""),
        owner_id=run.owner_id,
        novel_id=run.novel_id,
        cutoff_chapter=int(manifest.get("cutoff_chapter") or 0),
        concurrency_window=_validate_window(
            int(manifest.get("concurrency_window") or 0)
        ),
        chapters=[
            ChapterRef(id=int(chapter_id), chapter_number=int(chapter_numbers[index]))
            for index, chapter_id in enumerate(chapter_ids)
        ],
        next_window=[],
        pending_chapter_ids=[],
    )


async def _refill_existing_batch(
    db: AsyncSession,
    *,
    anchor_run: SkillRun,
    retry_terminal: bool,
) -> dict[str, Any]:
    """Fill available slots from one batch's frozen chapter manifest."""

    manifest = _batch_manifest(anchor_run)
    if manifest is None:
        raise ChapterBatchError("chapter batch manifest missing")
    plan = _plan_from_manifest(anchor_run, manifest)
    if not plan.batch_id:
        raise ChapterBatchError("chapter batch manifest id is invalid")

    # All creators/continuations serialize on the novel row, preventing two
    # successful finalize tasks from filling the same free slot concurrently.
    novel = await db.scalar(
        select(Novel)
        .where(Novel.id == plan.novel_id, Novel.owner_id == plan.owner_id)
        .with_for_update()
    )
    if novel is None:
        raise ChapterBatchError("novel is outside owner scope")

    all_rows = list(
        (
            await db.scalars(
                select(SkillRun)
                .where(
                    SkillRun.owner_id == plan.owner_id,
                    SkillRun.novel_id == plan.novel_id,
                    SkillRun.origin == CHAPTER_BATCH_ORIGIN,
                )
                .order_by(SkillRun.id.asc())
            )
        ).all()
    )
    runs = [
        row
        for row in all_rows
        if (_batch_manifest(row) or {}).get("batch_id") == plan.batch_id
    ]
    by_chapter = _latest_runs_by_chapter(runs)
    active = sum(1 for row in runs if row.status in ("queued", "running"))
    created_run_ids: list[int] = []
    if retry_terminal:
        active, retry_ids = await _create_retry_attempts(
            db,
            runs=runs,
            by_chapter=by_chapter,
            concurrency_window=plan.concurrency_window,
            active=active,
        )
        created_run_ids.extend(retry_ids)

    for spec in _chapter_run_specs(
        novel_id=plan.novel_id,
        cutoff_chapter=plan.cutoff_chapter,
        batch_id=plan.batch_id,
        chapters=plan.chapters,
    ):
        if spec.chapter_id in by_chapter:
            continue
        if active >= plan.concurrency_window:
            break
        input_payload = dict(spec.input)
        run = SkillRun(
            owner_id=plan.owner_id,
            novel_id=plan.novel_id,
            skill_version_id=anchor_run.skill_version_id,
            status="queued",
            input=input_payload,
            input_hash=registry_service.canonical_input_hash(input_payload),
            frozen_manifest=_accepted_manifest(anchor_run),
            budget_snapshot=dict(anchor_run.budget_snapshot or {}),
            internal_token_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
            origin=CHAPTER_BATCH_ORIGIN,
        )
        db.add(run)
        await db.flush()
        by_chapter[spec.chapter_id] = run
        runs.append(run)
        active += 1
        created_run_ids.append(run.id)
    await db.flush()
    return _status_for_runs(
        plan=plan, runs=runs, created_run_ids=created_run_ids
    )


def _status_for_runs(
    *, plan: ChapterBatchPlan, runs: list[SkillRun], created_run_ids: list[int]
) -> dict[str, Any]:
    by_chapter = _latest_runs_by_chapter(runs)
    chapter_items = []
    counts = {"queued": 0, "running": 0, "completed": 0, "failed": 0, "cancelled": 0, "pending": 0}
    for item in plan.chapters:
        row = by_chapter.get(item.id)
        state = row.status if row is not None else "pending"
        counts[state] += 1
        chapter_items.append(
            {
                "chapter_id": item.id,
                "chapter_number": item.chapter_number,
                "status": state,
                "run_id": row.id if row is not None else None,
            }
        )
    if counts["completed"] == plan.total:
        batch_status = "completed"
    elif counts["running"]:
        batch_status = "running"
    elif counts["queued"] or counts["pending"]:
        batch_status = "queued"
    elif counts["failed"]:
        batch_status = "failed"
    else:
        batch_status = "cancelled"
    return {
        "batch_id": plan.batch_id,
        "owner_id": plan.owner_id,
        "novel_id": plan.novel_id,
        "cutoff_chapter": plan.cutoff_chapter,
        "concurrency_window": plan.concurrency_window,
        "status": batch_status,
        "total": plan.total,
        "queued": counts["queued"],
        "running": counts["running"],
        "completed": counts["completed"],
        "failed": counts["failed"],
        "cancelled": counts["cancelled"],
        "pending": counts["pending"],
        "created_run_ids": created_run_ids,
        "chapters": chapter_items,
    }


async def create_or_resume_chapter_batch(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    chapter_start: int | None = None,
    chapter_end: int | None = None,
    chapter_ids: list[int] | None = None,
    concurrency_window: int = 3,
    retry_terminal: bool = False,
) -> dict[str, Any]:
    """Resolve scope and materialize only the next bounded window of runs."""

    window = _validate_window(concurrency_window)
    novel = await db.scalar(
        select(Novel)
        .where(Novel.id == novel_id, Novel.owner_id == owner_id)
        .with_for_update()
    )
    if novel is None:
        raise ChapterBatchError("novel is outside owner scope")
    progress = await resolve_progress_snapshot(db, novel)
    cutoff = int(progress.cutoff_chapter_number)
    version = await _active_analyze_chapter_version(db, owner_id=owner_id, novel_id=novel_id)
    if version is None:
        raise ChapterBatchError("active analyze-chapter skill is not registered")
    try:
        registry_service.skill_runtime_manifest(version)
        connector_versions = await freeze_connector_versions(
            db, owner_id=owner_id, allowed_tools=list(version.allowed_tools or [])
        )
    except Exception as exc:  # fail closed without changing registry behavior
        raise ChapterBatchError("analyze-chapter skill contract is unavailable") from exc
    refs = await _resolve_real_chapters(
        db,
        novel_id=novel_id,
        cutoff_chapter=cutoff,
        chapter_start=chapter_start,
        chapter_end=chapter_end,
        chapter_ids=chapter_ids,
    )
    plan = build_chapter_batch_plan(
        owner_id=owner_id,
        novel_id=novel_id,
        cutoff_chapter=cutoff,
        chapters=refs,
        concurrency_window=window,
        skill_version_id=version.id,
    )
    requested = {
        "chapter_start": chapter_start,
        "chapter_end": chapter_end,
        "chapter_ids": list(chapter_ids) if chapter_ids is not None else None,
    }
    manifest = _manifest_for_plan(plan, requested=requested)

    all_rows = list(
        (
            await db.scalars(
                select(SkillRun)
                .where(
                    SkillRun.owner_id == owner_id,
                    SkillRun.novel_id == novel_id,
                    SkillRun.origin == CHAPTER_BATCH_ORIGIN,
                )
                .order_by(SkillRun.id.asc())
            )
        ).all()
    )
    runs = [row for row in all_rows if (_batch_manifest(row) or {}).get("batch_id") == plan.batch_id]
    by_chapter = _latest_runs_by_chapter(runs)
    active = sum(1 for row in runs if row.status in ("queued", "running"))
    created_run_ids: list[int] = []
    # Only the explicit resume API may retry failed/cancelled rows, and it must
    # still respect the same bounded window.
    if retry_terminal:
        active, retry_ids = await _create_retry_attempts(
            db,
            runs=runs,
            by_chapter=by_chapter,
            concurrency_window=window,
            active=active,
        )
        created_run_ids.extend(retry_ids)

    for spec in _chapter_run_specs(
        novel_id=novel_id,
        cutoff_chapter=cutoff,
        batch_id=plan.batch_id,
        chapters=refs,
    ):
        if spec.chapter_id in by_chapter:
            continue
        if active >= window:
            break
        input_payload = dict(spec.input)
        run = SkillRun(
            owner_id=owner_id,
            novel_id=novel_id,
            skill_version_id=version.id,
            status="queued",
            input=input_payload,
            input_hash=registry_service.canonical_input_hash(input_payload),
            frozen_manifest={
                **manifest,
                "runtime_manifest_checksum": version.yaml_checksum,
                "connector_versions": connector_versions,
            },
            budget_snapshot=dict(version.budget or {}),
            internal_token_hash=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
            origin=CHAPTER_BATCH_ORIGIN,
        )
        db.add(run)
        await db.flush()
        by_chapter[spec.chapter_id] = run
        runs.append(run)
        active += 1
        created_run_ids.append(run.id)
    await db.flush()
    return _status_for_runs(plan=plan, runs=runs, created_run_ids=created_run_ids)


async def get_chapter_batch_status(
    db: AsyncSession, *, owner_id: int, novel_id: int, batch_id: str
) -> dict[str, Any]:
    rows = list(
        (
            await db.scalars(
                select(SkillRun)
                .where(
                    SkillRun.owner_id == owner_id,
                    SkillRun.novel_id == novel_id,
                    SkillRun.origin == CHAPTER_BATCH_ORIGIN,
                )
                .order_by(SkillRun.id.asc())
            )
        ).all()
    )
    runs = [row for row in rows if (_batch_manifest(row) or {}).get("batch_id") == batch_id]
    if not runs:
        raise ChapterBatchError("chapter batch not found")
    manifest = _batch_manifest(runs[0]) or {}
    ids = list(manifest.get("chapter_ids") or [])
    numbers = list(manifest.get("chapter_numbers") or [])
    plan = ChapterBatchPlan(
        batch_id=batch_id,
        owner_id=owner_id,
        novel_id=novel_id,
        cutoff_chapter=int(manifest.get("cutoff_chapter") or 1),
        concurrency_window=int(manifest.get("concurrency_window") or 1),
        chapters=[ChapterRef(id=int(cid), chapter_number=int(numbers[index])) for index, cid in enumerate(ids)],
        next_window=[],
        pending_chapter_ids=[],
    )
    return _status_for_runs(plan=plan, runs=runs, created_run_ids=[])


async def resume_chapter_batch(
    db: AsyncSession, *, owner_id: int, novel_id: int, batch_id: str
) -> dict[str, Any]:
    rows = list(
        (
            await db.scalars(
                select(SkillRun)
                .where(
                    SkillRun.owner_id == owner_id,
                    SkillRun.novel_id == novel_id,
                    SkillRun.origin == CHAPTER_BATCH_ORIGIN,
                )
                .order_by(SkillRun.id.asc())
            )
        ).all()
    )
    runs = [row for row in rows if (_batch_manifest(row) or {}).get("batch_id") == batch_id]
    if not runs:
        raise ChapterBatchError("chapter batch not found")
    return await _refill_existing_batch(
        db, anchor_run=runs[0], retry_terminal=True
    )


async def continue_chapter_batch_after_finalize(
    sessions: async_sessionmaker[AsyncSession], run_id: int
) -> dict[str, Any]:
    """Materialize one completed chapter artifact and refill its batch window.

    This is the background-task seam called by the finalize endpoint. It never
    retries failed/cancelled rows; only ``resume_chapter_batch`` owns that action.
    Repeated calls are idempotent because ``create_or_resume_chapter_batch`` fills
    only missing capacity and reuses the deterministic batch/chapter identities.
    """

    from app.services.agent_runtime.materialize import materialize_skill_run

    materialization = await materialize_skill_run(sessions, run_id)
    async with sessions.begin() as db:
        run = await db.get(SkillRun, run_id, with_for_update=True)
        if run is None:
            raise ChapterBatchError("chapter batch run not found")
        if run.origin != CHAPTER_BATCH_ORIGIN:
            raise ChapterBatchError("run is not a chapter batch run")
        if run.status != "completed":
            return {
                "materialization": materialization,
                "continuation": "skipped:not_completed",
            }
        result = await _refill_existing_batch(
            db, anchor_run=run, retry_terminal=False
        )
        return {
            "materialization": materialization,
            "continuation": "refilled",
            "batch": result,
        }
