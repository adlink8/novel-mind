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
