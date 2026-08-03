"""Adversarial fail-closed gates for the shared world-model authority boundary.

Attacks covered (REQ-WM-01 / D-01..D-06):
- co-occurrence can never auto-upgrade into causality or canon_fact;
- temporal conflicts are preserved, never silently dropped;
- wrong-owner claims and reads fail closed;
- stale evidence (frozen-snapshot drift) is rejected;
- spoiler/disclosure cutoff hides future facts, in gate and in query;
- attempted authority upgrade (inference → canon_fact) is rejected in-memory
  and in the durable row (checksum drift fails closed);
- no active-pointer / promotion path exists anywhere.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.novel import Novel
from app.models.user import User
from app.models.world_model_event import WorldModelEvent
from app.services.world_model.claims import CausalEdgeClaim, EventClaim
from app.services.world_model.contracts import (
    Authority,
    CausalEdge,
    EventFact,
    WorldModelCandidateProjection,
    event_checksum,
    projection_checksum,
)
from app.services.world_model.entities import (
    AliasCollisionKind,
    AliasReviewStatus,
    EntityCandidateProjection,
    EntityClaim,
    EntityGate,
    EntityLinkClaim,
    LinkKind,
    WorldEntity,
    WorldEntityQueryEngine,
    build_entity_candidate,
    build_entity_projection,
    entity_checksum,
    entity_projection_checksum,
    visible_at_cutoff,
)
from app.services.world_model.event_queries import WorldModelEventQueries
from app.services.world_model.event_repository import (
    WorldModelEventRepository,
    WorldModelRepositoryError,
)
from app.services.world_model.gates import (
    GateReason,
    WorldModelGate,
    build_candidate,
)
from app.services.world_model.provenance import (
    EntityProvenanceReason,
    entity_provenance_reasons,
    validate_entity_package,
)
from app.services.world_model.rules import (
    GateReason as RuleGateReason,
    RuleClaim,
    RuleExceptionClaim,
    RuleGate,
    SourceKind,
)

pytestmark = [pytest.mark.unit]

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "world_model" / "events_v1.json")
    .read_text(encoding="utf-8")
)


def scenario(name: str) -> dict:
    return FIXTURE["scenarios"][name]


def make_gate(name: str) -> WorldModelGate:
    scope = scenario(name)["scope"]
    return WorldModelGate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=scope["version_id"],
        source_snapshot_hash=scope["source_snapshot_hash"],
        disclosure_cutoff=scope["disclosure_cutoff"],
        approvals=frozenset(scope["approvals"]),
    )


def build_valid_facts(
    gate: WorldModelGate, *, version_id: int = 1
) -> dict[str, EventFact]:
    facts: dict[str, EventFact] = {}
    for raw in scenario("valid")["events"]:
        result = gate.validate_event(
            EventClaim.model_validate({**raw, "version_id": version_id})
        )
        assert result.fact is not None, result.verdicts
        facts[result.fact.event_key] = result.fact
    return facts


async def make_engine_and_factory(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'world_adversarial.db'}"
    engine = create_async_engine(url)
    search_vector = Base.metadata.tables["text_chunks"].c.search_vector
    postgres_computed = search_vector.computed
    search_vector.computed = None
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    finally:
        search_vector.computed = postgres_computed
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


async def seed_scope(session: AsyncSession) -> None:
    session.add(
        User(id=1, username="author", email="author@example.com", hashed_password="x")
    )
    session.add(
        User(id=2, username="other", email="other@example.com", hashed_password="x")
    )
    session.add(Novel(id=1, owner_id=1, title="测试小说"))
    await session.flush()


# ---------------------------------------------------------------------------
# Co-occurrence is never causality and never canon (D-04)
# ---------------------------------------------------------------------------


def test_co_occurrence_projection_materializes_no_edge():
    gate = make_gate("co_occurrence")
    facts: list[EventFact] = []
    for raw in scenario("co_occurrence")["events"]:
        result = gate.validate_event(EventClaim.model_validate(raw))
        assert result.fact is not None
        facts.append(result.fact)
    edges: list[CausalEdge] = []
    for raw in scenario("co_occurrence")["edges"]:
        result = gate.validate_edge(
            CausalEdgeClaim.model_validate(raw),
            {fact.event_key: fact for fact in facts},
        )
        assert result.edge is None
        assert {v.reason_code for v in result.verdicts} == {
            GateReason.CO_OCCURRENCE_ONLY
        }
    projection = build_candidate(
        owner_id=1, novel_id=1, version_id=2, events=facts, edges=edges
    )
    assert projection.edges == ()
    assert projection.conflicts == ()
    # Even co-occurring facts stay probable_inference, never canon_fact.
    assert all(e.authority == Authority.PROBABLE_INFERENCE for e in projection.events)


# ---------------------------------------------------------------------------
# Temporal conflicts preserved, never dropped (D-04)
# ---------------------------------------------------------------------------


def test_temporal_conflict_cannot_be_silently_resolved():
    gate = make_gate("temporal_conflict")
    facts: list[EventFact] = []
    for raw in scenario("temporal_conflict")["events"]:
        result = gate.validate_event(EventClaim.model_validate(raw))
        assert result.fact is not None
        facts.append(result.fact)
    edges: list[CausalEdge] = []
    for raw in scenario("temporal_conflict")["edges"]:
        result = gate.validate_edge(
            CausalEdgeClaim.model_validate(raw),
            {fact.event_key: fact for fact in facts},
        )
        assert result.edge is not None  # edge is kept, but flagged
        edges.append(result.edge)
    projection = build_candidate(
        owner_id=1, novel_id=1, version_id=3, events=facts, edges=edges
    )
    assert len(projection.conflicts) == 1
    assert projection.conflicts[0].kind.value == "temporal_conflict"
    assert projection.edges[0].edge_key == "edge-tc"
    assert {e.event_key for e in projection.events} == {"e-tc-a", "e-tc-b"}


# ---------------------------------------------------------------------------
# Wrong-owner claims and reads fail closed (V2/V3)
# ---------------------------------------------------------------------------


def test_wrong_owner_claim_is_rejected_and_not_relabeled():
    gate = make_gate("wrong_owner")
    result = gate.validate_event(EventClaim.model_validate(scenario("wrong_owner")["events"][0]))
    assert result.fact is None
    assert {v.reason_code for v in result.verdicts} == {GateReason.WRONG_OWNER}


@pytest.mark.integration
async def test_wrong_owner_replay_fails_closed(tmp_path):
    gate = make_gate("valid")
    facts = build_valid_facts(gate)
    edges = []
    for raw in scenario("valid")["edges"]:
        result = gate.validate_edge(CausalEdgeClaim.model_validate(raw), facts)
        assert result.edge is not None
        edges.append(result.edge)
    projection = build_candidate(
        owner_id=1, novel_id=1, version_id=1, events=list(facts.values()), edges=edges
    )

    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        await WorldModelEventRepository(session).append_projection(projection)
        with pytest.raises(WorldModelRepositoryError):
            await WorldModelEventRepository(session).replay_projection(
                owner_id=2, novel_id=1, version_id=1
            )
        # Cross-owner cutoff query also fails closed.
        assert (
            await WorldModelEventQueries(session).query_cutoff_projection(
                owner_id=2, novel_id=1, version_id=1, cutoff=3
            )
        ) is None
    await engine.dispose()


# ---------------------------------------------------------------------------
# Stale evidence (frozen-snapshot drift) fails closed
# ---------------------------------------------------------------------------


def test_stale_evidence_rejected_before_any_write():
    gate = make_gate("stale_evidence")
    claim = EventClaim.model_validate(scenario("stale_evidence")["events"][0])
    assert claim.source_refs[0].source_snapshot_hash != gate.source_snapshot_hash
    result = gate.validate_event(claim)
    assert result.fact is None
    assert {v.reason_code for v in result.verdicts} == {GateReason.STALE_EVIDENCE}


def test_stale_snapshot_in_otherwise_valid_claim_is_rejected():
    gate = make_gate("valid")
    raw = json.loads(json.dumps(scenario("valid")["events"][0]))  # deep copy
    raw["source_refs"][0]["source_snapshot_hash"] = "0" * 64
    result = gate.validate_event(EventClaim.model_validate(raw))
    assert result.fact is None
    assert {v.reason_code for v in result.verdicts} == {GateReason.STALE_EVIDENCE}


# ---------------------------------------------------------------------------
# Spoiler cutoff (D-05): future facts are never visible
# ---------------------------------------------------------------------------


def test_spoiler_claim_rejected_at_the_gate():
    gate = make_gate("spoiler_cutoff")
    result = gate.validate_event(
        EventClaim.model_validate(scenario("spoiler_cutoff")["events"][0])
    )
    assert result.fact is None
    assert {v.reason_code for v in result.verdicts} == {GateReason.SPOILER_CUTOFF}


@pytest.mark.integration
async def test_future_fact_stored_wide_but_never_readable_narrow(tmp_path):
    # A projection written with a full-book cutoff still obeys a narrow reader cutoff.
    gate = make_gate("valid")
    facts = build_valid_facts(gate)
    edges = []
    for raw in scenario("valid")["edges"]:
        result = gate.validate_edge(CausalEdgeClaim.model_validate(raw), facts)
        assert result.edge is not None
        edges.append(result.edge)
    projection = build_candidate(
        owner_id=1, novel_id=1, version_id=1, events=list(facts.values()), edges=edges
    )

    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        await WorldModelEventRepository(session).append_projection(projection)
        queries = WorldModelEventQueries(session)
        # Author with full-book access sees everything.
        full = await queries.query_cutoff_projection(
            owner_id=1, novel_id=1, version_id=1, cutoff=3
        )
        assert full is not None and len(full.events) == 4
        # Reader at cutoff 1 only ever sees chapter-1 facts; no future leak.
        narrow = await queries.query_cutoff_projection(
            owner_id=1, novel_id=1, version_id=1, cutoff=1
        )
        assert narrow is not None
        assert {e.event_key for e in narrow.events} == {"e-arrival"}
        assert "e-revolt" not in {e.event_key for e in narrow.events}
        # The chapter-3 causal edge cannot surface through a cutoff-1 query.
        assert all(e.disclosure_cutoff <= 1 for e in narrow.edges)
    await engine.dispose()


# ---------------------------------------------------------------------------
# Attempted authority upgrade is rejected, in-memory and durable (D-01)
# ---------------------------------------------------------------------------


def test_attempted_authority_upgrade_to_canon_fact_rejected():
    gate = make_gate("authority_upgrade")
    claim = EventClaim.model_validate(scenario("authority_upgrade")["events"][0])
    assert claim.authority == Authority.CANON_FACT
    result = gate.validate_event(claim)
    assert result.fact is None
    assert {v.reason_code for v in result.verdicts} == {GateReason.AUTHORITY_UPGRADE}


def test_no_silent_relabel_between_authorities():
    gate = make_gate("valid")
    facts = build_valid_facts(gate)
    by_key = {event.event_key: event for event in facts.values()}
    # literary_interpretation stays literary_interpretation; nothing becomes canon.
    assert by_key["e-arrival"].authority == Authority.PROBABLE_INFERENCE
    assert by_key["e-treaty-reading"].authority == Authority.USER_INTERPRETATION
    for event in facts.values():
        if event.authority != Authority.CANON_FACT:
            # A relabeled copy produces a different sealed checksum — the durable
            # row can never silently flip to canon_fact.
            mutated = event.model_copy(update={"authority": Authority.CANON_FACT})
            assert event_checksum(mutated) != event_checksum(event)


@pytest.mark.integration
async def test_durable_row_rejects_silent_authority_upgrade(tmp_path):
    gate = make_gate("valid")
    facts = build_valid_facts(gate)
    edges = []
    for raw in scenario("valid")["edges"]:
        result = gate.validate_edge(CausalEdgeClaim.model_validate(raw), facts)
        assert result.edge is not None
        edges.append(result.edge)
    projection = build_candidate(
        owner_id=1, novel_id=1, version_id=1, events=list(facts.values()), edges=edges
    )

    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        await WorldModelEventRepository(session).append_projection(projection)
        # Replay returns the exact original authorities — no implicit upgrade.
        replayed = await WorldModelEventRepository(session).replay_projection(
            owner_id=1, novel_id=1, version_id=1
        )
        for event in replayed.events:
            if event.event_key in facts:
                assert event.authority == facts[event.event_key].authority

        # Tampering the stored payload's authority breaks the sealed checksum.
        row = (
            await session.scalars(
                select(WorldModelEvent).where(
                    WorldModelEvent.event_key == "e-arrival"
                )
            )
        ).first()
        payload = dict(row.canonical_payload)
        payload["authority"] = Authority.CANON_FACT.value
        row.canonical_payload = payload
        row.canonical_payload_hash = "0" * 64
        await session.flush()
        with pytest.raises(WorldModelRepositoryError):
            await WorldModelEventRepository(session).replay_projection(
                owner_id=1, novel_id=1, version_id=1
            )
    await engine.dispose()


# ---------------------------------------------------------------------------
# Candidate-only; no active-pointer / promotion machinery (D-02)
# ---------------------------------------------------------------------------


def test_candidate_projection_is_the_only_output_shape():
    gate = make_gate("valid")
    facts = build_valid_facts(gate)
    edges = []
    for raw in scenario("valid")["edges"]:
        result = gate.validate_edge(CausalEdgeClaim.model_validate(raw), facts)
        assert result.edge is not None
        edges.append(result.edge)
    projection = build_candidate(
        owner_id=1, novel_id=1, version_id=1, events=list(facts.values()), edges=edges
    )
    assert isinstance(projection, WorldModelCandidateProjection)
    assert projection_checksum(projection) == projection.projection_hash
    fields = set(projection.model_dump().keys())
    for forbidden in ("active_pointer", "promotion", "current_revision", "cutover"):
        assert forbidden not in fields


def test_repository_has_no_promotion_and_queries_are_read_only():
    from app.services.world_model.event_queries import WorldModelEventQueries
    from app.services.world_model.event_repository import WorldModelEventRepository

    repo_members = {
        name
        for name, _ in __import__("inspect").getmembers(
            WorldModelEventRepository, predicate=callable
        )
    }
    query_members = {
        name
        for name, _ in __import__("inspect").getmembers(
            WorldModelEventQueries, predicate=callable
        )
    }
    assert not {m for m in repo_members if m.startswith(("promote", "update", "delete"))}
    assert not {m for m in query_members if m.startswith(("append", "write", "update"))}
    assert "append_projection" in repo_members
    assert "query_cutoff_projection" in query_members


# ===========================================================================
# Phase 27-03 entity/rule adversarial coverage (REQ-WM-03)
# ===========================================================================
# Attacks covered:
# - alias poisoning can never silently merge entities (review candidates only);
# - rule exceptions are first-class and never dropped by normalization;
# - membership/ownership/spatial/item-state links cannot be cross-owner written;
# - spoiler entities/rules are rejected at the gate and hidden by cutoff query;
# - Reader Chat / user conversation never serializes as entity/rule canon;
# - evidence/approval gate bypasses fail closed (checksum-visible mutations).
# ===========================================================================


ENTITY_FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "world_model"
        / "entities_v1.json"
    ).read_text(encoding="utf-8")
)


def entity_scenario(name: str) -> dict:
    return ENTITY_FIXTURE["scenarios"][name]


def make_entity_gate(name: str) -> EntityGate:
    scope = entity_scenario(name)["scope"]
    return EntityGate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=scope["version_id"],
        source_snapshot_hash=scope["source_snapshot_hash"],
        disclosure_cutoff=scope["disclosure_cutoff"],
        approvals=frozenset(scope["approvals"]),
    )


def make_rule_gate(name: str) -> RuleGate:
    scope = entity_scenario(name)["scope"]
    return RuleGate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=scope["version_id"],
        source_snapshot_hash=scope["source_snapshot_hash"],
        disclosure_cutoff=scope["disclosure_cutoff"],
        approvals=frozenset(scope["approvals"]),
    )


def build_entity_candidate_for(name: str) -> EntityCandidateProjection:
    """Run a fixture scenario's entities/links/rules/exceptions through gates."""
    sc = entity_scenario(name)
    scope = sc["scope"]
    egate = make_entity_gate(name)
    entities = []
    for raw in sc["entities"]:
        result = egate.validate_entity(EntityClaim.model_validate(raw))
        assert result.entity is not None, result.verdicts
        entities.append(result.entity)
    links = []
    for raw in sc["links"]:
        result = egate.validate_link(EntityLinkClaim.model_validate(raw))
        assert result.link is not None, result.verdicts
        links.append(result.link)
    rgate = make_rule_gate(name)
    rules = []
    for raw in sc["rules"]:
        result = rgate.validate_rule(RuleClaim.model_validate(raw))
        assert result.rule is not None, result.verdicts
        rules.append(result.rule)
    rule_keys = {rule.rule_key for rule in rules}
    exceptions = []
    for raw in sc["exceptions"]:
        result = rgate.validate_exception(
            RuleExceptionClaim.model_validate(raw), rule_keys
        )
        assert result.exception is not None, result.verdicts
        exceptions.append(result.exception)
    return build_entity_candidate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=scope["version_id"],
        entities=entities,
        links=links,
        rules=rules,
        exceptions=exceptions,
    )


# ---------------------------------------------------------------------------
# Alias poisoning: review candidate only, never a silent merge (REQ-WM-03)
# ---------------------------------------------------------------------------


def test_alias_poisoning_never_merges_entities():
    """A second entity claiming another entity's name/alias stays distinct."""
    projection = build_entity_candidate_for("alias_collision")
    engine = WorldEntityQueryEngine(projection)
    keys = {entity.entity_key for entity in engine.query_entities()}
    assert keys == {
        "e-faction-nan",
        "e-faction-nanjiang",
        "e-place-lin-an",
        "e-place-lin-anfu",
    }
    reviews = engine.query_alias_reviews()
    assert reviews
    assert all(review.status == AliasReviewStatus.REVIEW for review in reviews)
    for review in reviews:
        assert review.entity_key_a != review.entity_key_b


def test_alias_reviews_are_candidate_only_and_never_auto_resolved():
    projection = build_entity_candidate_for("alias_collision")
    for review in projection.alias_reviews:
        # The review is a candidate for a human decision; there is no path that
        # flips it to RESOLVED or merges the two entities automatically.
        assert review.status == AliasReviewStatus.REVIEW
        assert review.kind in (
            AliasCollisionKind.EXACT_ALIAS,
            AliasCollisionKind.NAME_SIMILARITY,
            AliasCollisionKind.ALIAS_SIMILARITY,
        )


# ---------------------------------------------------------------------------
# Rule exceptions are first-class and never dropped by normalization
# ---------------------------------------------------------------------------


def test_rule_exception_is_never_dropped_by_normalization():
    projection = build_entity_candidate_for("rule_exception")
    exceptions = WorldEntityQueryEngine(projection).query_exceptions(
        rule_key="rule-magic"
    )
    assert [exc.exception_key for exc in exceptions] == ["exc-magic-moon"]
    # Dropping the exception silently would change the sealed projection hash —
    # the durable row can never lose an exception without being detected.
    without_exception = build_entity_projection(
        owner_id=1,
        novel_id=1,
        version_id=3,
        entities=list(projection.entities),
        rules=list(projection.rules),
        exceptions=(),
    )
    assert (
        entity_projection_checksum(without_exception)
        != entity_projection_checksum(projection)
    )


def test_exception_bound_to_missing_rule_is_rejected():
    projection = build_entity_candidate_for("rule_exception")
    from app.services.world_model.rules import RuleException

    orphan = RuleException.model_validate(
        projection.exceptions[0]
        .model_copy(update={"rule_key": "rule-does-not-exist"})
        .model_dump(mode="json")
    )
    result = validate_entity_package(
        entities=list(projection.entities),
        rules=list(projection.rules),
        exceptions=[orphan],
    )
    assert not result.ok
    assert EntityProvenanceReason.ORPHAN_EXCEPTION_RULE in entity_provenance_reasons(
        result
    )


# ---------------------------------------------------------------------------
# Ownership / spatial / item state links (REQ-WM-03)
# ---------------------------------------------------------------------------


def test_ownership_and_spatial_state_queryable_and_scoped():
    projection = build_entity_candidate_for("ownership")
    engine = WorldEntityQueryEngine(projection)
    owns = engine.query_links(link_kind=LinkKind.OWNS)
    assert owns and owns[0].source_key == "e-char-lin-an"
    assert owns[0].target_key == "e-item-sword"
    located = engine.query_links(link_kind=LinkKind.LOCATED_IN)
    assert located and located[0].target_key == "e-place-camp"
    carried = engine.query_links(link_kind=LinkKind.CARRIED_BY)
    assert carried and carried[0].source_key == "e-item-sword"


def test_cross_owner_link_claim_fails_closed():
    gate = make_entity_gate("ownership")
    raw = json.loads(json.dumps(entity_scenario("ownership")["links"][0]))
    raw["owner_id"] = 2
    result = gate.validate_link(EntityLinkClaim.model_validate(raw))
    assert result.link is None
    assert {v.reason_code for v in result.verdicts} == {GateReason.WRONG_OWNER}


# ---------------------------------------------------------------------------
# Spoiler entities/rules (D-05)
# ---------------------------------------------------------------------------


def test_spoiler_entity_rejected_and_hidden_from_cutoff_query():
    gate = make_entity_gate("spoiler_cutoff")
    result = gate.validate_entity(
        EntityClaim.model_validate(entity_scenario("spoiler_cutoff")["entities"][0])
    )
    assert result.entity is None
    assert GateReason.SPOILER_CUTOFF in result.reason_codes

    # A wide-cutoff projection still hides the future fact from a narrow reader.
    projection = build_entity_candidate_for("valid")
    engine = WorldEntityQueryEngine(projection)
    at_2 = engine.query_entities(cutoff=2)
    assert "e-item-seal" not in {entity.entity_key for entity in at_2}
    assert all(visible_at_cutoff(entity.disclosure_cutoff, 2) for entity in at_2)


# ---------------------------------------------------------------------------
# Chat contamination can never write entity/rule canon (D-06)
# ---------------------------------------------------------------------------


def test_chat_entity_claim_rejected_even_with_approvals():
    gate = make_entity_gate("chat_contamination")
    assert Authority.CANON_FACT in gate.approvals
    raw = json.loads(json.dumps(entity_scenario("chat_contamination")["entities"][0]))
    result = gate.validate_entity(EntityClaim.model_validate(raw))
    assert result.entity is None
    assert RuleGateReason.CHAT_NOT_FACT_SOURCE in result.reason_codes


def test_chat_rule_escalation_rejected():
    gate = make_rule_gate("chat_contamination")
    raw = json.loads(json.dumps(entity_scenario("chat_contamination")["rules"][0]))
    # Already canon_fact with canon_fact approved; chat source still fails.
    result = gate.validate_rule(RuleClaim.model_validate(raw))
    assert result.rule is None
    assert RuleGateReason.CHAT_NOT_FACT_SOURCE in result.reason_codes
    assert RuleGateReason.AUTHORITY_UPGRADE not in result.reason_codes


def test_chat_source_kind_relabel_is_checksum_visible():
    raw = json.loads(json.dumps(entity_scenario("chat_contamination")["entities"][0]))
    claim = EntityClaim.model_validate(raw)
    assert claim.source_kind == SourceKind.READER_CHAT
    relabeled = claim.model_copy(update={"source_kind": SourceKind.CANON_SOURCE})
    # Relabeling changes the sealed checksum — the durable row can never
    # silently flip a chat-derived entity into canon_source.
    assert entity_checksum(
        WorldEntity.model_validate(
            {
                **relabeled.model_dump(mode="json"),
                "lineage": [relabeled.entity_key],
                "gate_status": "passed",
            }
        )
    ) != entity_checksum(
        WorldEntity.model_validate(
            {
                **claim.model_dump(mode="json"),
                "lineage": [claim.entity_key],
                "gate_status": "passed",
            }
        )
    )


# ---------------------------------------------------------------------------
# Evidence / approval gate bypass (D-01 / D-04)
# ---------------------------------------------------------------------------


def test_entity_authority_upgrade_rejected_and_checksum_visible():
    gate = make_entity_gate("authority_upgrade")
    claim = EntityClaim.model_validate(
        entity_scenario("authority_upgrade")["entities"][0]
    )
    assert claim.authority == Authority.CANON_FACT
    result = gate.validate_entity(claim)
    assert result.entity is None
    assert RuleGateReason.AUTHORITY_UPGRADE in result.reason_codes

    # A probable-inference entity relabeled as canon_fact changes its sealed
    # checksum, so the durable row can never silently upgrade (D-01).
    base = WorldEntity.model_validate(
        {
            **entity_scenario("valid")["entities"][0],
            "lineage": ["e-place-lin-an"],
            "gate_status": "passed",
        }
    )
    assert base.authority == Authority.PROBABLE_INFERENCE
    upgraded = base.model_copy(update={"authority": Authority.CANON_FACT})
    assert entity_checksum(upgraded) != entity_checksum(base)


def test_uncertain_merges_stay_candidate_review_not_canon():
    """Every uncertain alias collision is review/candidate — nothing becomes a
    canon fact, nothing is auto-resolved."""
    projection = build_entity_candidate_for("alias_collision")
    for review in projection.alias_reviews:
        assert review.status == AliasReviewStatus.REVIEW
        # Reviews carry no authority field at all: they are never facts.
        assert "authority" not in review.model_dump()
    # No entity carries the other's primary_name after review generation.
    names = {entity.primary_name for entity in projection.entities}
    assert "南境军" in names and "南疆军" in names
