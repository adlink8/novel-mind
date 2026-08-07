"""Append-only epistemic knowledge repository + cutoff/POV query API (REQ-WM-02)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novel import Novel
from app.models.user import User
from app.models.world_model_knowledge import WorldModelKnowledge
from app.services.world_model.contracts import Authority, GateStatus
from app.services.world_model.knowledge import (
    EpistemicAspect,
    EpistemicClaim,
    EpistemicGate,
    EpistemicStatus,
    KnowledgeCandidateProjection,
    build_knowledge_projection,
    claim_checksum,
    projection_checksum,
)
from app.services.world_model.knowledge_queries import (
    KnowledgeQueries,
    KnowledgeQueryError,
)
from app.services.world_model.knowledge_repository import (
    KnowledgeRepository,
    KnowledgeRepositoryError,
)

pytestmark = pytest.mark.unit

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "world_model"
        / "epistemic_v1.json"
    ).read_text(encoding="utf-8")
)


def scenario(name: str) -> dict:
    return FIXTURE["scenarios"][name]


def gated_projection(
    name: str = "valid", *, version_id: int = 1
) -> KnowledgeCandidateProjection:
    scope = scenario(name)["scope"]
    gate = EpistemicGate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=version_id,
        source_snapshot_hash=scope["source_snapshot_hash"],
        disclosure_cutoff=scope["disclosure_cutoff"],
        approvals=frozenset(scope["approvals"]),
    )
    claims = []
    for raw in scenario(name)["claims"]:
        result = gate.validate_claim(
            EpistemicClaim.model_validate({**raw, "version_id": version_id})
        )
        if result.claim is not None:
            claims.append(result.claim)
    assert claims, "scenario must gate at least one claim"
    return build_knowledge_projection(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=version_id,
        claims=claims,
    )


async def _seed_owner_novel(db_session: AsyncSession):
    owner = User(username="wm-know", email="wm-know@example.com", hashed_password="x")
    db_session.add(owner)
    await db_session.flush()
    novel = Novel(owner_id=owner.id, title="认知史书", status="ready")
    db_session.add(novel)
    await db_session.flush()
    await db_session.commit()
    return owner, novel


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_append_and_replay_knowledge_roundtrip(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    projection = gated_projection(version_id=1)
    repo = KnowledgeRepository(db_session)
    await repo.append_projection(projection)
    await db_session.commit()

    replayed = await repo.replay_projection(
        owner_id=projection.owner_id, novel_id=projection.novel_id, version_id=1
    )
    assert replayed.projection_hash == projection.projection_hash
    assert [c.knowledge_key for c in replayed.claims] == [
        c.knowledge_key for c in projection.claims
    ]
    assert replayed.schema_version == projection.schema_version


@pytest.mark.asyncio
async def test_append_projection_unsealed_hash_fails(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    projection = gated_projection(version_id=1)
    tampered = projection.model_copy(update={"projection_hash": "1" * 64})
    with pytest.raises(KnowledgeRepositoryError, match="not sealed"):
        await KnowledgeRepository(db_session).append_projection(tampered)


@pytest.mark.asyncio
async def test_append_projection_idempotent_on_conflict(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    repo = KnowledgeRepository(db_session)
    projection = gated_projection(version_id=1)
    await repo.append_projection(projection)
    await db_session.commit()
    await repo.append_projection(projection)
    await db_session.commit()
    rows = list((await db_session.scalars(select(WorldModelKnowledge))).all())
    assert len(rows) == len(projection.claims)


@pytest.mark.asyncio
async def test_replay_missing_projection_raises(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    with pytest.raises(KnowledgeRepositoryError, match="not found"):
        await KnowledgeRepository(db_session).replay_projection(
            owner_id=owner.id, novel_id=novel.id, version_id=999
        )


@pytest.mark.asyncio
async def test_append_stale_version_rejected(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    repo = KnowledgeRepository(db_session)
    await repo.append_projection(gated_projection(version_id=5))
    await db_session.commit()
    with pytest.raises(KnowledgeRepositoryError, match="stale-version"):
        await repo.append_projection(gated_projection(version_id=4))


@pytest.mark.asyncio
async def test_replay_claim_checksum_drift_fails_closed(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    repo = KnowledgeRepository(db_session)
    projection = gated_projection(version_id=1)
    await repo.append_projection(projection)
    await db_session.commit()

    row = (await db_session.scalars(select(WorldModelKnowledge))).first()
    row.canonical_payload_hash = "0" * 64
    await db_session.commit()
    with pytest.raises(KnowledgeRepositoryError, match="checksum drift"):
        await repo.replay_projection(
            owner_id=projection.owner_id, novel_id=projection.novel_id, version_id=1
        )


@pytest.mark.asyncio
async def test_replay_rejects_non_gate_passed_claim(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    repo = KnowledgeRepository(db_session)
    projection = gated_projection(version_id=1)
    await repo.append_projection(projection)
    await db_session.commit()

    row = (await db_session.scalars(select(WorldModelKnowledge))).first()
    payload = dict(row.canonical_payload)
    payload["gate_status"] = GateStatus.REJECTED.value
    row.canonical_payload = payload
    row.canonical_payload_hash = claim_checksum(
        EpistemicClaim.model_validate(payload)
    )
    await db_session.commit()
    with pytest.raises(KnowledgeRepositoryError, match="not a gate-passed"):
        await repo.replay_projection(
            owner_id=projection.owner_id, novel_id=projection.novel_id, version_id=1
        )


@pytest.mark.asyncio
async def test_list_versions_ascending(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    repo = KnowledgeRepository(db_session)
    await repo.append_projection(gated_projection(version_id=2))
    await db_session.commit()
    await repo.append_projection(gated_projection(version_id=3))
    await db_session.commit()
    assert await repo.list_versions(owner_id=1, novel_id=1) == [2, 3]


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_character_knowledge_cutoff_pov(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    repo = KnowledgeRepository(db_session)
    projection = gated_projection(version_id=1)
    await repo.append_projection(projection)
    await db_session.commit()

    queries = KnowledgeQueries(db_session)
    answer = await queries.query_character_knowledge(
        owner_id=1, novel_id=1, version_id=1, subject="lin-an", cutoff=8
    )
    assert answer.subject == "lin-an"
    assert answer.claims
    # scope/POV filter: another subject with no rows abstains
    other = await queries.query_character_knowledge(
        owner_id=1, novel_id=1, version_id=1, subject="ghost", cutoff=8
    )
    assert other.claims == ()


@pytest.mark.asyncio
async def test_query_character_knowledge_aspect_and_authorities_filters(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    repo = KnowledgeRepository(db_session)
    projection = gated_projection(version_id=1)
    await repo.append_projection(projection)
    await db_session.commit()

    queries = KnowledgeQueries(db_session)
    answer = await queries.query_character_knowledge(
        owner_id=1,
        novel_id=1,
        version_id=1,
        subject="lin-an",
        cutoff=8,
        aspect=EpistemicAspect.STATE,
        authorities=frozenset({Authority.CANON_FACT, Authority.PROBABLE_INFERENCE}),
    )
    assert all(c.aspect == EpistemicAspect.STATE for c in answer.claims)
    assert all(
        c.authority in {Authority.CANON_FACT, Authority.PROBABLE_INFERENCE}
        for c in answer.claims
    )


@pytest.mark.asyncio
async def test_query_character_history_author_view(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    repo = KnowledgeRepository(db_session)
    projection = gated_projection("mistaken_belief", version_id=1)
    await repo.append_projection(projection)
    await db_session.commit()

    queries = KnowledgeQueries(db_session)
    history = await queries.query_character_history(
        owner_id=1, novel_id=1, version_id=1, subject="lin-an"
    )
    assert history
    by_status = await queries.query_by_status(
        owner_id=1,
        novel_id=1,
        version_id=1,
        status=EpistemicStatus.MISTAKEN_BELIEF,
    )
    assert all(
        c.epistemic_status == EpistemicStatus.MISTAKEN_BELIEF for c in by_status
    )


@pytest.mark.asyncio
async def test_query_lineage_and_list_versions(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    repo = KnowledgeRepository(db_session)
    projection = gated_projection(version_id=1)
    key = projection.claims[0].knowledge_key
    await repo.append_projection(projection)
    await db_session.commit()
    await repo.append_projection(gated_projection(version_id=2))
    await db_session.commit()

    queries = KnowledgeQueries(db_session)
    lineage = await queries.query_lineage(owner_id=1, novel_id=1, knowledge_key=key)
    # lineage matching includes descendant rows whose lineage references the key
    assert len(lineage) >= 2
    versions = {c.version_id for c in lineage}
    assert versions == {1, 2}
    assert await queries.list_versions(owner_id=1, novel_id=1) == [1, 2]
