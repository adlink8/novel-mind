"""Phase 26-04 consumer contract and smoke tests (REQ-QP-04, D-10, D-12).

Proves that Reader Chat (selection anchor) and Analysis Chat (chapter_range
anchor) share one QueryPlan / retrieval / evidence core with distinct anchors,
that the Frozen Manifest reaches the existing cited-answer gateway with a
leaf-only allowlist, that the consumer view exposes trace / availability /
fallback / citation jump, and that the frozen QA samples keep parsing for both
intents. No database is required: the QueryPlan boundary is pure over a frozen
``SourceSnapshot``.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from app.schemas.reader_chat import ReaderAnswerEnvelope
from app.services.queryplan.adapters import (
    ChapterRecord,
    DimensionResult,
    SourceSnapshot,
    chapter_content_hash,
)
from app.services.queryplan.evidence import FrozenManifest
from app.services.queryplan.schemas import (
    AvailabilityStatus,
    BlockedReasonCode,
    CutoffMode,
    EvidenceRef,
    FallbackStage,
    QueryDimension,
)
from app.services.queryplan.service import (
    ConsumerManifestResult,
    ConsumerPlanBlocked,
    QueryPlanAnswer,
    QueryPlanService,
)

pytestmark = pytest.mark.integration

HEX_SNAPSHOT = "c" * 64

CHAPTER_1_TEXT = "林安走进竹林，剑客随后现身。她低声问：你是谁？"
CHAPTER_2_TEXT = "第二章：剑客没有回答。林安握紧剑柄。"
CHAPTER_3_TEXT = "第三章：两人并肩走出竹林。"


def make_chapter(
    chapter_id: int, chapter_number: int, content: str
) -> ChapterRecord:
    return ChapterRecord(
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        content=content,
        content_hash=chapter_content_hash(content),
    )


def make_source(*, snapshot_hash: str = HEX_SNAPSHOT) -> SourceSnapshot:
    return SourceSnapshot(
        owner_id=1,
        novel_id=1,
        version_id=1,
        snapshot_hash=snapshot_hash,
        chapters=(
            make_chapter(1, 1, CHAPTER_1_TEXT),
            make_chapter(2, 2, CHAPTER_2_TEXT),
            make_chapter(3, 3, CHAPTER_3_TEXT),
        ),
    )


def reader_payload(*, through_chapter: int = 3, chapter_id: int = 1, **overrides):
    payload = QueryPlanService.build_consumer_request(
        intent="reader",
        owner_id=1,
        novel_id=1,
        version_id=1,
        question_text="林安走进哪里？",
        through_chapter=through_chapter,
        snapshot_hash=HEX_SNAPSHOT,
        selection={
            "kind": "selection",
            "chapter_id": chapter_id,
            "source_start": 0,
            "source_end": 4,
            "chapter_content_hash": "b" * 64,
        },
    )
    payload.update(overrides)
    return payload


def analysis_payload(
    *,
    through_chapter: int = 3,
    chapter_start: int = 1,
    chapter_end: int = 2,
    question: str = "前两章里主角的性格如何变化？",
    **overrides,
):
    payload = QueryPlanService.build_consumer_request(
        intent="analysis",
        owner_id=1,
        novel_id=1,
        version_id=1,
        question_text=question,
        through_chapter=through_chapter,
        snapshot_hash=HEX_SNAPSHOT,
        chapter_range={
            "kind": "chapter_range",
            "chapter_start": chapter_start,
            "chapter_end": chapter_end,
        },
        source="analysis_chat",
    )
    payload.update(overrides)
    return payload


def leaf_ref(
    *,
    chapter_id: int = 1,
    chapter_number: int = 1,
    content: str = CHAPTER_1_TEXT,
    start: int = 0,
    end: int | None = None,
) -> EvidenceRef:
    if end is None:
        end = len(content[:10])
    excerpt = content[start:end]
    return EvidenceRef(
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        source_start=start,
        source_end=end,
        content_hash=chapter_content_hash(excerpt),
        source_snapshot_hash=HEX_SNAPSHOT,
    )


def make_available(refs: tuple[EvidenceRef, ...]) -> DimensionResult:
    return DimensionResult(
        dimension=QueryDimension.RAW_TEXT,
        status=AvailabilityStatus.AVAILABLE,
        reason="reader_ok",
        provenance="exact_reader_v1",
        stage=FallbackStage.EXACT_READER,
        refs=refs,
    )


def legal_producer():
    async def produce(manifest: FrozenManifest) -> ReaderAnswerEnvelope:
        keys = sorted(manifest.allowed_evidence_ids())
        if not keys:
            return ReaderAnswerEnvelope.model_validate(
                {
                    "schema_version": "reader-answer.v1",
                    "answer_blocks": [],
                    "clarifying_question": "证据不足，无法作答。",
                }
            )
        return ReaderAnswerEnvelope.model_validate(
            {
                "schema_version": "reader-answer.v1",
                "answer_blocks": [
                    {"block_id": "b1", "text": "林安走进竹林。", "evidence_refs": [keys[0]]}
                ],
            }
        )

    return produce


# ---------------------------------------------------------------------------
# Shared core, distinct anchors (D-10)
# ---------------------------------------------------------------------------


async def test_reader_and_analysis_share_queryplan_core_with_distinct_anchors():
    service = QueryPlanService()
    source = make_source()
    ref = leaf_ref()

    reader_result, reader_view = await service.execute_consumer_manifest(
        reader_payload(), source=source, dimension_results=(make_available((ref,)),)
    )
    analysis_result, analysis_view = await service.execute_consumer_manifest(
        analysis_payload(), source=source, dimension_results=(make_available((ref,)),)
    )

    # Both consumers share the same QueryPlan core (manifest-level artifacts).
    assert isinstance(reader_result, ConsumerManifestResult)
    assert isinstance(analysis_result, ConsumerManifestResult)
    assert reader_result.manifest.manifest_checksum
    assert analysis_result.manifest.manifest_checksum
    assert reader_result.manifest.allowed_evidence_ids()
    assert analysis_result.manifest.allowed_evidence_ids()

    # …but retain their distinct anchors.
    assert reader_result.plan.intent.value == "reader"
    assert analysis_result.plan.intent.value == "analysis"
    assert reader_view.anchor_kind == "selection"
    assert analysis_view.anchor_kind == "chapter_range"

    # Same evidence core: identical leaf refs produce identical allowlist keys.
    assert reader_view.allowed_evidence_ids == analysis_view.allowed_evidence_ids
    assert reader_view.citation_jump[0].evidence_key.startswith("qp:")


async def test_reader_anchor_requires_selection_and_analysis_requires_range():
    # Reader cannot carry a chapter_range anchor (D-10).
    with pytest.raises(ConsumerPlanBlocked) as exc:
        QueryPlanService.parse_consumer_request(
            reader_payload(chapter_range={"kind": "chapter_range", "chapter_start": 1, "chapter_end": 2})
        )
    assert exc.value.reason_code == BlockedReasonCode.AMBIGUOUS_INTENT.value

    # Analysis requires a chapter_range anchor (D-10).
    with pytest.raises(ConsumerPlanBlocked) as exc:
        QueryPlanService.parse_consumer_request(
            analysis_payload(chapter_range=None)
        )
    assert exc.value.reason_code == BlockedReasonCode.AMBIGUOUS_INTENT.value


# ---------------------------------------------------------------------------
# Frozen Manifest -> cited-answer gateway, leaf-only (D-08)
# ---------------------------------------------------------------------------


async def test_frozen_manifest_reaches_cited_answer_gate_leaf_only():
    source = make_source()
    ref = leaf_ref()
    service = QueryPlanService()

    answer, view = await service.execute_consumer(
        reader_payload(),
        source=source,
        dimension_results=(make_available((ref,)),),
        answer_producer=legal_producer(),
    )
    assert isinstance(answer, QueryPlanAnswer)
    assert not answer.abstained
    allowed = view.allowed_evidence_ids
    assert len(allowed) == 1
    assert allowed[0].startswith("qp:")  # leaf-only allowlist shape

    # A forged non-leaf citation is rejected before any answer artifact exists.
    async def forged_producer(_manifest: FrozenManifest) -> ReaderAnswerEnvelope:
        return ReaderAnswerEnvelope.model_validate(
            {
                "schema_version": "reader-answer.v1",
                "answer_blocks": [
                    {
                        "block_id": "b1",
                        "text": "林安走进竹林。",
                        "evidence_refs": ["summary:ch1"],
                    }
                ],
            }
        )

    with pytest.raises(ValueError):
        await service.execute_consumer(
            reader_payload(),
            source=source,
            dimension_results=(make_available((ref,)),),
            answer_producer=forged_producer,
        )


def test_provable_call_chain_queryplan_to_gateway_to_schema():
    """AST-proof: service -> gateway::business_validate_answer -> schema validator."""
    root = Path(__file__).resolve().parents[3]
    service_src = (root / "app/services/queryplan/service.py").read_text(encoding="utf-8")
    gateway_src = (root / "app/services/reader_chat/gateway.py").read_text(encoding="utf-8")

    service_ast = ast.parse(service_src)
    gateway_ast = ast.parse(gateway_src)

    gateway_import = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "app.services.reader_chat.gateway"
        and any(a.name == "business_validate_answer" for a in node.names)
        for node in ast.walk(service_ast)
    )
    assert gateway_import

    gateway_calls_gate = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "business_validate_answer"
        for node in ast.walk(gateway_ast)
    )
    assert gateway_calls_gate


async def test_no_uncited_factual_assertion_rejected():
    source = make_source()
    service = QueryPlanService()

    async def uncited_producer(_manifest: FrozenManifest) -> ReaderAnswerEnvelope:
        # Non-leaf citation with no materialized evidence — rejected by the gate.
        return ReaderAnswerEnvelope.model_validate(
            {
                "schema_version": "reader-answer.v1",
                "answer_blocks": [
                    {
                        "block_id": "b1",
                        "text": "剑客的身份是谜。",
                        "evidence_refs": ["summary:ch1"],
                    }
                ],
            }
        )

    with pytest.raises(ValueError):
        await service.execute_consumer(
            reader_payload(),
            source=source,
            dimension_results=(make_unavailable_dimension(),),
            answer_producer=uncited_producer,
        )


def make_unavailable_dimension() -> DimensionResult:
    return DimensionResult(
        dimension=QueryDimension.WORLD_RULES,
        status=AvailabilityStatus.UNAVAILABLE,
        reason="dimension_unavailable",
        provenance="deterministic_contract_v1",
        stage=FallbackStage.STABLE_UNAVAILABLE,
    )


# ---------------------------------------------------------------------------
# Owner / cutoff / spoiler / whole-book revalidation (D-03, D-12)
# ---------------------------------------------------------------------------


def test_selection_beyond_cutoff_blocks():
    with pytest.raises(ConsumerPlanBlocked) as exc:
        QueryPlanService.parse_consumer_request(
            reader_payload(through_chapter=3, chapter_id=9)
        )
    assert exc.value.reason_code == BlockedReasonCode.SCOPE_ESCAPE.value


def test_range_beyond_cutoff_blocks():
    with pytest.raises(ConsumerPlanBlocked) as exc:
        QueryPlanService.parse_consumer_request(
            analysis_payload(through_chapter=3, chapter_end=8)
        )
    assert exc.value.reason_code == BlockedReasonCode.SCOPE_ESCAPE.value


def test_future_probing_blocks():
    with pytest.raises(ConsumerPlanBlocked) as exc:
        QueryPlanService.parse_consumer_request(
            reader_payload(question_text="第十章发生了什么？")
        )
    assert exc.value.reason_code == BlockedReasonCode.FUTURE_PROBING.value


def test_whole_book_requires_per_novel_switch():
    # Explicit switch but novel does not authorize full-book -> contradictory.
    with pytest.raises(ConsumerPlanBlocked) as exc:
        QueryPlanService.parse_consumer_request(
            analysis_payload(
                whole_book=True,
                reading_progress={
                    "through_chapter": 3,
                    "snapshot_hash": HEX_SNAPSHOT,
                    "full_book_authorized": False,
                },
            )
        )
    assert exc.value.reason_code == BlockedReasonCode.CONTRADICTORY.value

    # Whole-book wording without the explicit switch -> unauthorized.
    with pytest.raises(ConsumerPlanBlocked) as exc:
        QueryPlanService.parse_consumer_request(
            analysis_payload(question="全书的主线是什么？")
        )
    assert exc.value.reason_code == BlockedReasonCode.WHOLE_BOOK_UNAUTHORIZED.value


async def test_whole_book_authorized_bypasses_cutoff():
    plan = QueryPlanService.parse_consumer_request(
        analysis_payload(
            whole_book=True,
            reading_progress={
                "through_chapter": 1,
                "snapshot_hash": HEX_SNAPSHOT,
                "full_book_authorized": True,
            },
            question="全书的主线主题是什么？",
            chapter_end=3,
        )
    )
    assert plan.spoiler_cutoff.mode == CutoffMode.WHOLE_BOOK
    assert plan.spoiler_cutoff.full_book_authorized is True


# ---------------------------------------------------------------------------
# Consumer view: trace / availability / fallback / citation jump (D-01/D-05)
# ---------------------------------------------------------------------------


async def test_consumer_view_exposes_trace_availability_fallback_citation_jump():
    source = make_source()
    ref = leaf_ref(start=0, end=len(CHAPTER_1_TEXT[:10]))
    _, view = await QueryPlanService().execute_consumer_manifest(
        reader_payload(), source=source, dimension_results=(make_available((ref,)),)
    )

    # Trace level.
    assert len(view.trace_id) >= 32
    assert re.fullmatch(r"[0-9a-f]{64}", view.plan_hash)
    assert view.intent == "reader"
    assert view.cutoff_mode == CutoffMode.READING_PROGRESS.value
    assert view.through_chapter == 3

    # Availability level (declared per dimension; partial/unavailable recorded).
    assert view.availability
    statuses = {entry["status"] for entry in view.availability}
    assert statuses <= {"available", "partial", "unavailable"}

    # Fallback chain is the fixed D-15 chain.
    assert view.fallback["chain"] == [
        "exact_reader",
        "deterministic_heuristic",
        "stable_unavailable",
    ]

    # Citation-jump level: only leaf evidence, never summary/score/chat refs.
    assert view.citation_jump
    for jump in view.citation_jump:
        assert jump.evidence_key.startswith("qp:")
        assert jump.chapter_id >= 1
        assert jump.source_end > jump.source_start

    assert view.abstained is False
    canonical = view.canonical_dict()
    assert canonical["trace_id"] == view.trace_id


async def test_consumer_view_abstains_when_no_evidence():
    result, view = await QueryPlanService().execute_consumer_manifest(
        reader_payload(),
        source=make_source(),
        dimension_results=(make_unavailable_dimension(),),
    )
    assert view.abstained is True
    assert view.allowed_evidence_ids == ()
    assert view.citation_jump == ()
    # Omitted/fallback reasons exist but are never evidence.
    assert any(
        entry.kind in ("dimension", "heuristic_candidate")
        for entry in result.manifest.omitted
    )


# ---------------------------------------------------------------------------
# Consumer seams smoke (Reader selection + Analysis chapter_range)
# ---------------------------------------------------------------------------


def _mock_chat_session(*, cutoff: int, chapters: list):
    """AsyncMock session sufficient for resolve_progress_snapshot +
    build_source_snapshot + retrieve_visible_evidence (no pointers seeded)."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    session = AsyncMock()

    async def _scalar(query, *args, **kwargs):
        text = str(query)
        if "chunk_active_pointer" in text or "timeline_active_pointer" in text:
            return None
        return cutoff

    session.scalar = _scalar

    async def _scalars(query, *args, **kwargs):
        text_query = str(query)
        # 问答按需分析物化的域表查询：无候选行（空），避免污染 chapter 路由。
        if (
            "world_model_knowledge" in text_query
            or "key_scene_evidence_ranges" in text_query
            or "visual_bible_evidence_refs" in text_query
        ):
            return SimpleNamespace(all=lambda: [])
        return SimpleNamespace(all=lambda: list(chapters))

    session.scalars = _scalars
    session.get = AsyncMock(return_value=None)
    return session, SimpleNamespace(id=1, owner_id=1, reading_progress={})


def _chapter_row(chapter_id: int, chapter_number: int, content: str):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=chapter_id,
        novel_id=1,
        chapter_number=chapter_number,
        content=content,
    )


async def test_reader_queryplan_seam_runs_with_selection_anchor():
    from app.services.reader_chat.context import (
        ValidatedSelection,
        run_reader_queryplan,
    )

    content = CHAPTER_1_TEXT
    session, novel = _mock_chat_session(
        cutoff=1,
        chapters=[_chapter_row(1, 1, content)],
    )
    selection = ValidatedSelection(
        chapter_id=1,
        chapter_number=1,
        source_start=0,
        source_end=4,
        selection_text=content[0:4],
        selection_text_hash="0" * 64,
        chapter_content_hash=chapter_content_hash(content),
        hierarchy_build_id="none",
        hierarchy_checksum="0" * 64,
    )
    result, view = await run_reader_queryplan(
        session,
        novel=novel,
        owner_id=1,
        version_id=1,
        question="林安走进哪里？",
        selection=selection,
    )
    assert isinstance(result, ConsumerManifestResult)
    assert view.intent == "reader"
    assert view.anchor_kind == "selection"
    assert view.through_chapter == 1


async def test_reader_queryplan_seam_maps_blocked_plan_to_stable_code():
    from app.services.reader_chat.context import (
        SelectionValidationError,
        ValidatedSelection,
        run_reader_queryplan,
    )

    content = CHAPTER_1_TEXT
    session, novel = _mock_chat_session(
        cutoff=1,
        chapters=[_chapter_row(1, 1, content)],
    )
    selection = ValidatedSelection(
        chapter_id=1,
        chapter_number=1,
        source_start=0,
        source_end=4,
        selection_text=content[0:4],
        selection_text_hash="0" * 64,
        chapter_content_hash=chapter_content_hash(content),
        hierarchy_build_id="none",
        hierarchy_checksum="0" * 64,
    )
    with pytest.raises(SelectionValidationError) as exc:
        await run_reader_queryplan(
            session,
            novel=novel,
            owner_id=1,
            version_id=1,
            question="第十章发生了什么？",  # future probing
            selection=selection,
        )
    assert exc.value.code == "future_probing"


async def test_analysis_adapter_narrows_range_and_returns_view():
    from app.services.analysis_chat.query_adapter import (
        AnalysisQueryPlanAdapter,
    )

    session, novel = _mock_chat_session(
        cutoff=3,
        chapters=[
            _chapter_row(1, 1, CHAPTER_1_TEXT),
            _chapter_row(2, 2, CHAPTER_2_TEXT),
            _chapter_row(3, 3, CHAPTER_3_TEXT),
        ],
    )
    result, view = await AnalysisQueryPlanAdapter().execute_manifest(
        session,
        novel=novel,
        owner_id=1,
        version_id=1,
        question="前两章里主角的性格如何变化？",
        chapter_start=1,
        chapter_end=5,  # requested beyond cutoff -> narrowed to 3
    )
    assert isinstance(result, ConsumerManifestResult)
    assert view.intent == "analysis"
    assert view.anchor_kind == "chapter_range"
    assert result.plan.anchor.chapter_end == 3
    assert view.cutoff_mode == CutoffMode.READING_PROGRESS.value


async def test_analysis_adapter_blocks_range_starting_beyond_cutoff():
    from app.services.analysis_chat.query_adapter import AnalysisQueryPlanAdapter
    from app.services.reader_chat.context import SelectionValidationError

    session, novel = _mock_chat_session(
        cutoff=3,
        chapters=[
            _chapter_row(1, 1, CHAPTER_1_TEXT),
            _chapter_row(2, 2, CHAPTER_2_TEXT),
            _chapter_row(3, 3, CHAPTER_3_TEXT),
        ],
    )
    with pytest.raises(SelectionValidationError) as exc:
        await AnalysisQueryPlanAdapter().execute_manifest(
            session,
            novel=novel,
            owner_id=1,
            version_id=1,
            question="这段的主线是什么？",
            chapter_start=4,
            chapter_end=5,
        )
    assert exc.value.code == "chapter_beyond_cutoff"


def _fixture() -> dict:
    path = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "queryplan" / "questions_v1.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_frozen_qa_samples_parse_for_both_intents():
    fixture = _fixture()
    defaults = fixture["defaults"]
    for case in fixture["cases"]:
        payload = {
            "intent": case["intent"],
            "owner_id": defaults["owner_id"],
            "novel_id": defaults["novel_id"],
            "version_id": defaults["version_id"],
            "question_text": case["question"],
            "reading_progress": {
                "through_chapter": case.get("through_chapter", defaults["through_chapter"]),
                "snapshot_hash": case.get("snapshot_hash", defaults["snapshot_hash"]),
                "full_book_authorized": case.get(
                    "full_book_authorized", defaults["full_book_authorized"]
                ),
            },
            "source": case.get("source", defaults["source"]),
            "dataset_lineage": case.get("dataset_lineage", fixture["dataset_lineage"]),
        }
        if case.get("selection") is not None:
            payload["selection"] = case["selection"]
        if case.get("chapter_range") is not None:
            payload["chapter_range"] = case["chapter_range"]
        if case.get("whole_book"):
            payload["whole_book"] = True
        if case.get("dimensions") is not None:
            payload["dimensions"] = case["dimensions"]
        if case.get("answer_constraints") is not None:
            payload["answer_constraints"] = case["answer_constraints"]

        if case.get("expected") == "blocked":
            with pytest.raises(ConsumerPlanBlocked) as exc:
                QueryPlanService.parse_consumer_request(payload)
            assert exc.value.reason_code == case.get("expected_reason")
            continue

        plan = QueryPlanService.parse_consumer_request(payload)
        assert plan.intent.value == case["expected_intent"]
        assert plan.spoiler_cutoff.mode.value == case.get(
            "expected_cutoff_mode", "reading_progress"
        )
        if case.get("selection") is not None:
            assert plan.anchor.kind == "selection"
        if case.get("chapter_range") is not None:
            assert plan.anchor.kind == "chapter_range"
        expected_dimensions = case.get("expected_dimensions")
        if expected_dimensions:
            assert [d.value for d in plan.dimensions] == expected_dimensions
        for dim, status in (case.get("expected_availability") or {}).items():
            entry = next(
                e for e in plan.availability if e.dimension.value == dim
            )
            assert entry.status.value == status
