"""Cross-dimension closure and one-click candidate analysis (Phase 28-04).

REQ-NM-03/04, D-02/D-03/D-04/D-06/D-07/D-10: closes the timeline, relationship,
clue, character and world dimensions into an immutable ``CandidateManifest``
where every ``DimensionResult`` reports available/partial/blocked under one
shared snapshot/cutoff/owner/version/budget/lineage parity contract.

One-click analysis persists durable progress onto the existing build-run
``progress`` JSONB (never an active pointer) and emits an SSE-envelope
notification for the existing Agent SSE/Job transport. The DB checkpoint is the
only authority after a reconnect; nothing here restores ``/analyze/stream``.
"""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.narrative_memory import (
    NarrativeMemoryClaim,
    NarrativeMemoryNode,
    NarrativeMemoryVersion,
)
from app.models.narrative_memory_builder import (
    NarrativeMemoryBuildRun,
    NarrativeMemoryBuildStage,
)
from app.services.narrative_memory.builder_contracts import (
    BuilderFrozenModel,
    SourceStatus,
)
from app.services.narrative_memory.builder_repository import BuilderRepository
from app.services.narrative_memory.contracts import (
    CANDIDATE_MANIFEST_SCHEMA_VERSION,
    BudgetTotals,
    CandidateManifest,
    DimensionKind,
    DimensionResult,
    DimensionStatus,
    Hash64,
    Key,
    VersionLabel,
    candidate_manifest_checksum,
    dimension_result_checksum,
)
from app.services.narrative_memory.manifest_contract import (
    assert_no_pointer_fields,
    validate_candidate_manifest,
)
from app.services.narrative_memory.optional_sources import load_optional_signals
from app.services.narrative_memory.progress import (
    ProgressNotification,
    build_progress_notification,
    load_durable_progress,
)
from app.services.narrative_memory.recovery import terminal_state_for_status

CLOSURE_SCHEMA_VERSION = "cross-dimension-closure.v1"
CLOSURE_EVENT_TYPE = "narrative_memory.closure"


class ClosureError(ValueError):
    """Fail-closed error for cross-dimension closure."""


class FacetRange(BuilderFrozenModel):
    """One real candidate facet range projected from the run boundary plan."""

    stage_key: Key
    node_kind: VersionLabel
    chapter_start: int
    chapter_end: int
    label: VersionLabel | None = None


class CrossDimensionClosure(BuilderFrozenModel):
    """Immutable one-click analysis report (candidate-only, no pointer)."""

    schema_version: VersionLabel = CLOSURE_SCHEMA_VERSION
    owner_id: int
    novel_id: int
    version_id: int
    version_key: Key
    source_snapshot_hash: Hash64
    cutoff: int
    budget: BudgetTotals
    lineage: dict[str, Any]
    dimensions: tuple[DimensionResult, ...]
    manifest: CandidateManifest
    manifest_checksum: Hash64
    run_id: int | None = None
    run_status: VersionLabel | None = None
    run_reason: VersionLabel | None = None
    resume_count: int = 0
    progress: float
    resumable: bool
    facet_ranges: tuple[FacetRange, ...] = ()
    notifications: tuple[ProgressNotification, ...] = ()
    publication_status: VersionLabel = "candidate_preview"


def _zero_budget() -> BudgetTotals:
    return BudgetTotals(
        calls=0,
        input_tokens=0,
        output_tokens=0,
        cost_usd="0",
        cache_hits=0,
    )


def _budget_from_ledger(totals: dict[str, Any] | None) -> BudgetTotals:
    if not totals:
        return _zero_budget()
    return BudgetTotals(
        calls=int(totals.get("settled_calls") or 0),
        input_tokens=int(totals.get("settled_input_tokens") or 0),
        output_tokens=int(totals.get("settled_output_tokens") or 0),
        cost_usd=str(totals.get("settled_cost_usd") or "0"),
        cache_hits=int(totals.get("cache_hits") or 0),
    )


def _version_lineage(version: NarrativeMemoryVersion) -> dict[str, Any]:
    return {
        "source_snapshot_hash": version.source_snapshot_hash,
        "hierarchy_build_id": version.hierarchy_build_id,
        "hierarchy_checksum": version.hierarchy_checksum,
        "eligibility_report_checksum": version.eligibility_report_checksum,
        "model_lineage": version.model_lineage,
        "prompt_hash": version.prompt_hash,
        "schema_hash": version.schema_hash,
        "decoding_hash": version.decoding_hash,
        "config_hash": version.config_hash,
        "policy_hash": version.policy_hash,
        "owner_id": version.owner_id,
        "novel_id": version.novel_id,
        "version_id": version.id,
        "version_key": version.version_key,
    }


def _status_from_signal(signal) -> tuple[DimensionStatus, str | None, float]:
    """Map an optional-source signal to a stable dimension verdict."""
    if signal.status in {SourceStatus.UNAVAILABLE, SourceStatus.LINEAGE_MISMATCH}:
        return DimensionStatus.BLOCKED, signal.reason_code or signal.status.value, 0.0
    if signal.status == SourceStatus.NON_EMPTY:
        return DimensionStatus.AVAILABLE, None, 1.0
    if signal.status == SourceStatus.HEALTHY_EMPTY:
        return DimensionStatus.PARTIAL, signal.reason_code or "source_empty", 0.5
    return DimensionStatus.BLOCKED, signal.status.value, 0.0


def _chapter_progress(
    stages: Sequence[NarrativeMemoryBuildStage], chapter_numbers: Sequence[int]
) -> float:
    if not chapter_numbers:
        return 0.0
    completed = sum(
        1
        for s in stages
        if s.stage_kind == "chapter_state" and s.status == "completed"
    )
    return round(min(completed / len(chapter_numbers), 1.0), 4)


def _resumable(stages: Sequence[NarrativeMemoryBuildStage]) -> bool:
    return any(
        terminal_state_for_status(s.status) is None
        and s.status in {"pending", "running", "paused"}
        for s in stages
    )


def _build_dimension_result(
    *,
    kind: DimensionKind,
    status: DimensionStatus,
    progress: float,
    version: NarrativeMemoryVersion,
    cutoff: int,
    budget: BudgetTotals,
    lineage: dict[str, Any],
    blocked_reason: str | None = None,
) -> DimensionResult:
    placeholder = DimensionResult(
        dimension=kind,
        status=status,
        progress=progress,
        blocked_reason=blocked_reason,
        source_snapshot_hash=version.source_snapshot_hash,
        cutoff=cutoff,
        owner_id=version.owner_id,
        version_id=version.id,
        version_key=version.version_key,
        budget=budget,
        lineage=lineage,
        checksum="0" * 64,
    )
    return placeholder.model_copy(
        update={"checksum": dimension_result_checksum(placeholder)}
    )


def _has_character_content(claims: Sequence[NarrativeMemoryClaim]) -> bool:
    for claim in claims:
        payload = dict(claim.typed_payload or {})
        kind = payload.get("claim_kind")
        if kind == "relationship_delta":
            return True
        if kind == "entity_state" and payload.get("entity_kind") == "character":
            return True
        if kind == "event_fact":
            return True
    return False


def _has_world_content(claims: Sequence[NarrativeMemoryClaim]) -> bool:
    for claim in claims:
        payload = dict(claim.typed_payload or {})
        kind = payload.get("claim_kind")
        if kind == "world_state_delta":
            return True
        if kind == "open_loop_delta":
            return True
        if kind == "entity_state" and payload.get("entity_kind") in {
            "location",
            "object",
            "faction",
            "world",
        }:
            return True
    return False


def _content_dimension_status(
    *,
    has_content: bool,
    stages: Sequence[NarrativeMemoryBuildStage],
    chapter_numbers: Sequence[int],
) -> tuple[DimensionStatus, str | None, float]:
    """Character/world dimensions: content present or chapter-coverage driven."""
    if has_content:
        return DimensionStatus.AVAILABLE, None, 1.0
    progress = _chapter_progress(stages, chapter_numbers)
    if progress > 0.0:
        return (
            DimensionStatus.PARTIAL,
            "partial_coverage",
            progress,
        )
    return DimensionStatus.BLOCKED, "no_builder_run", 0.0


def _facet_ranges_from_run(
    run: NarrativeMemoryBuildRun | None,
) -> tuple[FacetRange, ...]:
    if run is None:
        return ()
    plan = run.boundary_plan or {}
    ranges = plan.get("ranges") or []
    facets = []
    for item in ranges:
        facets.append(
            FacetRange(
                stage_key=str(item["stage_key"]),
                node_kind=str(item.get("node_kind") or "story_arc"),
                chapter_start=int(item["chapter_start"]),
                chapter_end=int(item["chapter_end"]),
                label=item.get("label"),
            )
        )
    return tuple(sorted(facets, key=lambda f: (f.chapter_start, f.stage_key)))


def _cutoff_for(
    *,
    chapter_numbers: Sequence[int],
    nodes: Sequence[NarrativeMemoryNode],
    claims: Sequence[NarrativeMemoryClaim],
) -> int:
    candidates: list[int] = []
    candidates.extend(int(n) for n in chapter_numbers)
    candidates.extend(int(node.chapter_end) for node in nodes)
    candidates.extend(int(claim.visible_from_chapter) for claim in claims)
    if not candidates:
        return 1
    return max(1, max(candidates))


def build_closure_notification(
    closure: CrossDimensionClosure,
) -> ProgressNotification:
    """Notification-only event; the DB checkpoint stays the authority."""
    return ProgressNotification(
        event_type=CLOSURE_EVENT_TYPE,
        payload={
            "run_id": closure.run_id,
            "status": closure.run_status,
            "progress": closure.progress,
            "resumable": closure.resumable,
            "cutoff": closure.cutoff,
            "manifest_checksum": closure.manifest_checksum,
            "dimensions": {
                str(result.dimension): {
                    "status": result.status.value,
                    "progress": result.progress,
                    "blocked_reason": result.blocked_reason,
                }
                for result in closure.dimensions
            },
            "authoritative": True,
        },
    )


def assemble_sse_frames(
    notifications: Sequence[ProgressNotification],
) -> tuple[str, ...]:
    """Frame notifications as Agent-transport SSE chunks (notification only)."""
    return tuple(notification.as_sse() for notification in notifications)


async def _load_claims(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
) -> list[NarrativeMemoryClaim]:
    return list(
        (
            await session.scalars(
                select(NarrativeMemoryClaim).where(
                    NarrativeMemoryClaim.owner_id == owner_id,
                    NarrativeMemoryClaim.novel_id == novel_id,
                    NarrativeMemoryClaim.version_id == version_id,
                )
            )
        ).all()
    )


async def _load_nodes(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
) -> list[NarrativeMemoryNode]:
    return list(
        (
            await session.scalars(
                select(NarrativeMemoryNode).where(
                    NarrativeMemoryNode.owner_id == owner_id,
                    NarrativeMemoryNode.novel_id == novel_id,
                    NarrativeMemoryNode.version_id == version_id,
                )
            )
        ).all()
    )


async def compute_dimension_closure(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
) -> CrossDimensionClosure:
    """Compute the immutable cross-dimension closure from DB authority rows.

    Deterministic and DB-recomputable: the same rows always yield the same
    report. Nothing here writes a pointer (D-07).
    """
    repo = BuilderRepository(session)
    version = await repo.get_version(
        owner_id=owner_id, novel_id=novel_id, version_id=version_id
    )
    run = await repo.get_run(
        owner_id=owner_id, novel_id=novel_id, version_id=version_id
    )
    stages: list[NarrativeMemoryBuildStage] = []
    if run is not None:
        stages = await repo.list_stages(int(run.id))

    chapter_numbers = (
        list((run.progress or {}).get("chapter_numbers") or []) if run else []
    )
    claims = await _load_claims(
        session, owner_id=owner_id, novel_id=novel_id, version_id=version_id
    )
    nodes = await _load_nodes(
        session, owner_id=owner_id, novel_id=novel_id, version_id=version_id
    )
    cutoff = _cutoff_for(
        chapter_numbers=chapter_numbers, nodes=nodes, claims=claims
    )
    ledger = (
        await repo.get_ledger_totals(int(run.id)) if run is not None else None
    )
    budget = _budget_from_ledger(ledger)
    base_lineage = _version_lineage(version)
    # One shared parity lineage: identical for every DimensionResult and the
    # CandidateManifest header. Per-dimension source detail lives inside this
    # shared mapping so cross-dimension parity is strict (D-04/D-07).
    shared_lineage = dict(base_lineage)

    signals = await load_optional_signals(
        session,
        owner_id=owner_id,
        novel_id=novel_id,
        version=version,
    )
    signal_by_kind = {signal.source_kind: signal for signal in signals}

    # Pass 1: derive status/progress/reason per dimension (no model writes yet).
    dimension_details: dict[str, dict[str, Any]] = {}
    statuses: dict[DimensionKind, tuple[DimensionStatus, str | None, float]] = {}
    for kind in DimensionKind:
        signal = signal_by_kind.get(kind.value)
        if signal is not None:
            if signal.lineage:
                dimension_details[kind.value] = {"source_lineage": signal.lineage}
            statuses[kind] = _status_from_signal(signal)
        elif kind == DimensionKind.CHARACTER:
            statuses[kind] = _content_dimension_status(
                has_content=_has_character_content(claims),
                stages=stages,
                chapter_numbers=chapter_numbers,
            )
        elif kind == DimensionKind.WORLD:
            statuses[kind] = _content_dimension_status(
                has_content=_has_world_content(claims),
                stages=stages,
                chapter_numbers=chapter_numbers,
            )
        else:  # pragma: no cover - DimensionKind is closed above
            raise ClosureError(f"unhandled dimension: {kind}")
        details = dict(dimension_details.get(kind.value) or {})
        if statuses[kind][1]:
            details["status_reason"] = statuses[kind][1]
        dimension_details[kind.value] = details
    # Freeze the shared parity lineage before building any immutable result.
    shared_lineage["dimension_details"] = dimension_details

    # Pass 2: build the immutable dimension results on the final shared lineage.
    results = [
        _build_dimension_result(
            kind=kind,
            status=statuses[kind][0],
            progress=statuses[kind][2],
            version=version,
            cutoff=cutoff,
            budget=budget,
            lineage=shared_lineage,
            blocked_reason=(
                statuses[kind][1]
                if statuses[kind][0] == DimensionStatus.BLOCKED
                else None
            ),
        )
        for kind in DimensionKind
    ]

    manifest_placeholder = CandidateManifest(
        source_snapshot_hash=version.source_snapshot_hash,
        cutoff=cutoff,
        owner_id=version.owner_id,
        version_id=version.id,
        version_key=version.version_key,
        budget=budget,
        lineage=shared_lineage,
        dimensions=tuple(results),
        checksum="0" * 64,
    )
    manifest = manifest_placeholder.model_copy(
        update={
            "checksum": candidate_manifest_checksum(manifest_placeholder)
        }
    )
    # Fail closed on checksum/parity/pointer integrity before any consumer use.
    validate_candidate_manifest(manifest)

    overall_progress = (
        round(sum(result.progress for result in results) / len(results), 4)
        if results
        else 0.0
    )
    closure = CrossDimensionClosure(
        owner_id=int(version.owner_id),
        novel_id=int(version.novel_id),
        version_id=int(version.id),
        version_key=version.version_key,
        source_snapshot_hash=version.source_snapshot_hash,
        cutoff=cutoff,
        budget=budget,
        lineage=dict(base_lineage),
        dimensions=tuple(results),
        manifest=manifest,
        manifest_checksum=manifest.checksum,
        run_id=int(run.id) if run else None,
        run_status=run.status if run else None,
        run_reason=run.status_reason if run else None,
        resume_count=int(run.resume_count or 0) if run else 0,
        progress=overall_progress,
        resumable=_resumable(stages),
        facet_ranges=_facet_ranges_from_run(run),
    )
    notification = build_closure_notification(closure)
    return closure.model_copy(update={"notifications": (notification,)})


async def _persist_durable_progress(
    session: AsyncSession,
    closure: CrossDimensionClosure,
) -> None:
    """Persist the closure report onto the build-run progress JSONB.

    This is durable progress, never an active pointer (D-07). Idempotent and
    DB-recomputable; deterministic on recompute.
    """
    repo = BuilderRepository(session)
    run = await repo.get_run(
        owner_id=closure.owner_id,
        novel_id=closure.novel_id,
        version_id=closure.version_id,
    )
    if run is None:
        return
    progress = dict(run.progress or {})
    progress["dimension_statuses"] = {
        str(result.dimension): result.model_dump(mode="json")
        for result in closure.dimensions
    }
    progress["closure"] = {
        "schema_version": closure.schema_version,
        "manifest_checksum": closure.manifest_checksum,
        "progress": closure.progress,
        "resumable": closure.resumable,
        "cutoff": closure.cutoff,
        "facet_ranges": [
            facet.model_dump(mode="json") for facet in closure.facet_ranges
        ],
    }
    assert_no_pointer_fields(progress)
    await repo.update_run_status(int(run.id), status=run.status, progress=progress)
    await session.flush()


async def run_one_click_analysis(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    persist: bool = True,
) -> CrossDimensionClosure:
    """One-click analysis: compute closure and persist durable progress.

    The returned closure carries notification-only SSE frames; callers may
    push them through the existing Agent SSE/Job transport, but the DB
    checkpoint is the only reconnect authority (D-10).
    """
    closure = await compute_dimension_closure(
        session,
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
    )
    if persist:
        await _persist_durable_progress(session, closure)
    return closure


async def analysis_report_with_progress(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    persist: bool = True,
) -> dict[str, Any]:
    """One-click report = closure + DB-authoritative durable progress.

    ``persist=False`` recomputes deterministically without writing anything.
    """
    closure = await run_one_click_analysis(
        session,
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        persist=persist,
    )
    dimension_statuses = {
        str(result.dimension): result.model_dump(mode="json")
        for result in closure.dimensions
    }
    progress = await load_durable_progress(
        session,
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        dimension_statuses=dimension_statuses,
    )
    report = closure.model_dump(mode="json")
    report["durable_progress"] = progress.model_dump(mode="json")
    report["sse_frames"] = list(
        assemble_sse_frames(closure.notifications)
    )
    return report
