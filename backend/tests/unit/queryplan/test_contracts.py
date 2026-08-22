"""Phase 26-01 QueryPlan contract tests (REQ-QP-01, D-01/D-02/D-03/D-05/D-12).

Covers the frozen fixture, stable plan generation, determinism, fail-closed
rejection of unknown/ambiguous intent, scope escape, future probing and
contradictory constraints, plus the single-head reversible migration contract.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.queryplan.parser import parse_query_plan
from app.services.queryplan.schemas import (
    BlockedReasonCode,
    BlockedResult,
    QueryPlan,
    QueryPlanRequest,
    plan_payload_hash,
    QUERYPLAN_SCHEMA_VERSION,
)

pytestmark = pytest.mark.unit

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2] / "fixtures" / "queryplan" / "questions_v1.json"
)
MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations" / "versions"
MIGRATION_FILE = "20260801_2601_query_plan_trace.py"

HEX_A = "a" * 64
HEX_B = "b" * 64


def load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def case_request(fixture: dict, case: dict) -> dict:
    """Merge top-level defaults with a case into a parser payload."""

    defaults = fixture["defaults"]
    payload: dict = {
        "intent": case["intent"],
        "owner_id": defaults["owner_id"],
        "novel_id": defaults["novel_id"],
        "version_id": defaults["version_id"],
        "question_text": case["question"],
        "reading_progress": {
            "through_chapter": case.get("through_chapter", defaults["through_chapter"]),
            "snapshot_hash": defaults["snapshot_hash"],
            "full_book_authorized": case.get(
                "full_book_authorized", defaults["full_book_authorized"]
            ),
        },
        "whole_book": case.get("whole_book", False),
        "source": defaults["source"],
        "dataset_lineage": fixture["dataset_lineage"],
    }
    if case.get("selection") is not None:
        payload["selection"] = case["selection"]
    if case.get("chapter_range") is not None:
        payload["chapter_range"] = case["chapter_range"]
    if case.get("dimensions") is not None:
        payload["dimensions"] = case["dimensions"]
    if case.get("answer_constraints") is not None:
        payload["answer_constraints"] = case["answer_constraints"]
    return payload


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


def _load_migration():
    path = MIGRATIONS_DIR / MIGRATION_FILE
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Frozen fixture
# ---------------------------------------------------------------------------


def test_fixture_file_exists_and_is_frozen():
    fixture = load_fixture()
    assert fixture["fixture_version"] == "queryplan-fixture.v1"
    assert fixture["dataset_lineage"] == "queryplan-questions-v1"
    assert len(fixture["cases"]) >= 12
    keys = {case["case_key"] for case in fixture["cases"]}
    assert len(keys) == len(fixture["cases"]), "case keys must be unique"
    for case in fixture["cases"]:
        assert case["expected"] in {"plan", "blocked"}


@pytest.mark.parametrize("case_index", range(len(load_fixture()["cases"])))
def test_fixture_case_matches_expected_outcome(case_index: int):
    fixture = load_fixture()
    case = fixture["cases"][case_index]
    result = parse_query_plan(case_request(fixture, case))
    if case["expected"] == "plan":
        assert isinstance(result, QueryPlan), (
            f"{case['case_key']} expected plan, got {result!r}"
        )
        if case.get("expected_intent") is not None:
            assert result.intent.value == case["expected_intent"]
        if case.get("expected_cutoff_mode") is not None:
            assert result.spoiler_cutoff.mode.value == case["expected_cutoff_mode"]
        expected_dims = case.get("expected_dimensions")
        if expected_dims is not None:
            assert [d.value for d in result.dimensions] == expected_dims
        for dim, status in (case.get("expected_availability") or {}).items():
            entry = next(
                (a for a in result.availability if a.dimension.value == dim), None
            )
            assert entry is not None, f"{case['case_key']}: missing dimension {dim}"
            assert entry.status.value == status
    else:
        assert isinstance(result, BlockedResult), (
            f"{case['case_key']} expected blocked, got a QueryPlan"
        )
        assert result.reason_code.value == case["expected_reason"], (
            f"{case['case_key']} expected reason {case['expected_reason']}, "
            f"got {result.reason_code}"
        )


# ---------------------------------------------------------------------------
# Stable plan generation (D-01 / D-02)
# ---------------------------------------------------------------------------


def test_reader_question_produces_stable_plan_with_lineage():
    result = parse_query_plan(reader_payload())
    assert isinstance(result, QueryPlan)
    assert result.intent.value == "reader"
    assert result.owner_id == 1
    assert result.novel_id == 1
    assert result.version_id == 1
    assert result.spoiler_cutoff.mode.value == "reading_progress"
    assert result.spoiler_cutoff.through_chapter == 3
    assert result.spoiler_cutoff.full_book_authorized is False
    assert result.schema_version == QUERYPLAN_SCHEMA_VERSION
    assert result.trace.schema_version == QUERYPLAN_SCHEMA_VERSION
    assert len(result.trace.trace_id) == 32
    assert len(result.trace.idempotency_key) == 64
    assert len(result.trace.canonical_payload_hash) == 64
    assert len(result.trace.availability_checksum) == 64
    assert result.trace.source == "reader_chat"
    assert result.trace.dataset_lineage == "queryplan-questions-v1"


def test_analysis_question_produces_plan_with_range_anchor():
    result = parse_query_plan(analysis_payload())
    assert isinstance(result, QueryPlan)
    assert result.intent.value == "analysis"
    assert result.anchor is not None
    assert result.anchor.kind == "chapter_range"
    assert result.spoiler_cutoff.mode.value == "reading_progress"


def test_default_reading_progress_cutoff_and_availability_semantics():
    result = parse_query_plan(reader_payload())
    assert isinstance(result, QueryPlan)
    assert result.spoiler_cutoff.mode.value == "reading_progress"
    by_dim = {a.dimension: a for a in result.availability}
    assert len(by_dim) == len(result.dimensions)
    # D-05: absent readers are declared unavailable with a stable reason, never
    # empty-success.
    assert by_dim["character_state"].status.value == "unavailable"
    assert (
        by_dim["character_state"].reason
        == "character_state_reader_not_implemented_phase27"
    )
    assert by_dim["raw_text"].status.value == "available"
    # D-15: fallback is the fixed single chain with candidate-recall-only heuristic.
    assert result.fallback.heuristic_candidate_recall_only is True
    assert [s.value for s in result.fallback.chain] == [
        "exact_reader",
        "deterministic_heuristic",
        "stable_unavailable",
    ]


def test_whole_book_requires_explicit_authorized_switch():
    denied = parse_query_plan(reader_payload(question_text="全书的主角身份是什么？"))
    assert isinstance(denied, BlockedResult)
    assert denied.reason_code == BlockedReasonCode.WHOLE_BOOK_UNAUTHORIZED

    authorized = parse_query_plan(
        reader_payload(
            question_text="全书的主角身份是什么？",
            whole_book=True,
            reading_progress={
                "through_chapter": 3,
                "snapshot_hash": HEX_A,
                "full_book_authorized": True,
            },
        )
    )
    assert isinstance(authorized, QueryPlan)
    assert authorized.spoiler_cutoff.mode.value == "whole_book"
    assert authorized.spoiler_cutoff.full_book_authorized is True


def test_plan_is_deterministic_across_parses():
    first = parse_query_plan(reader_payload())
    second = parse_query_plan(reader_payload())
    assert isinstance(first, QueryPlan)
    assert isinstance(second, QueryPlan)
    assert first.trace.idempotency_key == second.trace.idempotency_key
    assert first.trace.canonical_payload_hash == second.trace.canonical_payload_hash
    assert first.trace.availability_checksum == second.trace.availability_checksum
    assert first.question_hash == second.question_hash
    assert [d.value for d in first.dimensions] == [d.value for d in second.dimensions]
    # Trace identity is unique per parse but the payload hash is stable.
    assert first.trace.trace_id != second.trace.trace_id


def test_normalized_whitespace_does_not_change_plan():
    # Inputs that collapse to the same normalized text produce the same plan.
    spaced = dict(reader_payload(), question_text="林安 在 第一章 走进哪里？")
    messy = dict(reader_payload(), question_text=" 林安   在 第一章   走进哪里？ ")
    a = parse_query_plan(spaced)
    b = parse_query_plan(messy)
    assert isinstance(a, QueryPlan)
    assert isinstance(b, QueryPlan)
    assert a.trace.canonical_payload_hash == b.trace.canonical_payload_hash
    assert a.trace.idempotency_key == b.trace.idempotency_key


# ---------------------------------------------------------------------------
# Fail-closed rejection (D-02 / D-03)
# ---------------------------------------------------------------------------


def test_unknown_intent_rejected():
    result = parse_query_plan(reader_payload(intent="historian"))
    assert isinstance(result, BlockedResult)
    assert result.reason_code == BlockedReasonCode.UNKNOWN_INTENT
    assert result.clarification


def test_ambiguous_intent_rejected():
    reader_with_range = parse_query_plan(
        reader_payload(
            chapter_range={
                "kind": "chapter_range",
                "chapter_start": 1,
                "chapter_end": 2,
            }
        )
    )
    assert isinstance(reader_with_range, BlockedResult)
    assert reader_with_range.reason_code == BlockedReasonCode.AMBIGUOUS_INTENT

    analysis_with_selection = parse_query_plan(
        analysis_payload(
            selection={
                "kind": "selection",
                "chapter_id": 1,
                "source_start": 0,
                "source_end": 10,
                "chapter_content_hash": HEX_B,
            }
        )
    )
    assert isinstance(analysis_with_selection, BlockedResult)
    assert analysis_with_selection.reason_code == BlockedReasonCode.AMBIGUOUS_INTENT

    analysis_without_range = parse_query_plan(analysis_payload(chapter_range=None))
    assert isinstance(analysis_without_range, BlockedResult)
    assert analysis_without_range.reason_code == BlockedReasonCode.AMBIGUOUS_INTENT


def test_scope_escape_rejected():
    selection_escape = parse_query_plan(
        reader_payload(
            selection={
                "kind": "selection",
                "chapter_id": 9,
                "source_start": 0,
                "source_end": 10,
                "chapter_content_hash": HEX_B,
            }
        )
    )
    assert isinstance(selection_escape, BlockedResult)
    assert selection_escape.reason_code == BlockedReasonCode.SCOPE_ESCAPE

    range_escape = parse_query_plan(
        analysis_payload(
            chapter_range={
                "kind": "chapter_range",
                "chapter_start": 4,
                "chapter_end": 8,
            }
        )
    )
    assert isinstance(range_escape, BlockedResult)
    assert range_escape.reason_code == BlockedReasonCode.SCOPE_ESCAPE


def test_future_probing_rejected():
    result = parse_query_plan(reader_payload(question_text="第十章发生了什么？"))
    assert isinstance(result, BlockedResult)
    assert result.reason_code == BlockedReasonCode.FUTURE_PROBING

    # A chapter reference inside the cutoff is fine.
    ok = parse_query_plan(reader_payload(question_text="第二章提到了什么？"))
    assert isinstance(ok, QueryPlan)


def test_contradictory_constraints_rejected():
    relaxed = parse_query_plan(
        reader_payload(
            answer_constraints={
                "must_cite_every_fact": False,
                "abstain_without_evidence": False,
            }
        )
    )
    assert isinstance(relaxed, BlockedResult)
    assert relaxed.reason_code == BlockedReasonCode.CONTRADICTORY

    whole_book_contradiction = parse_query_plan(
        reader_payload(question_text="整本书的主题是什么？", whole_book=True)
    )
    assert isinstance(whole_book_contradiction, BlockedResult)
    assert whole_book_contradiction.reason_code == BlockedReasonCode.CONTRADICTORY


def test_reject_extra_fields_and_invalid_scope():
    with pytest.raises(ValidationError):
        QueryPlanRequest.model_validate(reader_payload(extra_field="nope"))
    malformed = parse_query_plan(reader_payload(owner_id=0))
    assert isinstance(malformed, BlockedResult)
    assert malformed.reason_code == BlockedReasonCode.INVALID_INPUT


def test_blocked_result_never_creates_trace():
    """A blocked parse must not yield a QueryPlan or any trace payload (D-03)."""

    for payload in (
        reader_payload(intent="historian"),
        reader_payload(question_text="第十章发生了什么？"),
        reader_payload(question_text="全书的主线是什么？"),
    ):
        result = parse_query_plan(payload)
        assert isinstance(result, BlockedResult)
        assert not hasattr(result, "trace")
        assert not hasattr(result, "canonical_payload_hash")
        assert result.reason_code in {
            BlockedReasonCode.UNKNOWN_INTENT,
            BlockedReasonCode.FUTURE_PROBING,
            BlockedReasonCode.WHOLE_BOOK_UNAUTHORIZED,
        }


def test_parser_is_pure_no_database_writes():
    """parse_query_plan must be a pure function with no session/db dependency."""

    import inspect

    signature = inspect.signature(parse_query_plan)
    assert list(signature.parameters) == ["payload"]
    source = inspect.getsource(parse_query_plan)
    assert "AsyncSession" not in source
    assert "session" not in source


def test_no_nm_promotion_or_active_pointer_in_plan():
    """D-14: the QueryPlan boundary carries no promotion / active-pointer fields."""

    plan = parse_query_plan(analysis_payload())
    assert isinstance(plan, QueryPlan)
    payload = plan.model_dump(mode="json", exclude={"trace"})
    lowered = json.dumps(payload).lower()
    for forbidden in ("active_pointer", "promotion", "current_revision", "cutover"):
        assert forbidden not in lowered, (
            f"forbidden field leaked into plan: {forbidden}"
        )


# ---------------------------------------------------------------------------
# Migration contract (single head, reversible pair)
# ---------------------------------------------------------------------------


def test_migration_metadata_matches_plan():
    migration = _load_migration()
    assert migration.revision == "20260801_2601"
    assert migration.down_revision == "27approval01"
    assert migration.branch_labels is None
    assert migration.depends_on is None
    # Symmetric reversible pair must both exist.
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)


def test_canonical_payload_hash_is_stable_and_covered():
    plan = parse_query_plan(reader_payload())
    assert isinstance(plan, QueryPlan)
    dumped = plan.model_dump(mode="json", exclude={"trace"})
    assert plan.trace.canonical_payload_hash == plan_payload_hash(dumped)
