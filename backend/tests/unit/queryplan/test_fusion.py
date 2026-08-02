"""Phase 26-02 fusion tests (REQ-QP-05, D-05/D-06/D-08/D-09/D-12/D-14).

Covers: deterministic single-source comparison, explainable changes with the
dimension set, missing dimensions that never become empty-success or uncited
facts, candidate-only exclusion from evidence, cutoff enforcement, budget
propagation, owner/snapshot scope escape and provenance preservation.
"""

from __future__ import annotations

import pytest

from app.services.queryplan.adapters import (
    ChapterRecord,
    DimensionResult,
    HeuristicCandidate,
    SourceSnapshot,
    chapter_content_hash,
)
from app.services.queryplan.fusion import (
    FusedEvidence,
    FusionError,
    FusionResult,
    fuse_dimension_results,
    fusion_checksum,
)
from app.services.queryplan.schemas import (
    AvailabilityStatus,
    CutoffMode,
    EvidenceRef,
    FallbackStage,
    QueryDimension,
)

pytestmark = pytest.mark.unit

HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_SNAPSHOT = "c" * 64
HEX_OTHER = "d" * 64

RAW = QueryDimension.RAW_TEXT
REL = QueryDimension.RELATIONS
TIMELINE = QueryDimension.TIMELINE
CHAR = QueryDimension.CHARACTER_STATE
WORLD = QueryDimension.WORLD_RULES


def make_ref(
    *,
    chapter_id: int = 1,
    chapter_number: int = 1,
    start: int = 0,
    end: int = 10,
    content_hash: str = HEX_A,
    snapshot_hash: str = HEX_SNAPSHOT,
) -> EvidenceRef:
    return EvidenceRef(
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        source_start=start,
        source_end=end,
        content_hash=content_hash,
        source_snapshot_hash=snapshot_hash,
    )


def make_candidate(
    *,
    dimension: QueryDimension = CHAR,
    chapter_number: int = 1,
    start: int = 0,
    end: int = 8,
    snapshot_hash: str = HEX_SNAPSHOT,
) -> HeuristicCandidate:
    return HeuristicCandidate(
        dimension=dimension,
        chapter_id=chapter_number,
        chapter_number=chapter_number,
        source_start=start,
        source_end=end,
        content_hash=HEX_A,
        source_snapshot_hash=snapshot_hash,
        snippet="候选片段",
        evidence_eligible=False,
    )


def make_chapter(chapter_number: int, content: str) -> ChapterRecord:
    return ChapterRecord(
        chapter_id=chapter_number,
        chapter_number=chapter_number,
        content=content,
        content_hash=chapter_content_hash(content),
    )


def make_source(
    *,
    chapters: tuple[ChapterRecord, ...] = (
        make_chapter(1, "第一章"),
        make_chapter(2, "第二章"),
        make_chapter(3, "第三章"),
    ),
    snapshot_hash: str = HEX_SNAPSHOT,
) -> SourceSnapshot:
    return SourceSnapshot(
        owner_id=1,
        novel_id=1,
        version_id=1,
        snapshot_hash=snapshot_hash,
        chapters=chapters,
    )


def make_available(
    dimension: QueryDimension,
    refs: tuple[EvidenceRef, ...],
    *,
    provenance: str = "exact_reader_v1",
) -> DimensionResult:
    return DimensionResult(
        dimension=dimension,
        status=AvailabilityStatus.AVAILABLE,
        reason="reader_ok",
        provenance=provenance,
        stage=FallbackStage.EXACT_READER,
        refs=refs,
    )


def make_heuristic(
    dimension: QueryDimension,
    candidates: tuple[HeuristicCandidate, ...],
) -> DimensionResult:
    return DimensionResult(
        dimension=dimension,
        status=AvailabilityStatus.PARTIAL,
        reason="heuristic_candidate_only",
        provenance="deterministic_heuristic_v1",
        stage=FallbackStage.DETERMINISTIC_HEURISTIC,
        candidates=candidates,
    )


def make_unavailable(dimension: QueryDimension) -> DimensionResult:
    return DimensionResult(
        dimension=dimension,
        status=AvailabilityStatus.UNAVAILABLE,
        reason="dimension_unavailable",
        provenance="deterministic_contract_v1",
        stage=FallbackStage.STABLE_UNAVAILABLE,
    )


def ref_keys(fused: tuple[FusedEvidence, ...]) -> list[tuple[int, int, int, str]]:
    return [
        (
            fe.ref.chapter_number,
            fe.ref.source_start,
            fe.ref.source_end,
            fe.ref.content_hash,
        )
        for fe in fused
    ]


# ---------------------------------------------------------------------------
# Single-source comparability + determinism (D-06)
# ---------------------------------------------------------------------------


def test_single_source_baseline_reproduces_dimension_exactly():
    refs = (
        make_ref(chapter_number=2, start=5, end=15, content_hash=HEX_B),
        make_ref(chapter_number=1, start=0, end=10),
    )
    result = make_available(RAW, refs)
    fused = fuse_dimension_results(
        [result], source=make_source(), through_chapter=3
    )
    assert fused.status == AvailabilityStatus.AVAILABLE
    assert fused.reason == "all_dimensions_available"
    assert fused.evidence_count == 2
    assert ref_keys(fused.fused_evidence) == [
        (1, 0, 10, HEX_A),
        (2, 5, 15, HEX_B),
    ]
    for fe in fused.fused_evidence:
        assert fe.dimensions == (RAW,)
        assert fe.provenance == ("exact_reader_v1",)
        assert fe.stages == ("exact_reader",)
    assert fused.candidate_count == 0
    assert len(fused.checksum) == 64


def test_fusion_is_deterministic():
    refs = (make_ref(), make_ref(chapter_number=2, start=5, end=15))
    kwargs = dict(results=[make_available(RAW, refs)], source=make_source(), through_chapter=3)
    first = fuse_dimension_results(**kwargs)
    second = fuse_dimension_results(**kwargs)
    assert first == second
    assert first.checksum == second.checksum
    # Checksum recomputed externally matches the embedded one.
    assert first.checksum == fusion_checksum(first)


def test_fusion_owner_version_preserved_from_source():
    source = SourceSnapshot(
        owner_id=7,
        novel_id=9,
        version_id=11,
        snapshot_hash=HEX_SNAPSHOT,
        chapters=(),
    )
    result = make_available(RAW, (make_ref(),))
    fused = fuse_dimension_results([result], source=source, through_chapter=3)
    assert fused.owner_id == 7
    assert fused.novel_id == 9
    assert fused.version_id == 11


# ---------------------------------------------------------------------------
# Explainable change with the dimension set (D-06)
# ---------------------------------------------------------------------------


def test_dimension_set_change_is_explainable_and_reversible():
    r1 = make_ref(chapter_number=1, start=0, end=10)
    r2 = make_ref(chapter_number=2, start=5, end=15, content_hash=HEX_B)
    r3 = make_ref(chapter_number=3, start=0, end=12, content_hash=HEX_OTHER)
    a = make_available(RAW, (r1, r2), provenance="exact_reader_v1")
    b = make_available(REL, (r2, r3), provenance="exact_reader_v2")

    baseline = fuse_dimension_results([a], source=make_source(), through_chapter=3)
    combined = fuse_dimension_results([a, b], source=make_source(), through_chapter=3)

    assert baseline.checksum != combined.checksum
    assert combined.evidence_count == 3
    # Adding a dimension adds its refs; shared refs are merged not duplicated.
    assert ref_keys(combined.fused_evidence) == [
        (1, 0, 10, HEX_A),
        (2, 5, 15, HEX_B),
        (3, 0, 12, HEX_OTHER),
    ]
    merged = next(
        fe for fe in combined.fused_evidence if fe.ref.content_hash == HEX_B
    )
    assert merged.dimensions == (RAW, REL)
    assert merged.provenance == ("exact_reader_v1", "exact_reader_v2")
    assert merged.stages == ("exact_reader",)

    # Reversing the set restores the single-source baseline.
    restored = fuse_dimension_results([b], source=make_source(), through_chapter=3)
    assert ref_keys(restored.fused_evidence) == [
        (2, 5, 15, HEX_B),
        (3, 0, 12, HEX_OTHER),
    ]


# ---------------------------------------------------------------------------
# Missing dimensions never empty-success (D-05/D-09)
# ---------------------------------------------------------------------------


def test_all_unavailable_is_never_empty_success():
    fused = fuse_dimension_results(
        [make_unavailable(CHAR), make_unavailable(WORLD)],
        source=make_source(),
        through_chapter=3,
    )
    assert fused.status == AvailabilityStatus.UNAVAILABLE
    assert fused.reason == "all_dimensions_unavailable"
    assert fused.evidence_count == 0
    assert fused.fused_evidence == ()
    assert fused.candidate_recall == ()
    assert fused.provenance_chain == ("deterministic_contract_v1",)


def test_mixed_available_unavailable_is_partial_not_available():
    a = make_available(RAW, (make_ref(),))
    fused = fuse_dimension_results(
        [a, make_unavailable(WORLD)],
        source=make_source(),
        through_chapter=3,
    )
    assert fused.status == AvailabilityStatus.PARTIAL
    assert fused.reason == "partial_coverage"
    assert fused.evidence_count == 1


def test_heuristic_only_fusion_never_creates_uncited_facts():
    candidates = (make_candidate(chapter_number=1), make_candidate(chapter_number=2))
    fused = fuse_dimension_results(
        [make_heuristic(CHAR, candidates)],
        source=make_source(),
        through_chapter=3,
    )
    assert fused.status == AvailabilityStatus.PARTIAL
    assert fused.fused_evidence == ()
    assert fused.evidence_count == 0
    assert fused.candidate_count == 2
    assert len(fused.candidate_recall) == 2
    assert all(c.evidence_eligible is False for c in fused.candidate_recall)


# ---------------------------------------------------------------------------
# Candidate-only never enters evidence (D-08/D-15)
# ---------------------------------------------------------------------------


def test_candidates_never_enter_fused_evidence():
    ref = make_ref(chapter_number=1)
    candidate = make_candidate(chapter_number=2, start=0, end=8)
    fused = fuse_dimension_results(
        [make_available(RAW, (ref,)), make_heuristic(CHAR, (candidate,))],
        source=make_source(),
        through_chapter=3,
    )
    assert fused.evidence_count == 1
    assert ref_keys(fused.fused_evidence) == [(1, 0, 10, HEX_A)]
    assert fused.candidate_count == 1
    assert fused.candidate_recall[0].evidence_eligible is False
    assert fused.status == AvailabilityStatus.PARTIAL
    assert fused.reason == "partial_coverage"
    # Provenance chain preserves both the reader and the heuristic provenance.
    assert fused.provenance_chain == (
        "deterministic_heuristic_v1",
        "exact_reader_v1",
    )


def test_fused_evidence_carries_only_leaf_fields():
    fused = fuse_dimension_results(
        [make_available(RAW, (make_ref(),))],
        source=make_source(),
        through_chapter=3,
    )
    assert len(fused.fused_evidence) == 1
    payload = fused.fused_evidence[0].ref.model_dump(mode="json")
    assert set(payload) == {
        "chapter_id",
        "chapter_number",
        "source_start",
        "source_end",
        "content_hash",
        "source_snapshot_hash",
    }
    # No summary / score / routing metadata / chat text can be evidence (D-08).
    assert "summary" not in payload
    assert "score" not in payload


# ---------------------------------------------------------------------------
# Cutoff enforcement (D-12)
# ---------------------------------------------------------------------------


def test_cutoff_filters_evidence_and_candidates_in_reading_progress():
    future_ref = make_ref(chapter_number=9, start=0, end=10)
    future_candidate = make_candidate(chapter_number=9, start=0, end=8)
    fused = fuse_dimension_results(
        [
            make_available(RAW, (make_ref(chapter_number=2), future_ref)),
            make_heuristic(CHAR, (future_candidate,)),
        ],
        source=make_source(),
        through_chapter=3,
    )
    assert ref_keys(fused.fused_evidence) == [(2, 0, 10, HEX_A)]
    assert fused.candidate_recall == ()


def test_whole_book_keeps_beyond_cutoff():
    future_ref = make_ref(chapter_number=9, start=0, end=10)
    future_candidate = make_candidate(chapter_number=9, start=0, end=8)
    fused = fuse_dimension_results(
        [make_available(RAW, (future_ref,)), make_heuristic(CHAR, (future_candidate,))],
        source=make_source(),
        through_chapter=3,
        cutoff_mode=CutoffMode.WHOLE_BOOK.value,
    )
    assert ref_keys(fused.fused_evidence) == [(9, 0, 10, HEX_A)]
    assert fused.candidate_count == 1


# ---------------------------------------------------------------------------
# Budget propagation
# ---------------------------------------------------------------------------


def test_budget_is_applied_deterministically():
    refs = (
        make_ref(chapter_number=1, start=0, end=10),
        make_ref(chapter_number=2, start=5, end=15, content_hash=HEX_B),
    )
    unbounded = fuse_dimension_results(
        [make_available(RAW, refs)], source=make_source(), through_chapter=3
    )
    assert unbounded.evidence_count == 2
    assert unbounded.exceeded_budget is False
    assert unbounded.budget is None

    capped = fuse_dimension_results(
        [make_available(RAW, refs)],
        source=make_source(),
        through_chapter=3,
        budget=1,
    )
    assert capped.evidence_count == 2  # count keeps the pre-truncation total
    assert len(capped.fused_evidence) == 1
    assert capped.exceeded_budget is True
    # Deterministic truncation keeps the first ref in sort order.
    assert ref_keys(capped.fused_evidence) == [(1, 0, 10, HEX_A)]

    empty = fuse_dimension_results(
        [make_available(RAW, refs)],
        source=make_source(),
        through_chapter=3,
        budget=0,
    )
    assert empty.fused_evidence == ()
    assert empty.exceeded_budget is True

    with pytest.raises(FusionError):
        fuse_dimension_results(
            [make_available(RAW, refs)],
            source=make_source(),
            through_chapter=3,
            budget=-1,
        )


# ---------------------------------------------------------------------------
# Owner / snapshot scope escape (D-12/D-14)
# ---------------------------------------------------------------------------


def test_ref_escaping_snapshot_raises():
    ref = make_ref(snapshot_hash=HEX_OTHER)
    with pytest.raises(FusionError):
        fuse_dimension_results(
            [make_available(RAW, (ref,))],
            source=make_source(),
            through_chapter=3,
        )


def test_candidate_escaping_snapshot_raises():
    candidate = make_candidate(snapshot_hash=HEX_OTHER)
    with pytest.raises(FusionError):
        fuse_dimension_results(
            [make_heuristic(CHAR, (candidate,))],
            source=make_source(),
            through_chapter=3,
        )


def test_empty_dimension_set_raises():
    with pytest.raises(FusionError):
        fuse_dimension_results([], source=make_source(), through_chapter=3)


def test_invalid_cutoff_mode_raises():
    with pytest.raises(ValueError):
        fuse_dimension_results(
            [make_unavailable(CHAR)],
            source=make_source(),
            through_chapter=3,
            cutoff_mode="not_a_mode",
        )


# ---------------------------------------------------------------------------
# Stability across repeated runs
# ---------------------------------------------------------------------------


def test_status_and_reason_stable_across_runs():
    refs = (make_ref(),)
    kwargs = dict(
        results=[
            make_available(RAW, refs),
            make_unavailable(WORLD),
            make_heuristic(CHAR, (make_candidate(),)),
        ],
        source=make_source(),
        through_chapter=3,
    )
    first = fuse_dimension_results(**kwargs)
    second = fuse_dimension_results(**kwargs)
    assert first.status == second.status == AvailabilityStatus.PARTIAL
    assert first.reason == second.reason == "partial_coverage"
    assert first.checksum == second.checksum


def test_fused_evidence_sort_order_is_deterministic():
    refs = (
        make_ref(chapter_number=3, start=20, end=30),
        make_ref(chapter_number=1, start=0, end=10),
        make_ref(chapter_number=2, start=5, end=15, content_hash=HEX_B),
    )
    fused = fuse_dimension_results(
        [make_available(RAW, refs)], source=make_source(), through_chapter=10
    )
    assert ref_keys(fused.fused_evidence) == [
        (1, 0, 10, HEX_A),
        (2, 5, 15, HEX_B),
        (3, 20, 30, HEX_A),
    ]
    assert isinstance(fused, FusionResult)
