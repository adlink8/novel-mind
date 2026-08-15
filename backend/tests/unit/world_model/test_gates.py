"""Phase 27-01 world-model gate unit tests (REQ-WM-01, D-01..D-04).

Coverage: evidence-gated causality (co-occurrence is never causality), stale
evidence, wrong-owner scope, spoiler/disclosure cutoff, authority upgrades,
user-interpretation approval, temporal-conflict preservation, immutable
candidate projection with no silent authority promotion.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.world_model.claims import CausalEdgeClaim, EventClaim
from app.services.world_model.contracts import (
    Authority,
    GateStatus,
    build_projection,
    projection_verified,
)
from app.services.world_model.gates import (
    GateReason,
    WorldModelGate,
    build_candidate,
    detect_conflicts,
)

pytestmark = pytest.mark.unit

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "world_model"
        / "events_v1.json"
    ).read_text(encoding="utf-8")
)


def make_gate(scenario_name: str) -> tuple[WorldModelGate, dict]:
    scenario = FIXTURE["scenarios"][scenario_name]
    scope = scenario["scope"]
    gate = WorldModelGate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=scope["version_id"],
        source_snapshot_hash=scope["source_snapshot_hash"],
        disclosure_cutoff=scope["disclosure_cutoff"],
        approvals=frozenset(scope["approvals"]),
    )
    return gate, scenario


def gate_all(gate: WorldModelGate, scenario: dict) -> tuple[list, list]:
    """Validate every event/edge claim in a scenario; return (facts, edges)."""
    facts: list = []
    for raw in scenario["events"]:
        result = gate.validate_event(EventClaim.model_validate(raw))
        if result.fact is not None:
            facts.append(result.fact)
    events_by_key = {fact.event_key: fact for fact in facts}
    edges: list = []
    for raw in scenario["edges"]:
        result = gate.validate_edge(CausalEdgeClaim.model_validate(raw), events_by_key)
        if result.edge is not None:
            edges.append(result.edge)
    return facts, edges


# ---------------------------------------------------------------------------
# Evidence-gated causality (D-04)
# ---------------------------------------------------------------------------


def test_cited_cause_passes_the_evidence_gate():
    gate, scenario = make_gate("valid")
    facts, edges = gate_all(gate, scenario)
    assert len(facts) == 4
    assert len(edges) == 2
    for edge in edges:
        assert edge.gate_status == GateStatus.PASSED
        assert edge.source_refs, "passing edge must carry independent evidence"


def test_co_occurrence_is_not_causality():
    gate, scenario = make_gate("co_occurrence")
    result = gate.validate_edge(
        CausalEdgeClaim.model_validate(scenario["edges"][0]),
        {
            "e-cx-a": gate.validate_event(
                EventClaim.model_validate(scenario["events"][0])
            ).fact,
            "e-cx-b": gate.validate_event(
                EventClaim.model_validate(scenario["events"][1])
            ).fact,
        },
    )
    assert result.edge is None
    codes = {verdict.reason_code for verdict in result.verdicts}
    assert GateReason.CO_OCCURRENCE_ONLY in codes
    assert all(
        "co-occurrence" in verdict.message.lower()
        or "independent evidence" in verdict.message.lower()
        for verdict in result.verdicts
        if verdict.reason_code == GateReason.CO_OCCURRENCE_ONLY
    )


def test_edge_claim_without_evidence_never_materializes():
    gate, scenario = make_gate("co_occurrence")
    facts, edges = gate_all(gate, scenario)
    assert len(facts) == 2
    assert edges == [], "co-occurrence edge must not materialize in the projection"


# ---------------------------------------------------------------------------
# Temporal conflicts are preserved, never overwritten
# ---------------------------------------------------------------------------


def test_temporal_conflict_is_preserved_and_queryable():
    gate, scenario = make_gate("temporal_conflict")
    facts, edges = gate_all(gate, scenario)
    assert len(facts) == 2
    assert len(edges) == 1

    projection = build_candidate(
        owner_id=1, novel_id=1, version_id=3, events=facts, edges=edges
    )
    kinds = {(c.kind.value, c.conflict_key) for c in projection.conflicts}
    assert ("temporal_conflict", "temporal:edge-tc") in kinds
    # Both events and the offending edge survive; nothing was deleted.
    assert {e.event_key for e in projection.events} == {"e-tc-a", "e-tc-b"}
    assert projection.edges[0].edge_key == "edge-tc"


def test_ordered_causal_edge_has_no_conflict():
    gate, scenario = make_gate("valid")
    facts, edges = gate_all(gate, scenario)
    projection = build_candidate(
        owner_id=1, novel_id=1, version_id=1, events=facts, edges=edges
    )
    assert projection.conflicts == ()
    assert projection_verified(projection)


def test_assertion_conflict_preserves_both_versions():
    gate, scenario = make_gate("valid")
    facts, _ = gate_all(gate, scenario)
    fact = facts[0]
    twin = fact.model_copy(update={"description": "另一版本的不同描述"})
    conflicts = detect_conflicts([fact, twin], [])
    assert any(
        conflict.kind.value == "assertion_conflict"
        and conflict.involved_keys == (fact.event_key,)
        for conflict in conflicts
    )


# ---------------------------------------------------------------------------
# Owner / version / stale evidence / spoiler / authority fail-closed
# ---------------------------------------------------------------------------


def test_wrong_owner_is_rejected():
    gate, scenario = make_gate("wrong_owner")
    result = gate.validate_event(EventClaim.model_validate(scenario["events"][0]))
    assert result.fact is None
    codes = {verdict.reason_code for verdict in result.verdicts}
    assert GateReason.WRONG_OWNER in codes


def test_stale_evidence_is_rejected():
    gate, scenario = make_gate("stale_evidence")
    result = gate.validate_event(EventClaim.model_validate(scenario["events"][0]))
    assert result.fact is None
    codes = {verdict.reason_code for verdict in result.verdicts}
    assert GateReason.STALE_EVIDENCE in codes


def test_spoiler_cutoff_is_rejected():
    gate, scenario = make_gate("spoiler_cutoff")
    result = gate.validate_event(EventClaim.model_validate(scenario["events"][0]))
    assert result.fact is None
    codes = {verdict.reason_code for verdict in result.verdicts}
    assert GateReason.SPOILER_CUTOFF in codes


def test_authority_upgrade_to_canon_fact_is_rejected():
    gate, scenario = make_gate("authority_upgrade")
    result = gate.validate_event(EventClaim.model_validate(scenario["events"][0]))
    assert result.fact is None
    codes = {verdict.reason_code for verdict in result.verdicts}
    assert GateReason.AUTHORITY_UPGRADE in codes


def test_user_interpretation_requires_approval():
    gate, scenario = make_gate("user_interpretation_unapproved")
    result = gate.validate_event(EventClaim.model_validate(scenario["events"][0]))
    assert result.fact is None
    codes = {verdict.reason_code for verdict in result.verdicts}
    assert GateReason.MISSING_APPROVAL in codes


# ---------------------------------------------------------------------------
# Authority lineage is preserved; candidates never silently promote
# ---------------------------------------------------------------------------


def test_authority_label_survives_gate_unchanged():
    gate, scenario = make_gate("valid")
    facts, edges = gate_all(gate, scenario)
    by_key = {fact.event_key: fact for fact in facts}
    # The valid scenario contains one approved user_interpretation.
    assert by_key["e-treaty-reading"].authority == Authority.USER_INTERPRETATION
    assert by_key["e-arrival"].authority == Authority.PROBABLE_INFERENCE
    # No probable_inference was silently upgraded to canon_fact.
    assert all(event.authority != Authority.CANON_FACT for event in facts)
    assert all(edge.authority != Authority.CANON_FACT for edge in edges)


def test_approved_canon_fact_is_allowed_only_with_approval():
    gate, scenario = make_gate("valid")
    claim = EventClaim.model_validate(scenario["events"][0]).model_copy(
        update={"authority": Authority.CANON_FACT}
    )
    # Without approval in the gate the identical claim is rejected.
    unapproved_gate = WorldModelGate(
        owner_id=1,
        novel_id=1,
        version_id=1,
        source_snapshot_hash=scenario["scope"]["source_snapshot_hash"],
        disclosure_cutoff=3,
        approvals=frozenset(),
    )
    rejected = unapproved_gate.validate_event(claim)
    assert rejected.fact is None
    assert {v.reason_code for v in rejected.verdicts} == {GateReason.AUTHORITY_UPGRADE}
    # With explicit approval the same claim passes without relabeling.
    approved_gate = WorldModelGate(
        owner_id=1,
        novel_id=1,
        version_id=1,
        source_snapshot_hash=scenario["scope"]["source_snapshot_hash"],
        disclosure_cutoff=3,
        approvals=frozenset({Authority.CANON_FACT}),
    )
    accepted = approved_gate.validate_event(claim)
    assert accepted.fact is not None
    assert accepted.fact.authority == Authority.CANON_FACT


# ---------------------------------------------------------------------------
# Contract / projection invariants
# ---------------------------------------------------------------------------


def test_projection_is_immutable_and_hash_sealed():
    gate, scenario = make_gate("valid")
    facts, edges = gate_all(gate, scenario)
    projection = build_candidate(
        owner_id=1, novel_id=1, version_id=1, events=facts, edges=edges
    )
    assert projection_verified(projection)
    assert projection.schema_version == "world-model-event.v1"
    # No active-pointer / promotion / cutover fields exist.
    fields = set(projection.model_dump().keys())
    for forbidden in ("active_pointer", "promotion", "current_revision", "cutover"):
        assert forbidden not in fields


def test_projection_rejects_cross_scope_rows():
    gate, scenario = make_gate("valid")
    facts, _ = gate_all(gate, scenario)
    hijack = facts[0].model_copy(update={"owner_id": 2})
    with pytest.raises(ValueError):
        build_projection(
            owner_id=1,
            novel_id=1,
            version_id=1,
            events=[hijack],
            edges=[],
            conflicts=[],
        )


def test_projection_rejects_orphan_edge_endpoints():
    gate, scenario = make_gate("valid")
    facts, edges = gate_all(gate, scenario)
    with pytest.raises(ValueError):
        build_projection(
            owner_id=1,
            novel_id=1,
            version_id=1,
            events=facts,
            edges=[
                edge.model_copy(update={"target_event_key": "e-unknown"})
                for edge in edges
            ],
            conflicts=[],
        )


def test_claims_reject_untyped_shape_violations():
    gate, scenario = make_gate("co_occurrence")
    raw = dict(scenario["edges"][0])
    # An edge claim must be an object, not a bare list/tuple.
    with pytest.raises(ValidationError):
        CausalEdgeClaim.model_validate({**raw, "source_refs": "not-a-list"})
    with pytest.raises(ValidationError):
        EventClaim.model_validate({**scenario["events"][0], "confidence": 2.0})
    with pytest.raises(ValidationError):
        EventClaim.model_validate(
            {**scenario["events"][0], "effective": {"start": 5, "end": 2}}
        )
