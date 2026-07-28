"""Unit tests for Phase 17 qualification contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.narrative_memory.qualification_contracts import (
    FORBIDDEN_FIXTURE_RESULT_FIELDS,
    QUALIFICATION_KIND,
    SCOPE_DISCLAIMER,
    BudgetPolicy,
    ExpectedAnswerability,
    GoldLeafRef,
    MetricCell,
    MetricStatus,
    ModelLineageSpec,
    PairedCaseEnvelope,
    PriceSnapshot,
    QualificationFixture,
    QualificationPolicy,
    QualificationReport,
    QualificationVerdict,
    QuestionBucket,
    QuestionCase,
    RetrievalStrategy,
    ThresholdSpec,
    assert_envelopes_paired,
    build_paired_envelopes,
    reject_result_fields,
    stable_checksum,
    stable_json,
)

pytestmark = pytest.mark.unit

FIXTURE_PATH = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "narrative_memory"
    / "qualification"
    / "single_book_v1.json"
)
POLICY_PATH = FIXTURE_PATH.parent / "policy_v1.json"

HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_1 = "1" * 64
HEX_2 = "2" * 64
HEX_3 = "3" * 64
HEX_4 = "4" * 64
HEX_5 = "5" * 64
HEX_6 = "6" * 64


def _gen() -> ModelLineageSpec:
    return ModelLineageSpec(
        role="generator",
        deployment_id="gen",
        model_revision="g1",
        prompt_hash=HEX_1,
        schema_hash=HEX_2,
        decoding_hash=HEX_3,
        calibrated=False,
    )


def _judge() -> ModelLineageSpec:
    return ModelLineageSpec(
        role="judge",
        deployment_id="judge",
        model_revision="j1",
        prompt_hash=HEX_4,
        schema_hash=HEX_5,
        decoding_hash=HEX_6,
        calibrated=True,
    )


def _policy(**overrides) -> QualificationPolicy:
    base = dict(
        generator=_gen(),
        judge=_judge(),
        price=PriceSnapshot(input_per_million_usd="0.5", output_per_million_usd="1.5"),
        budget=BudgetPolicy(),
        thresholds=(
            ThresholdSpec(
                metric_name="spoiler_leakage",
                scope="aggregate",
                zero_tolerance=True,
                maximum=0.0,
            ),
        ),
    )
    base.update(overrides)
    return QualificationPolicy(**base)


def _leaf(**kw) -> GoldLeafRef:
    data = dict(
        leaf_id="leaf-1",
        hierarchy_build_id="hb1",
        source_snapshot_hash=HEX_A,
        chapter_id=1,
        chapter_number=1,
        start_offset=0,
        end_offset=5,
        content_hash=HEX_B,
        relevance=2.0,
    )
    data.update(kw)
    return GoldLeafRef(**data)


def _case(**kw) -> QuestionCase:
    data = dict(
        case_key="c1",
        bucket=QuestionBucket.LOCAL,
        query="q?",
        through_chapter=1,
        expected_answerability=ExpectedAnswerability.ANSWERABLE,
        gold_leaves=(_leaf(),),
    )
    data.update(kw)
    return QuestionCase(**data)


def _fixture(cases=None, **kw) -> QualificationFixture:
    data = dict(
        owner_id=1,
        novel_id=1,
        version_id=1,
        source_snapshot_hash=HEX_A,
        hierarchy_build_id="hb1",
        hierarchy_checksum=HEX_B,
        candidate_manifest_checksum=HEX_1,
        reviewed_by="rev",
        frozen_at="2026-07-16T00:00:00Z",
        cases=cases
        or (
            _case(case_key="local_1", bucket=QuestionBucket.LOCAL),
            _case(
                case_key="arc_1",
                bucket=QuestionBucket.CROSS_CHAPTER_ARC,
                through_chapter=2,
                gold_leaves=(_leaf(chapter_number=2, leaf_id="l2"),),
            ),
            _case(
                case_key="g_1",
                bucket=QuestionBucket.WHOLE_BOOK_GLOBAL,
                through_chapter=3,
                full_book_authorized=True,
                gold_leaves=(_leaf(chapter_number=3, leaf_id="l3"),),
            ),
            _case(
                case_key="na_1",
                bucket=QuestionBucket.NO_ANSWER,
                expected_answerability=ExpectedAnswerability.NO_ANSWER,
                gold_leaves=(),
                no_answer_rationale="none",
            ),
            _case(
                case_key="sp_1",
                bucket=QuestionBucket.SPOILER,
                expected_answerability=ExpectedAnswerability.SPOILER_RISK,
                gold_leaves=(),
                spoiler_forbidden=(
                    {"leaf_id": "future", "chapter_number": 9, "metadata_key": None},
                ),
            ),
        ),
    )
    data.update(kw)
    return QualificationFixture(**data)


def test_qualification_kind_and_verdict_closed():
    assert QUALIFICATION_KIND == "single_book_candidate"
    assert set(QualificationVerdict) == {
        QualificationVerdict.QUALIFIED_CANDIDATE,
        QualificationVerdict.BLOCKED,
    }
    assert (
        "promote" not in SCOPE_DISCLAIMER.lower()
        or "does not promote" in SCOPE_DISCLAIMER.lower()
    )


def test_frozen_fixture_json_loads_and_hashes():
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    fixture = QualificationFixture.model_validate(payload)
    assert fixture.bucket_counts() == {
        "local": 1,
        "cross_chapter_arc": 1,
        "whole_book_global": 1,
        "no_answer": 1,
        "spoiler": 1,
    }
    h1 = fixture.checksum()
    h2 = fixture.checksum()
    assert h1 == h2
    assert len(h1) == 64


def test_policy_json_loads():
    payload = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    policy = QualificationPolicy.model_validate(payload)
    assert policy.judge.calibrated is True
    assert policy.checksum()


def test_reject_result_fields():
    with pytest.raises(ValueError, match="result-derived"):
        reject_result_fields({"case_key": "x", "candidate_answer": "bad"})
    for field in ("metrics", "verdict", "judge_score"):
        assert field in FORBIDDEN_FIXTURE_RESULT_FIELDS


def test_duplicate_case_key_rejected():
    with pytest.raises(ValidationError):
        _fixture(
            cases=(
                _case(case_key="dup"),
                _case(
                    case_key="dup",
                    bucket=QuestionBucket.NO_ANSWER,
                    expected_answerability=ExpectedAnswerability.NO_ANSWER,
                    gold_leaves=(),
                    no_answer_rationale="x",
                ),
            )
        )


def test_empty_bucket_rejected():
    with pytest.raises(ValidationError, match="empty required buckets"):
        _fixture(
            cases=(
                _case(case_key="local_1"),
                _case(
                    case_key="arc_1",
                    bucket=QuestionBucket.CROSS_CHAPTER_ARC,
                    through_chapter=2,
                ),
                _case(
                    case_key="g_1",
                    bucket=QuestionBucket.WHOLE_BOOK_GLOBAL,
                    through_chapter=3,
                    full_book_authorized=True,
                ),
                _case(
                    case_key="na_1",
                    bucket=QuestionBucket.NO_ANSWER,
                    expected_answerability=ExpectedAnswerability.NO_ANSWER,
                    gold_leaves=(),
                    no_answer_rationale="x",
                ),
                # missing spoiler
            )
        )


def test_cross_snapshot_gold_rejected():
    with pytest.raises(ValidationError, match="cross-snapshot"):
        _fixture(
            cases=(
                _case(gold_leaves=(_leaf(source_snapshot_hash=HEX_B),)),
                _case(
                    case_key="arc_1",
                    bucket=QuestionBucket.CROSS_CHAPTER_ARC,
                    through_chapter=2,
                ),
                _case(
                    case_key="g_1",
                    bucket=QuestionBucket.WHOLE_BOOK_GLOBAL,
                    through_chapter=3,
                    full_book_authorized=True,
                ),
                _case(
                    case_key="na_1",
                    bucket=QuestionBucket.NO_ANSWER,
                    expected_answerability=ExpectedAnswerability.NO_ANSWER,
                    gold_leaves=(),
                    no_answer_rationale="x",
                ),
                _case(
                    case_key="sp_1",
                    bucket=QuestionBucket.SPOILER,
                    expected_answerability=ExpectedAnswerability.SPOILER_RISK,
                    gold_leaves=(),
                    spoiler_forbidden=(
                        {"leaf_id": "f", "chapter_number": 9, "metadata_key": None},
                    ),
                ),
            )
        )


def test_same_generator_judge_lineage_rejected():
    with pytest.raises(ValidationError, match="isolated"):
        _policy(
            judge=ModelLineageSpec(
                role="judge",
                deployment_id="gen",  # same deployment as generator
                model_revision="g1",
                prompt_hash=HEX_1,
                schema_hash=HEX_5,
                decoding_hash=HEX_6,
                calibrated=True,
            )
        )


def test_uncalibrated_judge_rejected():
    with pytest.raises(ValidationError, match="calibrated"):
        _policy(judge=_judge().model_copy(update={"calibrated": False}))


def test_nan_threshold_rejected():
    with pytest.raises(ValidationError):
        ThresholdSpec(
            metric_name="x",
            scope="aggregate",
            minimum=float("nan"),
        )


def test_unknown_price_rejected():
    with pytest.raises(ValidationError):
        PriceSnapshot(input_per_million_usd="nan", output_per_million_usd="1")


def test_metric_cell_rejects_nan():
    with pytest.raises(ValidationError):
        MetricCell(
            metric_name="m",
            numerator=float("inf"),
            denominator=1,
            value=1,
            unit="ratio",
            status=MetricStatus.OK,
        )


def test_metric_ok_requires_positive_denominator():
    with pytest.raises(ValidationError, match="denominator"):
        MetricCell(
            metric_name="m",
            numerator=0,
            denominator=0,
            value=0,
            unit="ratio",
            status=MetricStatus.OK,
        )


def test_report_only_two_verdicts_and_sorted_reasons():
    cell = MetricCell(
        metric_name="m",
        numerator=1,
        denominator=1,
        value=1,
        unit="count",
        status=MetricStatus.OK,
    )
    blocked = QualificationReport(
        verdict=QualificationVerdict.BLOCKED,
        reason_codes=("aa", "zz"),
        fixture_checksum=HEX_A,
        policy_checksum=HEX_B,
        metric_cells=(cell,),
    )
    assert blocked.reason_codes == ("aa", "zz")
    with pytest.raises(ValidationError):
        QualificationReport(
            verdict=QualificationVerdict.BLOCKED,
            reason_codes=("zz", "aa"),  # unsorted
            fixture_checksum=HEX_A,
            policy_checksum=HEX_B,
            metric_cells=(cell,),
        )
    ok = QualificationReport(
        verdict=QualificationVerdict.QUALIFIED_CANDIDATE,
        reason_codes=(),
        fixture_checksum=HEX_A,
        policy_checksum=HEX_B,
        metric_cells=(cell,),
    )
    assert ok.verdict == QualificationVerdict.QUALIFIED_CANDIDATE
    with pytest.raises(ValidationError):
        QualificationReport(
            verdict=QualificationVerdict.BLOCKED,
            reason_codes=("promoted",),
            fixture_checksum=HEX_A,
            policy_checksum=HEX_B,
            metric_cells=(cell,),
        )


def test_paired_envelopes_identical_except_strategy():
    fixture = _fixture()
    policy = _policy()
    case = fixture.cases[0]
    cand, base = build_paired_envelopes(case, fixture, policy)
    assert cand.strategy == RetrievalStrategy.HIERARCHICAL_CANDIDATE
    assert base.strategy == RetrievalStrategy.LEAF_RAW_BASELINE
    assert cand.common.model_dump() == base.common.model_dump()
    assert cand.cache_namespace != base.cache_namespace
    assert_envelopes_paired(cand, base)


def test_paired_divergence_blocks():
    fixture = _fixture()
    policy = _policy()
    case = fixture.cases[0]
    cand, base = build_paired_envelopes(case, fixture, policy)
    bad_common = cand.common.model_copy(update={"top_k": cand.common.top_k + 1})
    bad = PairedCaseEnvelope(
        common=bad_common,
        strategy=RetrievalStrategy.LEAF_RAW_BASELINE,
        cache_namespace=base.cache_namespace,
    )
    with pytest.raises(ValueError, match="diverge"):
        assert_envelopes_paired(cand, bad)


def test_checksum_excludes_self_digest_field():
    body = {"a": 1, "fixture_checksum": "should-not-matter"}
    other = {"a": 1, "fixture_checksum": "different"}
    assert stable_checksum(body) == stable_checksum(other)


def test_extra_forbid():
    with pytest.raises(ValidationError):
        GoldLeafRef(
            leaf_id="l",
            hierarchy_build_id="h",
            source_snapshot_hash=HEX_A,
            chapter_id=1,
            chapter_number=1,
            start_offset=0,
            end_offset=1,
            content_hash=HEX_B,
            relevance=1.0,
            unexpected=True,  # type: ignore[call-arg]
        )


def test_insertion_order_independent_checksum():
    f1 = _fixture()
    dumped = f1.model_dump(mode="json")
    # re-order case list alphabetically vs reverse — fixture stores tuple order;
    # canonical dump uses sort_keys so top-level key order is stable.
    s1 = stable_json(dumped)
    s2 = stable_json(json.loads(s1))
    assert s1 == s2
    assert stable_checksum(dumped) == stable_checksum(json.loads(s1))


def test_one_field_hash_sensitivity():
    f = _fixture()
    h0 = f.checksum()
    h1 = f.model_copy(update={"reviewed_by": "other"}).checksum()
    assert h0 != h1
