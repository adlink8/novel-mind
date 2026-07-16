"""Adversarial tests for Phase 17 qualification safety gates."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.narrative_memory.qualification_contracts import (
    QualificationVerdict,
)
from app.services.narrative_memory.qualification_fixtures import (
    FIXTURES_DIR,
    load_frozen_bundle,
    module_has_forbidden_capability,
)
from app.services.narrative_memory.qualification_runner import (
    default_generator,
    run_qualification,
    runner_has_promotion_capability,
)
from app.services.narrative_memory.qualification_verdict import evaluate_verdict
from app.services.narrative_memory.qualification_contracts import MetricCell, MetricStatus

pytestmark = [pytest.mark.unit, pytest.mark.adversarial]

HEX = "a" * 64
NM = Path(__file__).resolve().parents[2] / "app" / "services" / "narrative_memory"


def test_summary_citation_not_in_contracts():
    text = (NM / "qualification_contracts.py").read_text(encoding="utf-8")
    assert "candidate summary" not in text.lower() or "never" in text.lower()


def test_forbidden_imports_in_qualification_modules():
    for name in (
        "qualification_contracts.py",
        "qualification_fixtures.py",
        "qualification_metrics.py",
        "qualification_verdict.py",
        "qualification_runner.py",
        "qualification_baseline.py",
    ):
        hits = module_has_forbidden_capability(NM / name)
        hard = [h for h in hits if h.startswith("import:")]
        assert hard == [], f"{name}: {hard}"


def test_no_promotion_surface():
    assert runner_has_promotion_capability() is False
    for name in ("qualification_verdict.py", "qualification_runner.py"):
        src = (NM / name).read_text(encoding="utf-8")
        assert "prepare_baseline" not in src
        assert "commit_baseline" not in src
        assert "ActiveBaseline" not in src


@pytest.mark.asyncio
async def test_no_answer_hallucination_blocks():
    fixture, policy, _, _ = load_frozen_bundle(
        FIXTURES_DIR / "single_book_v1.json",
        FIXTURES_DIR / "policy_v1.json",
    )

    def lying_gen(**kwargs):
        out = default_generator(**kwargs)
        if kwargs.get("expected_answerability") == "no_answer":
            out = {
                **out,
                "answer": "Her name is Alice",
                "abstained": False,
                "faithfulness": 1.0,
            }
        return out

    result = await run_qualification(None, fixture, policy, generator=lying_gen)
    assert result.report is not None
    # false answer rate / abstention threshold should block
    assert result.report.verdict == QualificationVerdict.BLOCKED


@pytest.mark.asyncio
async def test_spoiler_leak_blocks_despite_high_scores():
    fixture, policy, _, _ = load_frozen_bundle(
        FIXTURES_DIR / "single_book_v1.json",
        FIXTURES_DIR / "policy_v1.json",
    )

    def leaky_gen(**kwargs):
        out = default_generator(**kwargs)
        if kwargs.get("expected_answerability") == "spoiler_risk":
            out = {
                **out,
                "answer": "mysterious person at north gate",
                "abstained": False,
                "spoiler_leaks": 1,
            }
        return out

    result = await run_qualification(None, fixture, policy, generator=leaky_gen)
    assert result.report is not None
    assert result.report.verdict == QualificationVerdict.BLOCKED
    assert any("spoiler" in r or "zero_tolerance" in r for r in result.report.reason_codes)


@pytest.mark.asyncio
async def test_cache_namespaces_isolated_per_strategy():
    fixture, policy, _, _ = load_frozen_bundle(
        FIXTURES_DIR / "single_book_v1.json",
        FIXTURES_DIR / "policy_v1.json",
    )
    result = await run_qualification(None, fixture, policy)
    namespaces = {(a["strategy"], a["cache_namespace"]) for a in result.artifacts}
    hier = {n for s, n in namespaces if s == "hierarchical_candidate"}
    base = {n for s, n in namespaces if s == "leaf_raw_baseline"}
    assert hier.isdisjoint(base)
    assert all("hierarchical_candidate" in n for n in hier)
    assert all("leaf_raw_baseline" in n for n in base)


def test_judge_false_positive_cannot_clear_hard_gate():
    from app.services.narrative_memory.qualification_fixtures import load_frozen_bundle as lfb

    _, policy, _, _ = lfb(
        FIXTURES_DIR / "single_book_v1.json",
        FIXTURES_DIR / "policy_v1.json",
    )
    cells = [
        MetricCell(
            metric_name="spoiler_leakage",
            numerator=2,
            denominator=1,
            value=2,
            unit="count",
            status=MetricStatus.OK,
        ),
        MetricCell(
            metric_name="faithfulness_mean",
            numerator=1.0,
            denominator=1,
            value=1.0,
            unit="score",
            status=MetricStatus.OK,
            strategy="hierarchical_candidate",
        ),
    ]
    report = evaluate_verdict(
        policy=policy,
        fixture_checksum=HEX,
        policy_checksum=policy.checksum(),
        metric_cells=cells,
        pointer_before_digest=HEX,
        pointer_after_digest=HEX,
    )
    assert report.verdict == QualificationVerdict.BLOCKED
