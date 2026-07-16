"""Unit tests for pure Phase 17 verdict evaluator."""

from __future__ import annotations

import pytest

from app.services.narrative_memory.qualification_contracts import (
    MetricCell,
    MetricStatus,
    QualificationPolicy,
    QualificationVerdict,
)
from app.services.narrative_memory.qualification_fixtures import (
    FIXTURES_DIR,
    load_frozen_bundle,
)
from app.services.narrative_memory.qualification_metrics import build_complete_report_cells
from app.services.narrative_memory.qualification_runner import run_qualification
from app.services.narrative_memory.qualification_verdict import (
    evaluate_verdict,
    verdict_has_promotion_capability,
    verdict_has_provider_capability,
)

pytestmark = pytest.mark.unit

HEX = "a" * 64


def _policy() -> QualificationPolicy:
    return QualificationPolicy.model_validate(
        __import__("json").loads((FIXTURES_DIR / "policy_v1.json").read_text(encoding="utf-8"))
    )


def _ok_cells():
    arts = []
    for strat in ("hierarchical_candidate", "leaf_raw_baseline"):
        for case_key, bucket, gold, expected, abstain in (
            ("local_01", "local", ["leaf-ch1-01"], "answerable", False),
            ("arc_01", "cross_chapter_arc", ["leaf-ch2-01"], "answerable", False),
            ("global_01", "whole_book_global", ["leaf-ch3-01"], "answerable", False),
            ("noans_01", "no_answer", [], "no_answer", True),
            ("spoiler_01", "spoiler", [], "spoiler_risk", True),
        ):
            arts.append(
                {
                    "case_key": case_key,
                    "bucket": bucket,
                    "strategy": strat,
                    "retrieved_leaf_ids": gold if gold else [],
                    "gold_leaf_ids": gold,
                    "graded_relevance": {g: 3.0 for g in gold},
                    "route_allowed": ["local", "arc", "global"],
                    "route_chosen": "local",
                    "fallback_used": False,
                    "citations_accepted": max(len(gold), 1) if not abstain else 0,
                    "citations_total": max(len(gold), 1) if not abstain else 0,
                    "abstained": abstain,
                    "expected_answerability": expected,
                    "spoiler_leaks": 0,
                    "critical_unsupported": 0,
                    "faithfulness": 0.9,
                    "relevance": 0.9 if not abstain else 0.0,
                    "latency_ms": 20.0,
                    "calls": 2,
                    "input_tokens": 10,
                    "output_tokens": 10,
                    "cost_usd": 0.001,
                    "cache_hit": False,
                }
            )
    reuse = {
        "rebuilt_count": 1,
        "carried_count": 2,
        "stale_count": 0,
        "observed_actual": {"cost_usd": 0.01},
        "full_rebuild_upper_bound": {"cost_usd": 0.05},
        "avoided_upper_bound": {"cost_usd": 0.04},
    }
    return build_complete_report_cells(arts, reuse=reuse)


@pytest.mark.asyncio
async def test_qualified_when_all_gates_pass():
    fixture, policy, fx, pol = load_frozen_bundle(
        FIXTURES_DIR / "single_book_v1.json",
        FIXTURES_DIR / "policy_v1.json",
    )
    result = await run_qualification(None, fixture, policy)
    assert result.report is not None
    # deterministic defaults should qualify
    assert result.report.verdict == QualificationVerdict.QUALIFIED_CANDIDATE
    assert result.report.reason_codes == ()
    assert "Does not promote" in result.report.disclaimer


def test_spoiler_blocks_even_with_perfect_judge():
    policy = _policy()
    cells = list(_ok_cells())
    cells.append(
        MetricCell(
            metric_name="spoiler_leakage",
            numerator=1,
            denominator=1,
            value=1,
            unit="count",
            status=MetricStatus.OK,
            case_ids=("spoiler_01",),
            strategy="hierarchical_candidate",
        )
    )
    report = evaluate_verdict(
        policy=policy,
        fixture_checksum=HEX,
        policy_checksum=policy.checksum(),
        metric_cells=cells,
        pointer_before_digest=HEX,
        pointer_after_digest=HEX,
        verifier_checksum=HEX,
    )
    assert report.verdict == QualificationVerdict.BLOCKED
    assert any("spoiler" in r for r in report.reason_codes)


def test_judge_cannot_override_unsupported():
    policy = _policy()
    cells = list(_ok_cells())
    cells.append(
        MetricCell(
            metric_name="critical_unsupported",
            numerator=1,
            denominator=1,
            value=1,
            unit="count",
            status=MetricStatus.OK,
            case_ids=("local_01",),
            strategy="hierarchical_candidate",
        )
    )
    report = evaluate_verdict(
        policy=policy,
        fixture_checksum=HEX,
        policy_checksum=policy.checksum(),
        metric_cells=cells,
        pointer_before_digest=HEX,
        pointer_after_digest=HEX,
        verifier_checksum=HEX,
    )
    assert report.verdict == QualificationVerdict.BLOCKED


def test_pointer_mismatch_blocks():
    policy = _policy()
    report = evaluate_verdict(
        policy=policy,
        fixture_checksum=HEX,
        policy_checksum=policy.checksum(),
        metric_cells=_ok_cells(),
        pointer_equal=False,
        pointer_before_digest=HEX,
        pointer_after_digest="b" * 64,
        verifier_checksum=HEX,
    )
    assert report.verdict == QualificationVerdict.BLOCKED
    assert any("pointer" in r for r in report.reason_codes)


def test_reason_codes_sorted_stable():
    policy = _policy()
    report = evaluate_verdict(
        policy=policy,
        fixture_checksum=HEX,
        policy_checksum=policy.checksum(),
        metric_cells=[],
        preflight_reasons=["zz_reason", "aa_reason"],
        scope_ok=False,
        build_complete=False,
    )
    assert list(report.reason_codes) == sorted(report.reason_codes)


def test_no_promotion_capability():
    assert verdict_has_promotion_capability() is False
    assert verdict_has_provider_capability() is False
