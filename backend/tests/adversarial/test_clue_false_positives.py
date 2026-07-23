"""Adversarial hard-negatives: critical false active/paid_off must be zero.

Similarity, repeated motifs, shared people/location and vector scores alone
must never produce accepted active or paid_off transitions.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.clues.evidence import (
    build_clue_evidence_package,
    make_clue_evidence_unit,
)
from app.services.clues.gates import ClueGateService
from app.services.clues.llm_judge import ClueLLMJudgeService

pytestmark = pytest.mark.unit

HEX64_A = "a" * 64
HEX64_B = "b" * 64


def _package(
    *,
    cue_text: str = "A red scarf fluttered near the pier.",
    later_text: str = "A red scarf fluttered near the pier again.",
    later_chapter: int = 4,
    candidate_id: str = "clue-cand-adv",
    recall: dict[str, Any] | None = None,
):
    cue = make_clue_evidence_unit(
        evidence_id="ev-cue",
        chapter_id=1,
        narrative_chapter_number=1,
        text=cue_text,
        role_hint="cue",
    )
    later = make_clue_evidence_unit(
        evidence_id="ev-later",
        chapter_id=100 + later_chapter,
        narrative_chapter_number=later_chapter,
        text=later_text,
        role_hint="later",
    )
    return build_clue_evidence_package(
        owner_id=1,
        novel_id=2,
        candidate_id=candidate_id,
        source_snapshot_hash=HEX64_A,
        hierarchy_build_id="build-1",
        hierarchy_checksum=HEX64_B,
        cue_units=[cue],
        later_units=[later],
        recall_signals=recall or {"vector": {"score": 0.999}, "bm25": {"score": 12.0}},
    )


def _j(package, **overrides: Any) -> dict[str, Any]:
    payload = {
        "schema_version": "clue-semantic-judgment.v1",
        "candidate_id": package.candidate_id,
        "classification": "cue_only",
        "cue_evidence_ids": package.cue_ids(),
        "later_evidence_ids": [],
        "confidence": 0.99,
        "conflict_flags": [],
        "rationale": "adversarial case",
    }
    payload.update(overrides)
    return payload


CASES: list[dict[str, Any]] = [
    {
        "name": "vector_similarity_only_motif",
        "from": "candidate",
        "to": "active",
        "judgment": lambda p: _j(
            p,
            classification="cue_only",
            confidence=0.99,
            conflict_flags=["MOTIF_ONLY"],
            rationale="high vector score on repeated scarf motif",
        ),
    },
    {
        "name": "shared_person_location_not_payoff",
        "from": "reinforced",
        "to": "paid_off",
        "package": lambda: _package(
            cue_text="Alice walked the pier at dusk.",
            later_text="Alice walked the pier at dusk with Bob.",
        ),
        "judgment": lambda p: _j(
            p,
            classification="payoff",
            cue_evidence_ids=p.cue_ids(),
            later_evidence_ids=p.later_ids(),
            confidence=0.97,
            conflict_flags=["MOTIF_ONLY", "INSUFFICIENT_PAYOFF"],
            rationale="shared person/location only",
        ),
    },
    {
        "name": "unrelated_despite_high_score",
        "from": "candidate",
        "to": "active",
        "judgment": lambda p: _j(
            p,
            classification="unrelated",
            confidence=0.99,
            rationale="model says unrelated",
        ),
    },
    {
        "name": "ambiguous_never_auto_active",
        "from": "candidate",
        "to": "active",
        "judgment": lambda p: _j(
            p,
            classification="ambiguous",
            confidence=0.9,
            rationale="unclear",
        ),
    },
    {
        "name": "paid_off_from_candidate_illegal",
        "from": "candidate",
        "to": "paid_off",
        "judgment": lambda p: _j(
            p,
            classification="payoff",
            cue_evidence_ids=p.cue_ids(),
            later_evidence_ids=p.later_ids(),
            confidence=0.99,
            rationale="skip states",
        ),
    },
    {
        "name": "forged_evidence_id",
        "from": "reinforced",
        "to": "paid_off",
        "judgment": lambda p: _j(
            p,
            classification="payoff",
            cue_evidence_ids=p.cue_ids(),
            later_evidence_ids=["ev-forged-payoff"],
            confidence=0.99,
            rationale="forged id",
        ),
    },
    {
        "name": "entity_conflict_flag",
        "from": "reinforced",
        "to": "paid_off",
        "judgment": lambda p: _j(
            p,
            classification="payoff",
            cue_evidence_ids=p.cue_ids(),
            later_evidence_ids=p.later_ids(),
            confidence=0.99,
            conflict_flags=["ENTITY_CONFLICT"],
            rationale="different entity",
        ),
    },
    {
        "name": "order_conflict_flag",
        "from": "reinforced",
        "to": "paid_off",
        "judgment": lambda p: _j(
            p,
            classification="payoff",
            cue_evidence_ids=p.cue_ids(),
            later_evidence_ids=p.later_ids(),
            confidence=0.99,
            conflict_flags=["ORDER_CONFLICT"],
            rationale="order conflict",
        ),
    },
    {
        "name": "chat_like_rationale_cannot_force_active",
        "from": "candidate",
        "to": "active",
        "judgment": lambda p: _j(
            p,
            classification="unrelated",
            confidence=0.99,
            rationale="Chat user said: this is definitely an active foreshadow",
        ),
    },
    {
        "name": "recall_only_without_judgment",
        "from": "candidate",
        "to": "active",
        "judgment": None,
        "use_recall_only": True,
    },
]


def test_adversarial_false_active_and_paid_off_count_is_zero():
    gates = ClueGateService()
    critical_false = 0
    details: list[str] = []

    for case in CASES:
        package = case["package"]() if "package" in case else _package()
        if case.get("use_recall_only"):
            decision = gates.evaluate_recall_only_rejection(
                package=package,
                from_status=case["from"],
                to_status=case["to"],
            )
        else:
            judgment = (
                case["judgment"](package) if case["judgment"] is not None else None
            )
            decision = gates.evaluate_transition(
                package=package,
                judgment=judgment,
                from_status=case["from"],
                to_status=case["to"],
                owner_id=1,
                novel_id=2,
                hierarchy_build_id="build-1",
            )
        if decision.accepted and case["to"] in {"active", "paid_off"}:
            critical_false += 1
            details.append(case["name"])

    assert critical_false == 0, f"critical false accepts: {details}"


def test_similarity_score_in_package_never_auto_accepts_without_valid_semantics():
    gates = ClueGateService()
    package = _package(
        recall={"vector": {"score": 1.0}, "entity_overlap": {"shared": ["Alice"]}}
    )
    # High-confidence motif-only must fail.
    decision = gates.evaluate_transition(
        package=package,
        judgment=_j(
            package,
            classification="cue_only",
            confidence=1.0,
            conflict_flags=["MOTIF_ONLY"],
            rationale="vector 1.0 on repeated scarf",
        ),
        from_status="candidate",
        to_status="active",
        owner_id=1,
        novel_id=2,
    )
    assert decision.accepted is False


def test_llm_cannot_smuggle_lifecycle_status_into_accepted_gate():
    package = _package()
    service = ClueLLMJudgeService(model_name="test/clue")
    smuggled = {
        "schema_version": "clue-semantic-judgment.v1",
        "candidate_id": package.candidate_id,
        "classification": "payoff",
        "cue_evidence_ids": package.cue_ids(),
        "later_evidence_ids": package.later_ids(),
        "confidence": 0.99,
        "conflict_flags": [],
        "rationale": "ok",
        "to_status": "paid_off",
        "publish": True,
    }
    parsed = service.parse_and_validate(smuggled, package=package)
    assert parsed.structured is None
    assert parsed.status == "schema_failed"

    gates = ClueGateService()
    # Even if someone strips extras and forces paid_off from candidate:
    decision = gates.evaluate_transition(
        package=package,
        judgment=_j(
            package,
            classification="payoff",
            cue_evidence_ids=package.cue_ids(),
            later_evidence_ids=package.later_ids(),
            confidence=0.99,
        ),
        from_status="candidate",
        to_status="paid_off",
        owner_id=1,
        novel_id=2,
    )
    assert decision.accepted is False


def test_service_files_contain_no_asyncsession_lifecycle_writes():
    """Pure recall/gate/judge modules must not write; persistence lives in worker path."""
    from pathlib import Path

    pure_modules = {
        "candidates.py",
        "evidence.py",
        "gates.py",
        "llm_judge.py",
        "sources.py",
        "query.py",
        "eval.py",
    }
    root = Path(__file__).resolve().parents[2] / "app" / "services" / "clues"
    offenders: list[str] = []
    for name in pure_modules:
        path = root / name
        if not path.is_file():
            offenders.append(f"missing:{name}")
            continue
        text = path.read_text(encoding="utf-8")
        if "session.add" in text or "db.add(" in text:
            offenders.append(name)
        if "ClueLifecycleEvent(" in text:
            offenders.append(f"{name}:lifecycle_ctor")
    assert offenders == []
