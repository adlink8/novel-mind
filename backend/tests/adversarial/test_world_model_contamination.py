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

from app.services.queryplan.adapters import (
    READER_WORLD_PROJECTION,
    SourceSnapshot,
    run_plan_adapters,
)
from app.services.queryplan.parser import parse_query_plan
from app.services.queryplan.schemas import QueryDimension, QueryPlan
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
from app.services.world_model.queries import (
    EpistemicQueryEngine,
    WorldProjectionAnswer,
    world_projection_reader,
)

pytestmark = [pytest.mark.unit]

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "world_model"
        / "epistemic_v1.json"
    ).read_text(encoding="utf-8")
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


# ===========================================================================
# Phase 27-04 world projection: user interpretation isolation + chat bounds
# (REQ-WM-04, D-06)
# ===========================================================================


def _world_claim(
    *,
    key: str,
    authority: str = "probable_inference",
    disclosure_cutoff: int = 3,
    gate_status: str = "passed",
    source_kind: str = "canon_source",
    snapshot_hash: str = "c" * 64,
    source_start: int = 0,
    source_end: int = 12,
) -> EpistemicClaim:
    return EpistemicClaim(
        claim_kind="character_knowledge",
        knowledge_key=key,
        subject="lin-an",
        aspect="knowledge",
        proposition=f"claim {key}",
        known_at=disclosure_cutoff,
        disclosure_cutoff=disclosure_cutoff,
        pov="lin-an",
        pov_kind="character",
        source_kind=source_kind,
        authority=authority,
        confidence=0.9,
        epistemic_status=EpistemicStatus.ASSERTED,
        transition_from=None,
        lineage=[key],
        source_refs=(
            {
                "evidence_id": f"ev-{key}",
                "chapter_id": 1,
                "chapter_number": 1,
                "source_start": source_start,
                "source_end": source_end,
                "content_hash": "1" * 64,
                "source_snapshot_hash": snapshot_hash,
            },
        ),
        gate_status=gate_status,
        gate_reason=None,
        owner_id=1,
        novel_id=1,
        version_id=1,
    )


def test_world_projection_isolates_user_interpretation_overrides():
    """query_world_projection never merges user interpretation into candidates."""
    claims = (
        _world_claim(key="k-canon", authority="canon_fact"),
        _world_claim(
            key="k-user-read",
            authority="user_interpretation",
            source_kind="human_override",
            source_start=4,
            source_end=20,
        ),
    )
    engine = EpistemicQueryEngine(claims)
    answer = engine.query_world_projection(
        owner_id=1, novel_id=1, version_id=1, cutoff=8
    )
    assert isinstance(answer, WorldProjectionAnswer)
    assert answer.status == KnowledgeResultStatus.ANSWERED
    assert answer.available is True
    # user_interpretation claims live only in overrides, never in items.
    item_authorities = {claim.authority for claim in answer.items}
    override_authorities = {claim.authority for claim in answer.overrides}
    assert Authority.USER_INTERPRETATION not in item_authorities
    assert override_authorities == {Authority.USER_INTERPRETATION}
    assert Authority.CANON_FACT in item_authorities


def test_world_projection_hides_user_override_from_authority_allowlist():
    claims = (
        _world_claim(key="k-canon", authority="canon_fact"),
        _world_claim(
            key="k-user-read",
            authority="user_interpretation",
            source_kind="human_override",
        ),
    )
    engine = EpistemicQueryEngine(claims)
    answer = engine.query_world_projection(
        owner_id=1,
        novel_id=1,
        version_id=1,
        cutoff=8,
        authorities=frozenset({Authority.USER_INTERPRETATION}),
    )
    # An allowlist filter selects labels, it never relabels; the override is
    # still isolated from the candidate items.
    assert answer.status == KnowledgeResultStatus.CANDIDATE_ONLY
    assert answer.items == ()
    assert {claim.authority for claim in answer.overrides} == {
        Authority.USER_INTERPRETATION
    }


async def _world_plan() -> QueryPlan:
    result = parse_query_plan(
        {
            "intent": "analysis",
            "owner_id": 1,
            "novel_id": 1,
            "version_id": 1,
            "question_text": "林安知道什么？",
            "reading_progress": {
                "through_chapter": 8,
                "snapshot_hash": "c" * 64,
                "full_book_authorized": False,
            },
            "chapter_range": {"chapter_start": 1, "chapter_end": 8},
            "dimensions": ["world_projection"],
            "source": "analysis_chat",
        }
    )
    assert isinstance(result, QueryPlan), result
    return result


async def _world_resolver(claims):
    async def resolver(reader_id: str):
        if reader_id != READER_WORLD_PROJECTION:
            return None

        async def reader(context):
            return await world_projection_reader(claims, context=context)

        return reader

    return resolver


async def test_chat_claims_can_never_enter_a_world_projection():
    plan = await _world_plan()
    source = SourceSnapshot(
        owner_id=1,
        novel_id=1,
        version_id=1,
        snapshot_hash="c" * 64,
        chapters=(),
    )
    # A chat-sourced claim that somehow materialized is still excluded.
    chat_claim = EpistemicClaim.model_validate(
        {
            **scenario("chat_contamination")["claims"][0],
            "gate_status": "passed",
        }
    )
    assert chat_claim.source_kind == SourceKind.READER_CHAT
    results = await run_plan_adapters(
        plan,
        source=source,
        resolver=await _world_resolver((chat_claim,)),
    )
    world = next(r for r in results if r.dimension == QueryDimension.WORLD_PROJECTION)
    assert world.status.value == "unavailable"
    assert world.refs == ()


async def test_stale_snapshot_world_projection_fails_closed():
    plan = await _world_plan()
    source = SourceSnapshot(
        owner_id=1,
        novel_id=1,
        version_id=1,
        snapshot_hash="c" * 64,
        chapters=(),
    )
    # A claim whose evidence belongs to a different snapshot lineage.
    claim = _world_claim(key="k-stale", snapshot_hash="0" * 64)
    results = await run_plan_adapters(
        plan,
        source=source,
        resolver=await _world_resolver((claim,)),
    )
    world = next(r for r in results if r.dimension == QueryDimension.WORLD_PROJECTION)
    assert world.status.value == "unavailable"
    assert world.refs == ()
