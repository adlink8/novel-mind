"""Phase 28-03 hierarchy coverage tests: gaps, overlaps, boundaries, lineage.

Covers REQ-NM-02/06 and decisions D-01/D-02/D-05/D-06/D-07/D-09:

- Chapter State converges into continuous Arc/Volume/Global candidate ranges.
- Gaps, overlaps and uncertain boundaries stay explicit.
- Hierarchy output is immutable candidate-only with source lineage.
- Outline/Mainline candidates retain lineage and uncertainty and never enter
  Canon by generation alone.

The pure-function tests need no database; the PostgreSQL-backed cases reuse the
builder worker fixture and skip when the CI service is unavailable.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.narrative_memory import NarrativeMemoryVersion
from app.models.narrative_memory_builder import (
    NarrativeMemoryBuildRun,
    NarrativeMemoryBuildStage,
)
from app.services.narrative_memory.arc_planner import (
    ArcCandidateRange,
    ChapterTerminalState,
    EvidenceSummary,
    TerminalState,
    Uncertainty,
    validate_outline_candidate,
)
from app.services.narrative_memory.global_builder import (
    validate_mainline_candidate,
)
from app.services.narrative_memory.hierarchy import (
    HierarchyError,
    assert_blocked_not_complete_fact,
    assert_hierarchy_candidate_only,
    assert_no_arc_overlaps,
    build_hierarchy_candidate,
    chapter_terminal_state_from_status,
    coverage_analysis,
    detect_arc_overlaps,
    lineage_for_chapter,
    validate_hierarchy_candidate,
)
from app.services.narrative_memory.source_manifest import (
    frozen_manifest_from_progress,
    recompute_source_manifest,
    source_manifest_drift_reasons,
)
from tests.integration.conftest import run_alembic
from tests.integration.narrative_memory.test_chapter_state_worker_pg import (
    ControlledTransport,
    _deployment,
    _policy,
    _seed,
)
from tests.integration.narrative_memory.test_arc_worker_pg import _Src


pytestmark = pytest.mark.integration

HEX_A = "a" * 64


def _chapter(
    number: int,
    state: str = "completed",
    reason: str | None = None,
    snapshot: str = HEX_A,
    input_hash: str | None = None,
) -> ChapterTerminalState:
    return ChapterTerminalState(
        chapter_number=number,
        terminal_state=TerminalState(state),
        reason_code=reason,
        source_snapshot_hash=snapshot,
        input_hash=input_hash or f"{number % 16:01x}" * 64,
    )


def _evidence(number: int, claim_count: int = 1) -> EvidenceSummary:
    return EvidenceSummary(
        chapter_number=number,
        claim_count=claim_count,
        mean_confidence=0.9,
        max_uncertainty=Uncertainty.CERTAIN,
        content_hash=HEX_A,
    )


def _arc_chapters(hierarchy) -> set[int]:
    return {
        int(number) for arc in hierarchy.outline.arcs for number in arc.chapter_numbers
    }


def _gap_chapters(hierarchy) -> set[int]:
    return {
        number
        for gap in hierarchy.outline.gaps
        for number in range(gap.chapter_start, gap.chapter_end + 1)
    }


# ---------------------------------------------------------------------------
# Continuous coverage and explicit gaps / boundaries
# ---------------------------------------------------------------------------


def test_window_arcs_continuous_coverage() -> None:
    chapters = [_chapter(number) for number in range(1, 7)]
    hierarchy = build_hierarchy_candidate(
        chapters=chapters, window_size=2, volume_arc_window=2
    )
    assert [arc.stage_key for arc in hierarchy.outline.arcs] == [
        "story_arc:1-2",
        "story_arc:3-4",
        "story_arc:5-6",
    ]
    analysis = coverage_analysis(hierarchy)
    assert analysis["continuous"] is True
    assert analysis["gaps"] == []
    assert analysis["gap_count"] == 0
    assert hierarchy.mainline.global_projection.coverage == "complete"


def test_gaps_and_blocked_remain_explicit() -> None:
    chapters = [
        _chapter(1),
        _chapter(2),
        _chapter(3),
        _chapter(4),
        _chapter(5, state="isolated", reason="provider_transport_error"),
        _chapter(6, state="blocked", reason="dependency_failed"),
        _chapter(7),
        _chapter(8),
    ]
    hierarchy = build_hierarchy_candidate(
        chapters=chapters, window_size=3, volume_arc_window=2
    )
    analysis = coverage_analysis(hierarchy)
    # Only terminal chapters feed arcs; 5/6 are explicit gaps.
    assert _arc_chapters(hierarchy) == {1, 2, 3, 4, 7, 8}
    assert _gap_chapters(hierarchy) == {5, 6}
    assert analysis["gap_count"] == 2
    assert analysis["continuous"] is False
    gap = next(g for g in hierarchy.outline.gaps if g.chapter_start == 5)
    assert gap.chapter_end == 6
    assert gap.terminal_states == ("isolated", "blocked")
    assert gap.reason_codes == ("provider_transport_error", "dependency_failed")
    # Global covers the full snapshot range but stays partial.
    global_projection = hierarchy.mainline.global_projection
    assert global_projection.chapter_start == 1
    assert global_projection.chapter_end == 8
    assert global_projection.coverage == "partial"


def test_boundary_uncertainty_adjacent_gap() -> None:
    chapters = [
        _chapter(1),
        _chapter(2),
        _chapter(3, state="isolated", reason="provider_transport_error"),
        _chapter(4),
    ]
    hierarchy = build_hierarchy_candidate(chapters=chapters, window_size=5)
    arc_12 = next(arc for arc in hierarchy.outline.arcs if arc.chapter_start == 1)
    arc_4 = next(arc for arc in hierarchy.outline.arcs if arc.chapter_start == 4)
    end_bounds = [b for b in arc_12.boundary_uncertainties if b.side == "end"]
    assert end_bounds and end_bounds[0].reason == "adjacent_gap"
    start_bounds = [b for b in arc_4.boundary_uncertainties if b.side == "start"]
    assert start_bounds and start_bounds[0].reason == "adjacent_gap"
    # Boundaries touching a gap keep the arc at least uncertain.
    assert arc_12.uncertainty in {Uncertainty.UNCERTAIN, Uncertainty.UNKNOWN}


def test_evidence_backed_boundary_split() -> None:
    chapters = [_chapter(number) for number in range(1, 5)]
    evidence = {
        1: _evidence(1, claim_count=3),
        2: _evidence(2, claim_count=10),
        3: _evidence(3, claim_count=1),
        4: _evidence(4, claim_count=3),
    }
    hierarchy = build_hierarchy_candidate(
        chapters=chapters, evidence_by_chapter=evidence, window_size=5
    )
    # Strong claim-density delta between 2 and 3 forces an evidence-backed split
    # even though the window would otherwise keep the run whole.
    arc_12 = next(arc for arc in hierarchy.outline.arcs if arc.chapter_end == 2)
    end_bounds = [b for b in arc_12.boundary_uncertainties if b.side == "end"]
    assert end_bounds and end_bounds[0].reason == "evidence_delta"
    assert any(arc.chapter_start == 3 for arc in hierarchy.outline.arcs)
    # The evidence lineage is retained on the arc.
    assert "2" in arc_12.evidence_lineage
    assert arc_12.evidence_lineage["2"]["claim_count"] == 10


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------


def test_hierarchy_lineage_queryable() -> None:
    chapters = [_chapter(number) for number in range(1, 5)]
    hierarchy = build_hierarchy_candidate(
        chapters=chapters, window_size=2, volume_arc_window=2
    )
    chain = lineage_for_chapter(hierarchy, 3)
    assert [entry["kind"] for entry in chain] == [
        "story_arc",
        "volume",
        "global_story",
    ]
    assert chain[0]["stage_key"] == "story_arc:3-4"
    assert chain[1]["stage_key"] == "volume:1-4"
    assert chain[2]["stage_key"] == "global_story:book"
    for entry in chain:
        assert entry["source_snapshot_hash"] == HEX_A
        assert len(entry["hierarchy_checksum"]) == 64
        assert entry["input_hash"]
        assert entry["uncertainty"]


def test_gap_chapter_lineage_is_gap() -> None:
    chapters = [
        _chapter(1),
        _chapter(2),
        _chapter(3, state="blocked", reason="dependency_failed"),
        _chapter(4),
    ]
    hierarchy = build_hierarchy_candidate(chapters=chapters)
    chain = lineage_for_chapter(hierarchy, 3)
    assert len(chain) == 1
    assert chain[0]["kind"] == "gap"
    assert chain[0]["chapter_number"] == 3
    # A completed chapter still resolves to the full parent chain.
    full = lineage_for_chapter(hierarchy, 2)
    assert [entry["kind"] for entry in full] == [
        "story_arc",
        "volume",
        "global_story",
    ]


def test_outline_and_mainline_retain_lineage_and_uncertainty() -> None:
    chapters = [_chapter(number) for number in range(1, 6)]
    evidence = {number: _evidence(number, claim_count=number) for number in range(1, 6)}
    hierarchy = build_hierarchy_candidate(
        chapters=chapters,
        evidence_by_chapter=evidence,
        window_size=2,
        volume_arc_window=2,
    )
    outline = hierarchy.outline
    assert outline.candidate_status == "candidate"
    assert outline.source_snapshot_hash == HEX_A
    assert outline.lineage["source_snapshot_hash"] == HEX_A
    assert all(len(arc.input_hash) == 64 for arc in outline.arcs)
    assert all(arc.uncertainty in Uncertainty for arc in outline.arcs)
    mainline = hierarchy.mainline
    assert mainline.candidate_status == "candidate"
    assert mainline.source_snapshot_hash == HEX_A
    assert mainline.volumes
    assert mainline.global_projection.child_stage_keys == tuple(
        volume.stage_key for volume in mainline.volumes
    )
    assert mainline.lineage["volume_input_hashes"]
    validate_outline_candidate(outline)
    validate_mainline_candidate(mainline)


# ---------------------------------------------------------------------------
# Immutable candidate-only + no Canon
# ---------------------------------------------------------------------------


def test_candidate_only_no_canon_markers() -> None:
    chapters = [_chapter(number) for number in range(1, 5)]
    hierarchy = build_hierarchy_candidate(chapters=chapters)
    assert hierarchy.candidate_status == "candidate"
    assert hierarchy.outline.candidate_status == "candidate"
    assert hierarchy.mainline.candidate_status == "candidate"
    assert_hierarchy_candidate_only(hierarchy)
    validate_hierarchy_candidate(hierarchy)


def test_checksum_deterministic() -> None:
    chapters = [_chapter(number) for number in range(1, 6)]
    first = build_hierarchy_candidate(
        chapters=chapters, window_size=2, volume_arc_window=2
    )
    second = build_hierarchy_candidate(
        chapters=list(reversed(chapters)), window_size=2, volume_arc_window=2
    )
    assert first.checksum == second.checksum
    assert first.outline.checksum == second.outline.checksum
    assert first.mainline.checksum == second.mainline.checksum
    # Evidence is part of lineage: adding it changes the checksum.
    evidence = {number: _evidence(number) for number in range(1, 6)}
    with_evidence = build_hierarchy_candidate(
        chapters=chapters,
        evidence_by_chapter=evidence,
        window_size=2,
        volume_arc_window=2,
    )
    assert with_evidence.checksum != first.checksum


def test_blocked_never_complete_fact() -> None:
    chapters = [
        _chapter(1),
        _chapter(2, state="blocked", reason="dependency_failed"),
        _chapter(3),
    ]
    hierarchy = build_hierarchy_candidate(chapters=chapters)
    assert 2 not in _arc_chapters(hierarchy)
    assert 2 in _gap_chapters(hierarchy)
    assert_hierarchy_candidate_only(hierarchy)
    assert_blocked_not_complete_fact(hierarchy)


def test_all_blocked_book_still_candidate() -> None:
    chapters = [
        _chapter(number, state="blocked", reason="dependency_failed")
        for number in range(1, 4)
    ]
    hierarchy = build_hierarchy_candidate(chapters=chapters)
    assert hierarchy.outline.arcs == ()
    assert _gap_chapters(hierarchy) == {1, 2, 3}
    global_projection = hierarchy.mainline.global_projection
    assert global_projection.coverage == "empty"
    assert global_projection.child_stage_keys == ()
    # Blocked input must not be disguised as fact, and stays candidate-only.
    assert_blocked_not_complete_fact(hierarchy)
    assert_hierarchy_candidate_only(hierarchy)


def test_validate_hierarchy_fails_on_candidate_status_change() -> None:
    chapters = [_chapter(number) for number in range(1, 4)]
    hierarchy = build_hierarchy_candidate(chapters=chapters)
    # model_copy bypasses pydantic validation, so the checksum guard is the
    # fail-closed boundary against any attempted candidate -> Canon cutover.
    forged = hierarchy.model_copy(update={"candidate_status": "canon"})
    with pytest.raises(HierarchyError):
        validate_hierarchy_candidate(forged)
    forged_range = hierarchy.model_copy(update={"chapter_max": 99})
    with pytest.raises(HierarchyError):
        validate_hierarchy_candidate(forged_range)


# ---------------------------------------------------------------------------
# Overlap handling
# ---------------------------------------------------------------------------


def test_arc_overlap_detection_is_explicit() -> None:
    arc_1 = ArcCandidateRange(
        stage_key="story_arc:1-3",
        chapter_start=1,
        chapter_end=3,
        chapter_numbers=(1, 2, 3),
        coverage="complete",
        uncertainty=Uncertainty.CERTAIN,
        confidence=0.9,
        input_hash=HEX_A,
    )
    arc_2 = ArcCandidateRange(
        stage_key="story_arc:3-4",
        chapter_start=3,
        chapter_end=4,
        chapter_numbers=(3, 4),
        coverage="complete",
        uncertainty=Uncertainty.CERTAIN,
        confidence=0.9,
        input_hash="b" * 64,
    )
    overlaps = detect_arc_overlaps([arc_1, arc_2])
    assert len(overlaps) == 1
    assert overlaps[0].chapter_start == 3
    assert overlaps[0].arc_keys == ("story_arc:1-3", "story_arc:3-4")
    with pytest.raises(HierarchyError):
        assert_no_arc_overlaps([arc_1, arc_2])
    # A generated hierarchy never overlaps.
    chapters = [_chapter(number) for number in range(1, 5)]
    hierarchy = build_hierarchy_candidate(chapters=chapters, window_size=2)
    assert_no_arc_overlaps(hierarchy.outline.arcs)
    assert hierarchy.outline.overlaps == ()


# ---------------------------------------------------------------------------
# Terminal state mapping
# ---------------------------------------------------------------------------


def test_terminal_state_mapping_covers_all_durable_statuses() -> None:
    assert chapter_terminal_state_from_status("completed") is TerminalState.COMPLETED
    assert chapter_terminal_state_from_status("failed") is TerminalState.ISOLATED
    assert chapter_terminal_state_from_status("paused_budget") is TerminalState.ISOLATED
    assert (
        chapter_terminal_state_from_status("blocked_dependency")
        is TerminalState.BLOCKED
    )
    with pytest.raises(HierarchyError):
        chapter_terminal_state_from_status("pending")


# ---------------------------------------------------------------------------
# PostgreSQL-backed: hierarchy derived from real terminal Chapter State
# ---------------------------------------------------------------------------


@pytest.fixture
async def builder_env(empty_postgres: str, pg_async_url: str):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_async_engine(pg_async_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            user, novel, version, _chapters, _report = await _seed(session)
        yield {
            "factory": factory,
            "owner_id": user.id,
            "novel_id": novel.id,
            "version_id": version.id,
        }
    finally:
        await engine.dispose()


async def _terminal_chapters(
    factory, *, version_id: int, run_id: int
) -> list[ChapterTerminalState]:
    """Rehydrate terminal Chapter State from the durable stage rows."""
    async with factory() as session:
        version = await session.get(NarrativeMemoryVersion, version_id)
        assert version is not None
        stages = (
            await session.scalars(
                select(NarrativeMemoryBuildStage).where(
                    NarrativeMemoryBuildStage.run_id == run_id,
                    NarrativeMemoryBuildStage.stage_kind == "chapter_state",
                )
            )
        ).all()
    states: list[ChapterTerminalState] = []
    for stage in stages:
        if stage.chapter_start is None:
            continue
        states.append(
            ChapterTerminalState(
                chapter_number=int(stage.chapter_start),
                terminal_state=chapter_terminal_state_from_status(stage.status),
                reason_code=stage.reason_code,
                source_snapshot_hash=version.source_snapshot_hash,
            )
        )
    return states


@pytest.mark.asyncio
async def test_db_hierarchy_consistent_with_frozen_manifest(builder_env) -> None:
    transport = ControlledTransport()
    worker = NarrativeMemoryBuilderWorkerForTest(builder_env, transport)
    run_id = await worker.start_run(builder_env)
    await worker.process_run(builder_env)

    chapters = await _terminal_chapters(
        builder_env["factory"],
        version_id=builder_env["version_id"],
        run_id=run_id,
    )
    assert len(chapters) == 3
    assert all(
        chapter.terminal_state is TerminalState.COMPLETED for chapter in chapters
    )
    hierarchy = build_hierarchy_candidate(
        chapters=chapters,
        window_size=2,
        volume_arc_window=2,
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
    )
    analysis = coverage_analysis(hierarchy)
    assert analysis["continuous"] is True
    assert analysis["gap_count"] == 0
    assert_hierarchy_candidate_only(hierarchy)

    # Manifests are DB-recomputable (D-05): frozen == recomputed while clean.
    async with builder_env["factory"]() as session:
        version = await session.get(NarrativeMemoryVersion, builder_env["version_id"])
        assert version is not None
        run = await session.scalar(
            select(NarrativeMemoryBuildRun).where(NarrativeMemoryBuildRun.id == run_id)
        )
        frozen = frozen_manifest_from_progress(run.progress)
        assert frozen is not None
        recomputed = await recompute_source_manifest(session, version=version)
        assert source_manifest_drift_reasons(frozen, recomputed) == []


@pytest.mark.asyncio
async def test_db_blocked_chapter_stays_gap_in_hierarchy(builder_env) -> None:
    transport = ControlledTransport()
    transport.fail_chapters = {2}
    worker = NarrativeMemoryBuilderWorkerForTest(builder_env, transport)
    run_id = await worker.start_run(builder_env)
    await worker.process_run(builder_env)

    chapters = await _terminal_chapters(
        builder_env["factory"],
        version_id=builder_env["version_id"],
        run_id=run_id,
    )
    by_number = {chapter.chapter_number: chapter for chapter in chapters}
    assert by_number[2].terminal_state is TerminalState.ISOLATED
    hierarchy = build_hierarchy_candidate(
        chapters=chapters,
        window_size=2,
        volume_arc_window=2,
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
    )
    analysis = coverage_analysis(hierarchy)
    assert 2 in analysis["gap_chapters"]
    assert analysis["continuous"] is False
    assert 2 not in _arc_chapters(hierarchy)
    assert hierarchy.mainline.global_projection.coverage == "partial"
    assert_blocked_not_complete_fact(hierarchy)
    assert_hierarchy_candidate_only(hierarchy)


class NarrativeMemoryBuilderWorkerForTest:
    """Thin wrapper that wires the worker the same way sibling tests do."""

    def __init__(self, env: dict, transport: ControlledTransport) -> None:
        from app.services.narrative_memory.builder_worker import (
            NarrativeMemoryBuilderWorker,
        )

        self._worker = NarrativeMemoryBuilderWorker(
            env["factory"],
            inventory_source=_Src(env["factory"]),
            transport=transport,
            deployment=_deployment(),
        )
        self._env = env

    async def start_run(self, env: dict) -> int:
        return await self._worker.start_run(
            owner_id=env["owner_id"],
            novel_id=env["novel_id"],
            version_id=env["version_id"],
            run_policy=_policy(),
        )

    async def process_run(self, env: dict) -> None:
        await self._worker.process_run(
            owner_id=env["owner_id"],
            novel_id=env["novel_id"],
            version_id=env["version_id"],
        )
