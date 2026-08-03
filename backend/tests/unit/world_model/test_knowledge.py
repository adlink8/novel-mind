"""Phase 27-02 epistemic history unit tests (REQ-WM-02, D-01/D-05/D-06).

Coverage: mistaken beliefs stay visible at the correct cutoff, hidden knowledge
never leaks before disclosure, future cutoffs are rejected, wrong-POV and
wrong-owner reads fail closed, state transitions never skip unevidenced nodes,
Reader Chat / user conversations can never serialize as canon_fact, and
no-answer / abstention never fabricates knowledge.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.world_model.contracts import (
    Authority,
    EvidenceRef,
    GateStatus,
)
from app.services.world_model.knowledge import (
    EpistemicAspect,
    EpistemicClaim,
    EpistemicGate,
    EpistemicStatus,
    GateReason,
    KnowledgeCandidateProjection,
    KnowledgeResultStatus,
    build_knowledge_projection,
    claim_checksum,
    projection_verified,
)
from app.services.world_model.queries import EpistemicQueryEngine

pytestmark = pytest.mark.unit

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures" / "world_model" / "epistemic_v1.json")
    .read_text(encoding="utf-8")
)


def scenario(name: str) -> dict:
    return FIXTURE["scenarios"][name]


def make_gate(name: str, *, version_id: int | None = None) -> EpistemicGate:
    scope = scenario(name)["scope"]
    return EpistemicGate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=version_id if version_id is not None else scope["version_id"],
        source_snapshot_hash=scope["source_snapshot_hash"],
        disclosure_cutoff=scope["disclosure_cutoff"],
        approvals=frozenset(scope["approvals"]),
    )


def gated_claims(name: str) -> tuple[list[EpistemicClaim], EpistemicGate]:
    """Run one scenario through the gate; return (passed claims, gate)."""
    gate = make_gate(name)
    claims: list[EpistemicClaim] = []
    for raw in scenario(name)["claims"]:
        result = gate.validate_claim(EpistemicClaim.model_validate(raw))
        if result.claim is not None:
            claims.append(result.claim)
    return claims, gate


def valid_projection() -> KnowledgeCandidateProjection:
    claims, _ = gated_claims("valid")
    assert len(claims) == 8, "valid scenario must gate all claims"
    return build_knowledge_projection(
        owner_id=1, novel_id=1, version_id=1, claims=claims
    )


def engine_for(name: str) -> EpistemicQueryEngine:
    claims, _ = gated_claims(name)
    return EpistemicQueryEngine(claims)


# ---------------------------------------------------------------------------
# Mistaken beliefs and truths at the correct cutoff (D-05)
# ---------------------------------------------------------------------------


def test_mistaken_belief_visible_until_truth_cutoff():
    engine = engine_for("mistaken_belief")
    before = engine.query_character_knowledge(
        owner_id=1, novel_id=1, version_id=2, subject="lin-an", cutoff=4
    )
    assert before.status == KnowledgeResultStatus.ANSWERED
    keys = {claim.knowledge_key for claim in before.claims}
    assert keys == {"k-belief-withdrawn"}
    assert before.claims[0].epistemic_status == EpistemicStatus.MISTAKEN_BELIEF
    assert before.claims[0].authority == Authority.PROBABLE_INFERENCE
    # The canon truth (chapter 5) is not visible at cutoff 4.
    assert "k-truth-siege" not in keys

    after = engine.query_character_knowledge(
        owner_id=1, novel_id=1, version_id=2, subject="lin-an", cutoff=6
    )
    assert {claim.knowledge_key for claim in after.claims} == {
        "k-belief-withdrawn",
        "k-truth-siege",
    }
    # The mistaken belief is preserved next to the truth, never overwritten.
    by_key = {claim.knowledge_key: claim for claim in after.claims}
    assert by_key["k-belief-withdrawn"].epistemic_status == EpistemicStatus.MISTAKEN_BELIEF
    assert by_key["k-truth-siege"].authority == Authority.CANON_FACT
    assert by_key["k-truth-siege"].epistemic_status == EpistemicStatus.ASSERTED


def test_mistaken_belief_queryable_in_full_history():
    engine = engine_for("valid")
    history = engine.query_character_history(
        owner_id=1, novel_id=1, version_id=1, subject="lin-an"
    )
    keys = {claim.knowledge_key for claim in history}
    assert "k-belief-ally" in keys
    assert "k-truth-ally" in keys
    assert "k-hidden-inheritance" in keys
    mistaken = engine.query_by_status(
        owner_id=1, novel_id=1, version_id=1, status=EpistemicStatus.MISTAKEN_BELIEF
    )
    assert [claim.knowledge_key for claim in mistaken] == ["k-belief-ally"]


# ---------------------------------------------------------------------------
# Hidden knowledge never leaks before disclosure (D-05)
# ---------------------------------------------------------------------------


def test_hidden_knowledge_never_leaks_before_disclosure():
    engine = engine_for("hidden_fact")
    answer = engine.query_character_knowledge(
        owner_id=1, novel_id=1, version_id=3, subject="lin-an", cutoff=3
    )
    assert answer.status == KnowledgeResultStatus.ABSTAINED
    assert answer.claims == ()
    # The author can still query the hidden fact by explicit status.
    hidden = engine.query_by_status(
        owner_id=1, novel_id=1, version_id=3, status=EpistemicStatus.HIDDEN_KNOWLEDGE
    )
    assert [claim.knowledge_key for claim in hidden] == ["k-hidden-twin"]
    assert hidden[0].known_at == 2
    assert hidden[0].disclosure_cutoff == 9


def test_hidden_knowledge_visible_after_disclosure():
    engine = engine_for("valid")
    before = engine.query_character_knowledge(
        owner_id=1, novel_id=1, version_id=1, subject="lin-an", cutoff=7
    )
    assert "k-hidden-inheritance" not in {
        claim.knowledge_key for claim in before.claims
    }
    after = engine.query_character_knowledge(
        owner_id=1, novel_id=1, version_id=1, subject="lin-an", cutoff=8
    )
    assert "k-hidden-inheritance" in {claim.knowledge_key for claim in after.claims}


# ---------------------------------------------------------------------------
# Future cutoff and evidence beyond cutoff fail closed (D-05)
# ---------------------------------------------------------------------------


def test_future_cutoff_claim_rejected_at_the_gate():
    gate = make_gate("future_cutoff")
    result = gate.validate_claim(
        EpistemicClaim.model_validate(scenario("future_cutoff")["claims"][0])
    )
    assert result.claim is None
    assert GateReason.SPOILER_CUTOFF in result.reason_codes


def test_evidence_beyond_cutoff_rejected():
    gate = make_gate("valid")
    raw = json.loads(json.dumps(scenario("valid")["claims"][0]))  # deep copy
    raw["disclosure_cutoff"] = 2
    raw["source_refs"][0]["chapter_number"] = 3  # evidence after the cutoff
    result = gate.validate_claim(EpistemicClaim.model_validate(raw))
    assert result.claim is None
    assert GateReason.EVIDENCE_BEYOND_CUTOFF in result.reason_codes


# ---------------------------------------------------------------------------
# Wrong POV and wrong owner fail closed
# ---------------------------------------------------------------------------


def test_wrong_pov_reads_fail_closed():
    engine = engine_for("wrong_pov")
    # Querying as a different POV sees nothing authored from the other character.
    other_pov = engine.query_character_knowledge(
        owner_id=1, novel_id=1, version_id=5, subject="mei-niang", cutoff=3, pov="lin-an"
    )
    assert other_pov.status == KnowledgeResultStatus.ABSTAINED
    assert other_pov.claims == ()
    # The owning POV sees the claim.
    own_pov = engine.query_character_knowledge(
        owner_id=1, novel_id=1, version_id=5, subject="mei-niang", cutoff=3, pov="mei-niang"
    )
    assert own_pov.status == KnowledgeResultStatus.ANSWERED
    assert own_pov.claims[0].knowledge_key == "k-other-pov"


def test_wrong_owner_claim_rejected_and_reads_abstain():
    gate = make_gate("wrong_owner")
    result = gate.validate_claim(
        EpistemicClaim.model_validate(scenario("wrong_owner")["claims"][0])
    )
    assert result.claim is None
    assert GateReason.WRONG_OWNER in result.reason_codes

    engine = engine_for("valid")
    cross_owner = engine.query_character_knowledge(
        owner_id=2, novel_id=1, version_id=1, subject="lin-an", cutoff=6
    )
    assert cross_owner.status == KnowledgeResultStatus.ABSTAINED


def test_stale_version_claim_rejected():
    gate = make_gate("valid", version_id=9)
    result = gate.validate_claim(
        EpistemicClaim.model_validate(scenario("valid")["claims"][0])
    )
    assert result.claim is None
    assert GateReason.STALE_VERSION in result.reason_codes


# ---------------------------------------------------------------------------
# Reader Chat / user conversation can never be canon facts (D-06)
# ---------------------------------------------------------------------------


def test_reader_chat_can_never_serialize_as_canon_fact():
    gate = make_gate("chat_contamination")
    results = [
        gate.validate_claim(EpistemicClaim.model_validate(raw))
        for raw in scenario("chat_contamination")["claims"]
    ]
    assert all(result.claim is None for result in results)
    assert {
        reason
        for result in results
        for reason in result.reason_codes
        if reason == GateReason.CHAT_NOT_FACT_SOURCE
    } == {GateReason.CHAT_NOT_FACT_SOURCE}
    # Even with canon_fact approved, Reader Chat stays a non-source.
    assert (
        GateReason.CHAT_NOT_FACT_SOURCE in results[0].reason_codes
    )


def test_user_conversation_never_canon_and_not_silently_promoted():
    gate = make_gate("chat_contamination")
    raw = json.loads(json.dumps(scenario("chat_contamination")["claims"][1]))
    raw["authority"] = "canon_fact"  # escalation attempt from user chat
    result = gate.validate_claim(EpistemicClaim.model_validate(raw))
    assert result.claim is None
    assert GateReason.CHAT_NOT_FACT_SOURCE in result.reason_codes


# ---------------------------------------------------------------------------
# State transitions never skip unevidenced nodes (REQ-WM-02)
# ---------------------------------------------------------------------------


def test_state_history_transitions_do_not_skip_nodes():
    projection = valid_projection()
    history = EpistemicQueryEngine(projection.claims).query_character_history(
        owner_id=1, novel_id=1, version_id=1, subject="lin-an", aspect=EpistemicAspect.STATE
    )
    keys = [claim.knowledge_key for claim in history]
    assert keys == ["k-state-arrival", "k-state-court", "k-state-declare"]
    assert [claim.known_at for claim in history] == [1, 4, 6]
    # Each node except the first chains to the evidence-backed predecessor.
    assert history[0].transition_from is None
    assert history[1].transition_from == "k-state-arrival"
    assert history[2].transition_from == "k-state-court"
    for claim in history:
        assert claim.gate_status == GateStatus.PASSED


def test_transition_gap_is_rejected_by_projection():
    claims, _ = gated_claims("transition_gap")
    assert len(claims) == 1
    with pytest.raises(ValueError):
        build_knowledge_projection(
            owner_id=1, novel_id=1, version_id=8, claims=claims
        )


def test_transition_cannot_skip_an_unevidenced_node():
    # A transition source that was never gate-passed is an unevidenced node;
    # the projection must reject the chain instead of silently skipping it.
    raw = scenario("valid")["claims"]
    by_key = {claim["knowledge_key"]: claim for claim in raw}
    unevidenced = {
        **by_key["k-state-arrival"],
        "gate_status": "pending",  # never passed the evidence gate
    }
    claims = [
        EpistemicClaim.model_validate(
            {**by_key["k-state-court"], "transition_from": "k-state-arrival"}
        ),
        EpistemicClaim.model_validate(unevidenced),
    ]
    with pytest.raises(ValueError):
        build_knowledge_projection(
            owner_id=1, novel_id=1, version_id=1, claims=claims
        )


# ---------------------------------------------------------------------------
# Abstention never fabricates knowledge (D-06)
# ---------------------------------------------------------------------------


def test_abstention_is_first_class_and_fabricates_nothing():
    engine = engine_for("abstention")
    answer = engine.query_character_knowledge(
        owner_id=1, novel_id=1, version_id=9, subject="lin-an", cutoff=1
    )
    assert answer.status == KnowledgeResultStatus.ABSTAINED
    assert answer.claims == ()
    assert answer.evidence == ()
    assert "abstaining" in answer.message
    # The same claim is visible once the character actually knows it.
    later = engine.query_character_knowledge(
        owner_id=1, novel_id=1, version_id=9, subject="lin-an", cutoff=3
    )
    assert later.status == KnowledgeResultStatus.ANSWERED
    assert later.claims[0].knowledge_key == "k-known-later"


# ---------------------------------------------------------------------------
# Authority filter and candidate-only status
# ---------------------------------------------------------------------------


def test_authority_filter_applies_after_scoping():
    engine = engine_for("mistaken_belief")
    only_canon = engine.query_character_knowledge(
        owner_id=1,
        novel_id=1,
        version_id=2,
        subject="lin-an",
        cutoff=6,
        authorities=frozenset({Authority.CANON_FACT}),
    )
    assert [claim.knowledge_key for claim in only_canon.claims] == ["k-truth-siege"]
    only_inference = engine.query_character_knowledge(
        owner_id=1,
        novel_id=1,
        version_id=2,
        subject="lin-an",
        cutoff=6,
        authorities=frozenset({Authority.PROBABLE_INFERENCE}),
    )
    assert [claim.knowledge_key for claim in only_inference.claims] == [
        "k-belief-withdrawn"
    ]


def test_candidate_only_claims_are_labeled_not_promoted():
    claims, _ = gated_claims("valid")
    engine = EpistemicQueryEngine(
        [claim.model_copy(update={"gate_status": GateStatus.PENDING}) for claim in claims]
    )
    answer = engine.query_character_knowledge(
        owner_id=1, novel_id=1, version_id=1, subject="lin-an", cutoff=2
    )
    assert answer.status == KnowledgeResultStatus.CANDIDATE_ONLY
    assert answer.claims
    assert not answer.has_approval


# ---------------------------------------------------------------------------
# Immutable contracts and lineage
# ---------------------------------------------------------------------------


def test_projection_is_immutable_and_hash_sealed():
    projection = valid_projection()
    assert projection_verified(projection)
    assert projection.schema_version == "world-model-knowledge.v1"
    fields = set(projection.model_dump().keys())
    for forbidden in ("active_pointer", "promotion", "current_revision", "cutover"):
        assert forbidden not in fields


def test_projection_rejects_cross_scope_rows():
    projection = valid_projection()
    hijack = projection.claims[0].model_copy(update={"owner_id": 2})
    with pytest.raises(ValueError):
        build_knowledge_projection(
            owner_id=1, novel_id=1, version_id=1, claims=[hijack]
        )


def test_lineage_must_end_at_self():
    raw = scenario("valid")["claims"][0]
    with pytest.raises(ValidationError):
        EpistemicClaim.model_validate({**raw, "lineage": ["k-some-other-key"]})
    with pytest.raises(ValidationError):
        EpistemicClaim.model_validate({**raw, "lineage": []})


def test_known_at_cannot_exceed_disclosure_cutoff():
    raw = scenario("valid")["claims"][0]
    with pytest.raises(ValidationError):
        EpistemicClaim.model_validate({**raw, "known_at": 9, "disclosure_cutoff": 2})


def test_claims_reject_untyped_shape_violations():
    raw = scenario("valid")["claims"][0]
    with pytest.raises(ValidationError):
        EpistemicClaim.model_validate({**raw, "source_refs": "not-a-list"})
    with pytest.raises(ValidationError):
        EpistemicClaim.model_validate({**raw, "confidence": 2.0})
    with pytest.raises(ValidationError):
        EpistemicClaim.model_validate({**raw, "aspect": "not_an_aspect"})


def test_checksum_is_content_anchored():
    projection = valid_projection()
    claim = projection.claims[0]
    mutated = claim.model_copy(update={"authority": Authority.CANON_FACT})
    assert claim_checksum(mutated) != claim_checksum(claim)
    # Evidence is part of the claim checksum — dropping a ref changes the hash.
    without_evidence = claim.model_copy(
        update={"source_refs": claim.source_refs[1:]}
    ) if len(claim.source_refs) > 1 else claim.model_copy(
        update={"proposition": "不同表述"}
    )
    assert claim_checksum(without_evidence) != claim_checksum(claim)


def test_query_lineage_returns_version_chain():
    engine = engine_for("valid")
    lineage = engine.query_lineage(
        owner_id=1, novel_id=1, knowledge_key="k-state-court"
    )
    keys = [claim.knowledge_key for claim in lineage]
    # k-state-court itself and the claim whose lineage includes it.
    assert "k-state-court" in keys
    for claim in lineage:
        assert claim.knowledge_key == "k-state-court" or (
            "k-state-court" in claim.lineage
        )


def test_evidence_is_returned_with_answers():
    engine = engine_for("valid")
    answer = engine.query_character_knowledge(
        owner_id=1, novel_id=1, version_id=1, subject="lin-an", cutoff=6
    )
    assert answer.status == KnowledgeResultStatus.ANSWERED
    assert answer.has_approval
    assert answer.evidence
    for ref in answer.evidence:
        assert isinstance(ref, EvidenceRef)
        assert len(ref.content_hash) == 64
        assert len(ref.source_snapshot_hash) == 64


def test_query_engine_is_read_only():
    members = {
        name for name, _ in EpistemicQueryEngine.__dict__.items()
        if callable(getattr(EpistemicQueryEngine, name, None))
    }
    assert not {m for m in members if m.startswith(("append", "write", "update"))}
    assert "query_character_knowledge" in members
