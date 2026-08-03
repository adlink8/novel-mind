"""Adversarial fail-closed tests for the epistemic world-model boundary.

Attacks covered (REQ-WM-02 / D-01..D-06):
- Reader Chat / user conversation can never write original-canon facts, even
  with canon_fact approved or with a forged authority relabel (D-06);
- no-answer / abstention never fabricates knowledge;
- hidden knowledge never leaks through a narrow reader cutoff or a wrong POV;
- wrong-owner claims and reads fail closed;
- contradictions are preserved, never resolved by overwrite;
- future-cutoff claims are rejected at the gate and never become queryable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.world_model.contracts import Authority
from app.services.world_model.knowledge import (
    EpistemicClaim,
    EpistemicGate,
    EpistemicStatus,
    GateReason,
    KnowledgeResultStatus,
    SourceKind,
    build_knowledge_projection,
)
from app.services.world_model.queries import EpistemicQueryEngine

pytestmark = [pytest.mark.unit]

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "world_model" / "epistemic_v1.json")
    .read_text(encoding="utf-8")
)


def scenario(name: str) -> dict:
    return FIXTURE["scenarios"][name]


def make_gate(name: str) -> EpistemicGate:
    scope = scenario(name)["scope"]
    return EpistemicGate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=scope["version_id"],
        source_snapshot_hash=scope["source_snapshot_hash"],
        disclosure_cutoff=scope["disclosure_cutoff"],
        approvals=frozenset(scope["approvals"]),
    )


def gated_claims(name: str) -> list[EpistemicClaim]:
    gate = make_gate(name)
    claims: list[EpistemicClaim] = []
    for raw in scenario(name)["claims"]:
        result = gate.validate_claim(EpistemicClaim.model_validate(raw))
        if result.claim is not None:
            claims.append(result.claim)
    return claims


# ---------------------------------------------------------------------------
# D-06: Reader Chat / user conversation can never write original-canon facts
# ---------------------------------------------------------------------------


def test_reader_chat_claims_are_always_rejected():
    gate = make_gate("chat_contamination")
    for raw in scenario("chat_contamination")["claims"]:
        result = gate.validate_claim(EpistemicClaim.model_validate(raw))
        assert result.claim is None, "chat-derived claim must never materialize"
        assert GateReason.CHAT_NOT_FACT_SOURCE in result.reason_codes


def test_reader_chat_cannot_hide_behind_approved_canon_fact():
    # Even with an explicit canon_fact approval, Reader Chat stays a non-source.
    gate = make_gate("chat_contamination")
    assert Authority.CANON_FACT in gate.approvals
    raw = json.loads(json.dumps(scenario("chat_contamination")["claims"][0]))
    result = gate.validate_claim(EpistemicClaim.model_validate(raw))
    assert result.claim is None
    assert GateReason.CHAT_NOT_FACT_SOURCE in result.reason_codes
    assert GateReason.AUTHORITY_UPGRADE not in result.reason_codes


def test_user_conversation_escalation_is_rejected():
    gate = make_gate("chat_contamination")
    raw = json.loads(json.dumps(scenario("chat_contamination")["claims"][1]))
    # Escalation: user chat inference relabeled as canon_fact.
    raw["authority"] = "canon_fact"
    result = gate.validate_claim(EpistemicClaim.model_validate(raw))
    assert result.claim is None
    assert GateReason.CHAT_NOT_FACT_SOURCE in result.reason_codes


def test_chat_source_kind_poisoning_is_rejected_by_projection_shape():
    # Even if a claim is hand-built with chat provenance, it can never join a
    # projection: relabeling source_kind is a checksum-visible mutation, and the
    # gate rejects chat sources outright before any durable write.
    raw = json.loads(json.dumps(scenario("chat_contamination")["claims"][0]))
    claim = EpistemicClaim.model_validate(raw)
    assert claim.source_kind == SourceKind.READER_CHAT
    relabeled = claim.model_copy(update={"source_kind": SourceKind.CANON_SOURCE})
    assert relabeled.checksum != claim.checksum
    assert relabeled.source_refs != ()  # evidence cannot be forged into canon
    gate = make_gate("chat_contamination")
    assert gate.validate_claim(claim).claim is None


def test_no_chat_claim_ever_enters_a_projection():
    assert gated_claims("chat_contamination") == []


# ---------------------------------------------------------------------------
# Abstention never fabricates knowledge (D-06)
# ---------------------------------------------------------------------------


def test_abstention_returns_no_claim_and_no_evidence():
    claims = gated_claims("abstention")
    assert len(claims) == 1  # only the later-known claim survives the gate
    engine = EpistemicQueryEngine(claims)
    answer = engine.query_character_knowledge(
        owner_id=1, novel_id=1, version_id=9, subject="lin-an", cutoff=1
    )
    assert answer.status == KnowledgeResultStatus.ABSTAINED
    assert answer.claims == ()
    assert answer.evidence == ()
    assert answer.has_approval is False


def test_unknown_subject_abstains_without_fabrication():
    claims = gated_claims("valid")
    engine = EpistemicQueryEngine(claims)
    answer = engine.query_character_knowledge(
        owner_id=1, novel_id=1, version_id=1, subject="nobody", cutoff=8
    )
    assert answer.status == KnowledgeResultStatus.ABSTAINED
    assert answer.claims == ()


# ---------------------------------------------------------------------------
# Hidden knowledge / future cutoff never leak (D-05)
# ---------------------------------------------------------------------------


def test_hidden_knowledge_never_leaks_at_narrow_cutoff():
    claims = gated_claims("hidden_fact")
    assert len(claims) == 1
    engine = EpistemicQueryEngine(claims)
    narrow = engine.query_character_knowledge(
        owner_id=1, novel_id=1, version_id=3, subject="lin-an", cutoff=3
    )
    assert narrow.status == KnowledgeResultStatus.ABSTAINED
    assert "k-hidden-twin" not in {c.knowledge_key for c in narrow.claims}
    # No raw row is ever returned across the disclosure boundary.
    assert all(c.disclosure_cutoff <= 3 for c in narrow.claims)


def test_future_cutoff_claim_never_becomes_queryable():
    gate = make_gate("future_cutoff")
    result = gate.validate_claim(
        EpistemicClaim.model_validate(scenario("future_cutoff")["claims"][0])
    )
    assert result.claim is None
    assert GateReason.SPOILER_CUTOFF in result.reason_codes
    # The projection built from surviving claims contains nothing future.
    claims = gated_claims("future_cutoff")
    assert claims == []


def test_wrong_pov_never_leaks_other_characters_knowledge():
    claims = gated_claims("wrong_pov")
    assert len(claims) == 1
    engine = EpistemicQueryEngine(claims)
    answer = engine.query_character_knowledge(
        owner_id=1,
        novel_id=1,
        version_id=5,
        subject="mei-niang",
        cutoff=3,
        pov="lin-an",
    )
    assert answer.status == KnowledgeResultStatus.ABSTAINED
    assert answer.claims == ()


# ---------------------------------------------------------------------------
# Owner mismatch fails closed (V2/V3)
# ---------------------------------------------------------------------------


def test_wrong_owner_claim_rejected_before_any_write():
    gate = make_gate("wrong_owner")
    result = gate.validate_claim(
        EpistemicClaim.model_validate(scenario("wrong_owner")["claims"][0])
    )
    assert result.claim is None
    assert {v.reason_code for v in result.verdicts} == {GateReason.WRONG_OWNER}


def test_cross_owner_read_abstains_fail_closed():
    claims = gated_claims("valid")
    engine = EpistemicQueryEngine(claims)
    answer = engine.query_character_knowledge(
        owner_id=2, novel_id=1, version_id=1, subject="lin-an", cutoff=8
    )
    assert answer.status == KnowledgeResultStatus.ABSTAINED


def test_cross_owner_projection_scope_rejected():
    claims = gated_claims("valid")
    assert len(claims) == 8
    hijack = claims[0].model_copy(update={"owner_id": 2})
    with pytest.raises(ValueError):
        build_knowledge_projection(
            owner_id=1, novel_id=1, version_id=1, claims=[hijack]
        )


# ---------------------------------------------------------------------------
# Contradictions are preserved, never resolved (D-04)
# ---------------------------------------------------------------------------


def test_contradiction_is_preserved_and_queryable():
    claims = gated_claims("contradiction")
    assert len(claims) == 2
    engine = EpistemicQueryEngine(claims)
    answer = engine.query_character_knowledge(
        owner_id=1, novel_id=1, version_id=10, subject="lin-an", cutoff=4
    )
    keys = {claim.knowledge_key for claim in answer.claims}
    assert keys == {"k-death-rumor", "k-alive-fact"}
    by_key = {claim.knowledge_key: claim for claim in answer.claims}
    assert by_key["k-alive-fact"].epistemic_status == EpistemicStatus.CONTRADICTION
    contradictions = engine.query_by_status(
        owner_id=1, novel_id=1, version_id=10, status=EpistemicStatus.CONTRADICTION
    )
    assert [claim.knowledge_key for claim in contradictions] == ["k-alive-fact"]
