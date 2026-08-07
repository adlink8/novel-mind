"""Phase 26-01 deterministic parser tests (REQ-QP-01, D-02/D-03/D-10/D-12).

Granular coverage of the deterministic parsing rules and hash determinism.
"""

from __future__ import annotations

import pytest

from app.services.queryplan.parser import parse_query_plan
from app.services.queryplan.schemas import (
    BlockedReasonCode,
    BlockedResult,
    QueryPlan,
    idempotency_key,
    normalize_query_text,
    question_hash,
)

pytestmark = pytest.mark.unit

HEX_A = "a" * 64
HEX_B = "b" * 64


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


def analysis_payload(**overrides) -> dict:
    base = reader_payload(
        intent="analysis",
        question_text="前两章里主角的性格如何变化？",
        chapter_range={"kind": "chapter_range", "chapter_start": 1, "chapter_end": 2},
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Normalization and hashing determinism
# ---------------------------------------------------------------------------


def test_normalize_query_text_collapses_whitespace():
    # NFKC normalizes full-width punctuation/digits to half-width.
    assert normalize_query_text(" 林安  在 第一章  走进哪里？ ") == (
        "林安 在 第一章 走进哪里?"
    )
    assert normalize_query_text("ＡＢＣ　第３章") == "ABC 第3章"


def test_question_hash_is_deterministic_and_64_hex():
    h1 = question_hash("林安在第一章走进哪里？")
    h2 = question_hash("林安在第一章走进哪里？")
    assert h1 == h2
    assert len(h1) == 64
    assert all(c in "0123456789abcdef" for c in h1)


def test_idempotency_key_ignores_lineage_but_keeps_plan_inputs():
    base = reader_payload()
    a = parse_query_plan(base)
    assert isinstance(a, QueryPlan)
    # Same plan inputs but different source/dataset lineage → same key.
    different_lineage = dict(
        base, source="analysis_chat", dataset_lineage="queryplan-questions-v2"
    )
    b = parse_query_plan(different_lineage)
    assert isinstance(b, QueryPlan)
    assert a.trace.idempotency_key == b.trace.idempotency_key
    # Different question text → different key.
    c = parse_query_plan(reader_payload(question_text="主角此刻在做什么？"))
    assert isinstance(c, QueryPlan)
    assert a.trace.idempotency_key != c.trace.idempotency_key
    # Different cutoff snapshot → different key.
    d = parse_query_plan(
        reader_payload(
            reading_progress={
                "through_chapter": 4,
                "snapshot_hash": HEX_B,
                "full_book_authorized": False,
            }
        )
    )
    assert isinstance(d, QueryPlan)
    assert a.trace.idempotency_key != d.trace.idempotency_key


def test_reason_codes_are_stable_for_same_failure():
    payload = reader_payload(question_text="第十章发生了什么？")
    first = parse_query_plan(payload)
    second = parse_query_plan(payload)
    assert isinstance(first, BlockedResult)
    assert isinstance(second, BlockedResult)
    assert first.reason_code == second.reason_code
    assert first.message == second.message
    assert first.clarification == second.clarification


# ---------------------------------------------------------------------------
# Deterministic parser rules
# ---------------------------------------------------------------------------


def test_future_probing_applies_to_chinese_and_english_chapter_refs():
    for question in (
        "第10章发生了什么？",
        "第十章发生了什么？",
        "what happened in chapter 10?",
    ):
        result = parse_query_plan(reader_payload(question_text=question))
        assert isinstance(result, BlockedResult)
        assert result.reason_code == BlockedReasonCode.FUTURE_PROBING


def test_chapter_reference_at_cutoff_boundary_is_allowed():
    for question in ("第一章发生了什么？", "第三章结尾发生了什么？"):
        result = parse_query_plan(reader_payload(question_text=question))
        assert isinstance(result, QueryPlan), question


def test_whole_book_skips_future_probing():
    result = parse_query_plan(
        analysis_payload(
            question_text="第十章的角色关系如何？",
            whole_book=True,
            reading_progress={
                "through_chapter": 3,
                "snapshot_hash": HEX_A,
                "full_book_authorized": True,
            },
        )
    )
    assert isinstance(result, QueryPlan)
    assert result.spoiler_cutoff.mode.value == "whole_book"


def test_whole_book_without_authorization_is_contradictory():
    result = parse_query_plan(
        analysis_payload(question_text="全书主线是什么？", whole_book=True)
    )
    assert isinstance(result, BlockedResult)
    assert result.reason_code == BlockedReasonCode.CONTRADICTORY


def test_selection_half_open_range_and_range_order_enforced():
    bad_selection = parse_query_plan(
        reader_payload(
            selection={
                "kind": "selection",
                "chapter_id": 1,
                "source_start": 10,
                "source_end": 10,
                "chapter_content_hash": HEX_B,
            }
        )
    )
    assert isinstance(bad_selection, BlockedResult)
    assert bad_selection.reason_code == BlockedReasonCode.INVALID_INPUT

    bad_range = parse_query_plan(
        analysis_payload(
            chapter_range={
                "kind": "chapter_range",
                "chapter_start": 3,
                "chapter_end": 1,
            }
        )
    )
    assert isinstance(bad_range, BlockedResult)
    assert bad_range.reason_code == BlockedReasonCode.INVALID_INPUT


def test_anchor_kind_must_match_anchor_shape():
    mismatched = parse_query_plan(
        reader_payload(
            selection={
                "kind": "chapter_range",
                "chapter_start": 1,
                "chapter_end": 2,
            }
        )
    )
    assert isinstance(mismatched, BlockedResult)
    assert mismatched.reason_code == BlockedReasonCode.INVALID_INPUT


def test_explicit_dimensions_dedup_and_are_validated():
    result = parse_query_plan(
        analysis_payload(
            dimensions=["relations", "relations", "timeline"],
        )
    )
    assert isinstance(result, QueryPlan)
    assert [d.value for d in result.dimensions] == ["relations", "timeline"]

    unknown_dimension = parse_query_plan(analysis_payload(dimensions=["memes"]))
    assert isinstance(unknown_dimension, BlockedResult)
    assert unknown_dimension.reason_code == BlockedReasonCode.INVALID_INPUT


def test_answer_constraints_default_and_max_refs():
    result = parse_query_plan(reader_payload())
    assert isinstance(result, QueryPlan)
    assert result.answer_constraints.must_cite_every_fact is True
    assert result.answer_constraints.abstain_without_evidence is True
    assert result.answer_constraints.max_evidence_refs == 8

    custom = parse_query_plan(
        reader_payload(
            answer_constraints={"max_evidence_refs": 3, "allow_summary_citation": False}
        )
    )
    assert isinstance(custom, QueryPlan)
    assert custom.answer_constraints.max_evidence_refs == 3


def test_invalid_scope_values_fail_closed():
    for bad in (
        {"owner_id": 0},
        {"novel_id": -1},
        {"version_id": 0},
        {
            "reading_progress": {
                "through_chapter": 0,
                "snapshot_hash": HEX_A,
                "full_book_authorized": False,
            }
        },
        {
            "reading_progress": {
                "through_chapter": 3,
                "snapshot_hash": "short",
                "full_book_authorized": False,
            }
        },
    ):
        result = parse_query_plan(reader_payload(**bad))
        assert isinstance(result, BlockedResult), bad
        assert result.reason_code == BlockedReasonCode.INVALID_INPUT


def test_missing_required_scope_is_invalid():
    payload = reader_payload()
    del payload["novel_id"]
    result = parse_query_plan(payload)
    assert isinstance(result, BlockedResult)
    assert result.reason_code == BlockedReasonCode.INVALID_INPUT


def test_parser_returns_blocked_for_non_dict_input():
    result = parse_query_plan(None)  # type: ignore[arg-type]
    assert isinstance(result, BlockedResult)


def test_plan_schema_and_parser_versions_pinned():
    plan = parse_query_plan(analysis_payload())
    assert isinstance(plan, QueryPlan)
    assert plan.schema_version == "queryplan.v1"
    assert plan.parser_version == "queryplan-parser.v1"
    assert plan.trace.parser_version == "queryplan-parser.v1"


def test_idempotency_key_helper_matches_parser_output():
    from app.services.queryplan.schemas import QueryPlanRequest

    payload = reader_payload()
    request = QueryPlanRequest.model_validate(payload)
    result = parse_query_plan(payload)
    assert isinstance(result, QueryPlan)
    assert result.trace.idempotency_key == idempotency_key(request)
