"""Phase 26-02 adapter tests (REQ-QP-02 + REQ-QP-05, D-05/D-12/D-14/D-15).

Covers: dimension adapter registry completeness, explicit availability with
stable reasons, the exact-reader -> deterministic-heuristic -> stable-reason
fallback chain, heuristic candidate-only guarantees (never facts/EvidenceRef/
citations), cutoff enforcement, NM candidate-only behavior and deterministic
replayability.
"""

from __future__ import annotations

import pytest

from app.services.queryplan.adapters import (
    CHARACTER_STATE_KEYWORDS,
    DEFAULT_ADAPTERS,
    WORLD_RULES_KEYWORDS,
    ChapterRecord,
    DimensionAdapter,
    DimensionResult,
    HeuristicCandidate,
    HeuristicOutcome,
    ReaderContext,
    ReaderOutcome,
    ReaderUnavailableError,
    SourceSnapshot,
    chapter_content_hash,
    evaluate_dimension_chain,
    extract_heuristic_candidates,
    run_adapter,
    run_plan_adapters,
)
from app.services.queryplan.parser import parse_query_plan
from app.services.queryplan.schemas import (
    AvailabilityStatus,
    EvidenceRef,
    FallbackStage,
    QueryDimension,
    QueryPlan,
)

pytestmark = pytest.mark.unit

HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_SNAPSHOT = "c" * 64


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


def make_chapter(
    *,
    chapter_id: int,
    chapter_number: int,
    content: str,
) -> ChapterRecord:
    return ChapterRecord(
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        content=content,
        content_hash=chapter_content_hash(content),
    )


def make_snapshot(
    *,
    chapters: tuple[ChapterRecord, ...] | None = None,
    owner_id: int = 1,
    novel_id: int = 1,
    version_id: int = 1,
    snapshot_hash: str = HEX_SNAPSHOT,
) -> SourceSnapshot:
    return SourceSnapshot(
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        snapshot_hash=snapshot_hash,
        chapters=chapters or (),
    )


def make_candidate(
    *,
    dimension: QueryDimension = QueryDimension.CHARACTER_STATE,
    chapter_id: int = 1,
    chapter_number: int = 1,
    start: int = 0,
    end: int = 8,
    snippet: str = "片段",
    snapshot_hash: str = HEX_SNAPSHOT,
) -> HeuristicCandidate:
    return HeuristicCandidate(
        dimension=dimension,
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        source_start=start,
        source_end=end,
        content_hash=HEX_A,
        source_snapshot_hash=snapshot_hash,
        snippet=snippet,
        evidence_eligible=False,
    )


class FakeResolver:
    def __init__(self, reader=None, *, reader_id: str | None = None) -> None:
        self._reader = reader
        self._reader_id = reader_id

    async def __call__(self, reader_id: str):
        if self._reader_id is not None and reader_id != self._reader_id:
            return None
        return self._reader


def make_reader(*, refs=None, decline: bool = False, error: bool = False):
    async def reader(context: ReaderContext):
        if decline:
            raise ReaderUnavailableError("reader declined for this scope")
        if error:
            raise RuntimeError("reader exploded")
        if refs is None:
            return None
        return ReaderOutcome(refs=tuple(refs))

    return reader


def reader_payload(**overrides) -> dict:
    base = {
        "intent": "reader",
        "owner_id": 1,
        "novel_id": 1,
        "version_id": 1,
        "question_text": "林安在第一章走进哪里？",
        "reading_progress": {
            "through_chapter": 3,
            "snapshot_hash": HEX_A,
            "full_book_authorized": False,
        },
        "source": "reader_chat",
        "dataset_lineage": "queryplan-questions-v1",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Registry completeness
# ---------------------------------------------------------------------------


def test_default_adapters_cover_every_dimension():
    assert set(DEFAULT_ADAPTERS) == set(QueryDimension)
    for dimension, adapter in DEFAULT_ADAPTERS.items():
        assert adapter.dimension == dimension
        # Every dimension has a reader id or a heuristic fallback (D-15 chain).
        assert adapter.reader_id is not None or adapter.has_heuristic


def test_character_state_and_world_rules_have_no_production_reader():
    """26-01 declared both absent until Phase 27; the chain must still run."""
    character = DEFAULT_ADAPTERS[QueryDimension.CHARACTER_STATE]
    world = DEFAULT_ADAPTERS[QueryDimension.WORLD_RULES]
    assert character.reader_id is None
    assert world.reader_id is None
    assert character.has_heuristic
    assert world.has_heuristic


def test_reader_backed_dimensions_keep_heuristic_fallback():
    for dimension in (
        QueryDimension.RAW_TEXT,
        QueryDimension.EVENTS_CAUSALITY,
        QueryDimension.RELATIONS,
        QueryDimension.TIMELINE,
        QueryDimension.CLUES_FORESHAOWING,
        QueryDimension.NARRATIVE_UNITS,
    ):
        adapter = DEFAULT_ADAPTERS[dimension]
        assert adapter.reader_id is not None
        assert adapter.has_heuristic


# ---------------------------------------------------------------------------
# Pure three-stage chain (D-15)
# ---------------------------------------------------------------------------


def test_reader_hit_marks_available_with_refs():
    refs = (make_ref(), make_ref(chapter_number=2, start=5, end=15, content_hash=HEX_B))
    result = evaluate_dimension_chain(
        QueryDimension.RAW_TEXT,
        reader_outcome=ReaderOutcome(refs=refs),
        heuristic_outcome=None,
    )
    assert result.status == AvailabilityStatus.AVAILABLE
    assert result.reason == "reader_ok"
    assert result.stage == FallbackStage.EXACT_READER
    assert result.refs == refs
    assert result.candidates == ()
    assert result.evidence_eligible is True


def test_reader_zero_hits_is_partial_never_empty_success():
    result = evaluate_dimension_chain(
        QueryDimension.RELATIONS,
        reader_outcome=ReaderOutcome(refs=()),
        heuristic_outcome=HeuristicOutcome(
            candidates=(make_candidate(dimension=QueryDimension.RELATIONS),)
        ),
    )
    assert result.status == AvailabilityStatus.PARTIAL
    assert result.reason == "reader_zero_hits_in_scope"
    assert result.stage == FallbackStage.EXACT_READER
    assert result.refs == ()
    assert result.evidence_eligible is False
    # The reader ran; heuristic is NOT consulted after a zero-hit reader.
    assert result.candidates == ()


def test_heuristic_candidates_are_partial_candidate_only():
    candidates = (make_candidate(),)
    result = evaluate_dimension_chain(
        QueryDimension.CHARACTER_STATE,
        reader_outcome=None,
        heuristic_outcome=HeuristicOutcome(candidates=candidates),
    )
    assert result.status == AvailabilityStatus.PARTIAL
    assert result.reason == "heuristic_candidate_only"
    assert result.stage == FallbackStage.DETERMINISTIC_HEURISTIC
    assert result.refs == ()
    assert result.candidates == candidates
    assert all(c.evidence_eligible is False for c in candidates)
    assert result.evidence_eligible is False


def test_heuristic_without_candidates_ends_dimension_unavailable():
    result = evaluate_dimension_chain(
        QueryDimension.CHARACTER_STATE,
        reader_outcome=None,
        heuristic_outcome=HeuristicOutcome(candidates=()),
    )
    assert result.status == AvailabilityStatus.UNAVAILABLE
    assert result.reason == "dimension_unavailable"
    assert result.stage == FallbackStage.STABLE_UNAVAILABLE


def test_no_reader_no_heuristic_ends_reader_unavailable():
    result = evaluate_dimension_chain(
        QueryDimension.WORLD_RULES,
        reader_outcome=None,
        heuristic_outcome=None,
    )
    assert result.status == AvailabilityStatus.UNAVAILABLE
    assert result.reason == "reader_unavailable"
    assert result.provenance == "deterministic_contract_v1"
    assert result.stage == FallbackStage.STABLE_UNAVAILABLE


def test_chain_is_replayable_and_stable():
    kwargs = dict(
        reader_outcome=None,
        heuristic_outcome=HeuristicOutcome(
            candidates=(make_candidate(), make_candidate(chapter_number=2, start=3))
        ),
    )
    first = evaluate_dimension_chain(QueryDimension.CHARACTER_STATE, **kwargs)
    second = evaluate_dimension_chain(QueryDimension.CHARACTER_STATE, **kwargs)
    assert first == second
    assert first.reason == second.reason
    assert first.status == second.status


# ---------------------------------------------------------------------------
# Deterministic heuristic extraction
# ---------------------------------------------------------------------------


def _state_chapters() -> tuple[ChapterRecord, ...]:
    return (
        make_chapter(
            chapter_id=1,
            chapter_number=1,
            content="林安站在城门前，他的目的是寻找失踪的妹妹。",
        ),
        make_chapter(
            chapter_id=2,
            chapter_number=2,
            content="她怀疑王国的势力正在监视他们。",
        ),
        make_chapter(
            chapter_id=3,
            chapter_number=3,
            content="两人进入地下通道，计划潜入宗门的禁地。",
        ),
        make_chapter(
            chapter_id=4,
            chapter_number=4,
            content="第四章的动机在幕后逐渐浮现。",
        ),
    )


def test_extract_respects_cutoff():
    chapters = _state_chapters()
    candidates = extract_heuristic_candidates(
        dimension=QueryDimension.CHARACTER_STATE,
        chapters=chapters,
        keywords=CHARACTER_STATE_KEYWORDS,
        snapshot_hash=HEX_SNAPSHOT,
        through_chapter=3,
    )
    assert candidates
    assert all(c.chapter_number <= 3 for c in candidates)
    numbers = {c.chapter_number for c in candidates}
    assert 4 not in numbers


def test_extract_is_deterministic():
    chapters = _state_chapters()
    kwargs = dict(
        dimension=QueryDimension.CHARACTER_STATE,
        chapters=chapters,
        keywords=CHARACTER_STATE_KEYWORDS,
        snapshot_hash=HEX_SNAPSHOT,
        through_chapter=10,
    )
    assert extract_heuristic_candidates(**kwargs) == extract_heuristic_candidates(
        **kwargs
    )


def test_extract_candidates_carry_offsets_and_hashes():
    chapters = _state_chapters()
    candidates = extract_heuristic_candidates(
        dimension=QueryDimension.CHARACTER_STATE,
        chapters=chapters,
        keywords=("目的",),
        snapshot_hash=HEX_SNAPSHOT,
        through_chapter=1,
    )
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.chapter_id == 1
    assert candidate.source_start < candidate.source_end
    assert candidate.source_snapshot_hash == HEX_SNAPSHOT
    assert candidate.content_hash == chapters[0].content_hash
    chapter = chapters[0]
    assert (
        chapter.content[candidate.source_start : candidate.source_end]
        == candidate.snippet
    )
    assert candidate.evidence_eligible is False


def test_extract_dedupes_overlapping_keyword_spans():
    chapter = make_chapter(
        chapter_id=1, chapter_number=1, content="他知道王国的动机和目的。"
    )
    candidates = extract_heuristic_candidates(
        dimension=QueryDimension.CHARACTER_STATE,
        chapters=(chapter,),
        keywords=("知道", "目的", "动机"),
        snapshot_hash=HEX_SNAPSHOT,
        through_chapter=1,
        window_before=80,
        window_after=80,
    )
    # All three keywords fall inside one wide window -> one deduplicated span.
    assert len(candidates) == 1


def test_extract_empty_keywords_returns_empty():
    candidates = extract_heuristic_candidates(
        dimension=QueryDimension.WORLD_RULES,
        chapters=_state_chapters(),
        keywords=(),
        snapshot_hash=HEX_SNAPSHOT,
        through_chapter=10,
    )
    assert candidates == ()


# ---------------------------------------------------------------------------
# run_adapter orchestration
# ---------------------------------------------------------------------------


async def test_run_adapter_exact_reader_priority():
    refs = (make_ref(),)
    adapter = DEFAULT_ADAPTERS[QueryDimension.RELATIONS]
    source = make_snapshot(chapters=_state_chapters())
    result = await run_adapter(
        adapter,
        source=source,
        through_chapter=3,
        resolver=FakeResolver(make_reader(refs=refs), reader_id=adapter.reader_id),
    )
    assert result.status == AvailabilityStatus.AVAILABLE
    assert result.stage == FallbackStage.EXACT_READER
    assert result.refs == refs
    assert result.candidates == ()


async def test_run_adapter_reader_decline_falls_to_heuristic():
    adapter = DEFAULT_ADAPTERS[QueryDimension.CHARACTER_STATE]
    source = make_snapshot(chapters=_state_chapters())
    result = await run_adapter(
        adapter,
        source=source,
        through_chapter=3,
        resolver=FakeResolver(make_reader(decline=True)),
    )
    assert result.status == AvailabilityStatus.PARTIAL
    assert result.reason == "heuristic_candidate_only"
    assert result.candidates
    assert result.refs == ()


async def test_run_adapter_reader_missing_falls_to_heuristic():
    adapter = DEFAULT_ADAPTERS[QueryDimension.WORLD_RULES]
    source = make_snapshot(chapters=_state_chapters())
    result = await run_adapter(adapter, source=source, through_chapter=3, resolver=None)
    # No production reader -> deterministic heuristic candidate recall only.
    assert result.status == AvailabilityStatus.PARTIAL
    assert result.reason == "heuristic_candidate_only"
    assert result.stage == FallbackStage.DETERMINISTIC_HEURISTIC
    assert result.candidates
    assert all(c.evidence_eligible is False for c in result.candidates)


async def test_run_adapter_no_reader_no_heuristic_is_unavailable():
    adapter = DimensionAdapter(QueryDimension.WORLD_RULES, None, ())
    source = make_snapshot(chapters=_state_chapters())
    result = await run_adapter(adapter, source=source, through_chapter=3, resolver=None)
    assert result.status == AvailabilityStatus.UNAVAILABLE
    assert result.reason == "reader_unavailable"


async def test_run_adapter_reader_error_degrades_to_heuristic():
    adapter = DEFAULT_ADAPTERS[QueryDimension.EVENTS_CAUSALITY]
    source = make_snapshot(chapters=_state_chapters())
    result = await run_adapter(
        adapter,
        source=source,
        through_chapter=3,
        resolver=FakeResolver(make_reader(error=True)),
    )
    # Every dimension must still report a stable status (D-05 must-have).
    assert isinstance(result, DimensionResult)
    assert result.status in {AvailabilityStatus.AVAILABLE, AvailabilityStatus.PARTIAL}
    assert result.reason


async def test_run_adapter_is_deterministic_across_runs():
    adapter = DEFAULT_ADAPTERS[QueryDimension.CHARACTER_STATE]
    source = make_snapshot(chapters=_state_chapters())
    first = await run_adapter(adapter, source=source, through_chapter=3, resolver=None)
    second = await run_adapter(adapter, source=source, through_chapter=3, resolver=None)
    assert first == second
    assert first.reason == second.reason
    assert first.status == second.status


# ---------------------------------------------------------------------------
# run_plan_adapters
# ---------------------------------------------------------------------------


async def test_run_plan_adapters_reports_every_requested_dimension():
    plan = parse_query_plan(reader_payload())
    assert isinstance(plan, QueryPlan)
    source = make_snapshot(chapters=_state_chapters())
    results = await run_plan_adapters(plan, source=source, resolver=None)
    assert [r.dimension for r in results] == list(plan.dimensions)
    for result in results:
        assert result.status in {
            AvailabilityStatus.AVAILABLE,
            AvailabilityStatus.PARTIAL,
            AvailabilityStatus.UNAVAILABLE,
        }
        assert result.reason
        assert result.provenance
    by_dim = {r.dimension: r for r in results}
    # character_state has no production reader: never empty-success.
    assert by_dim[QueryDimension.CHARACTER_STATE].status in {
        AvailabilityStatus.PARTIAL,
        AvailabilityStatus.UNAVAILABLE,
    }
    assert not by_dim[QueryDimension.CHARACTER_STATE].evidence_eligible


async def test_run_plan_adapters_unregistered_dimension_fails_closed():
    plan = parse_query_plan(reader_payload(dimensions=["raw_text"]))
    assert isinstance(plan, QueryPlan)
    source = make_snapshot(chapters=_state_chapters())
    results = await run_plan_adapters(
        plan, source=source, resolver=None, adapters={}
    )
    assert len(results) == 1
    assert results[0].status == AvailabilityStatus.UNAVAILABLE
    assert results[0].reason == "reader_unavailable"


async def test_run_plan_adapters_whole_book_ignores_cutoff():
    plan = parse_query_plan(
        reader_payload(
            question_text="全书的主角动机是什么？",
            whole_book=True,
            reading_progress={
                "through_chapter": 3,
                "snapshot_hash": HEX_A,
                "full_book_authorized": True,
            },
        )
    )
    assert isinstance(plan, QueryPlan)
    assert plan.spoiler_cutoff.mode.value == "whole_book"
    source = make_snapshot(chapters=_state_chapters())
    results = await run_plan_adapters(plan, source=source, resolver=None)
    by_dim = {r.dimension: r for r in results}
    numbers = {
        c.chapter_number for c in by_dim[QueryDimension.CHARACTER_STATE].candidates
    }
    # Whole-book scope legitimately reaches chapter 4 as well.
    assert 4 in numbers


# ---------------------------------------------------------------------------
# NM candidate-only (D-14 / ADR-0002)
# ---------------------------------------------------------------------------


async def test_narrative_units_without_reader_is_candidate_only():
    adapter = DEFAULT_ADAPTERS[QueryDimension.NARRATIVE_UNITS]
    assert adapter.reader_id == "knowledge_units_units"
    source = make_snapshot(chapters=_state_chapters())
    result = await run_adapter(
        adapter,
        source=source,
        through_chapter=3,
        resolver=FakeResolver(None, reader_id=adapter.reader_id),
    )
    # NM layer declined / unavailable: candidates only, never a fact.
    assert result.refs == ()
    assert result.evidence_eligible is False
    if result.candidates:
        assert all(c.evidence_eligible is False for c in result.candidates)
        assert result.status == AvailabilityStatus.PARTIAL
        assert result.reason == "heuristic_candidate_only"


# ---------------------------------------------------------------------------
# Candidate-only never becomes evidence
# ---------------------------------------------------------------------------


def test_heuristic_output_cannot_become_evidence_ref_or_citation():
    candidates = (make_candidate(),)
    result = evaluate_dimension_chain(
        QueryDimension.CHARACTER_STATE,
        reader_outcome=None,
        heuristic_outcome=HeuristicOutcome(candidates=candidates),
    )
    assert result.refs == ()
    assert result.evidence_eligible is False
    # No path: candidates are not EvidenceRefs and carry no citation flag.
    assert not isinstance(candidates[0], EvidenceRef)
    assert candidates[0].evidence_eligible is False


def test_heuristic_keyword_sets_are_defined():
    assert WORLD_RULES_KEYWORDS
    assert CHARACTER_STATE_KEYWORDS
