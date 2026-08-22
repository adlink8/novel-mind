"""Append-only world entity repository + owner-scoped query API (REQ-WM-03)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novel import Novel
from app.models.user import User
from app.models.world_model_entity import (
    WorldModelEntity,
)
from app.services.world_model.entities import (
    EntityCandidateProjection,
    EntityClaim,
    EntityGate,
    EntityLinkClaim,
    EntityType,
    LinkKind,
    build_entity_candidate,
)
from app.services.world_model.entity_queries import (
    WorldEntityQueries,
)
from app.services.world_model.entity_repository import (
    WorldEntityRepository,
    WorldEntityRepositoryError,
)
from app.services.world_model.rules import RuleClaim, RuleExceptionClaim, RuleGate

pytestmark = pytest.mark.unit

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "world_model"
        / "entities_v1.json"
    ).read_text(encoding="utf-8")
)


def scenario(name: str) -> dict:
    return FIXTURE["scenarios"][name]


def build_valid(
    name: str = "valid", *, version_id: int = 1
) -> EntityCandidateProjection:
    """Run one fixture scenario through the gates into an immutable candidate."""
    sc = scenario(name)
    scope = sc["scope"]
    egate = EntityGate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=version_id,
        source_snapshot_hash=scope["source_snapshot_hash"],
        disclosure_cutoff=scope["disclosure_cutoff"],
        approvals=frozenset(scope["approvals"]),
    )
    entities = [
        egate.validate_entity(
            EntityClaim.model_validate({**raw, "version_id": version_id})
        ).entity
        for raw in sc["entities"]
    ]
    assert all(entity is not None for entity in entities), "all entities must gate"
    links = [
        egate.validate_link(
            EntityLinkClaim.model_validate({**raw, "version_id": version_id})
        ).link
        for raw in sc["links"]
    ]
    assert all(link is not None for link in links), "all links must gate"
    rgate = RuleGate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=version_id,
        source_snapshot_hash=scope["source_snapshot_hash"],
        disclosure_cutoff=scope["disclosure_cutoff"],
        approvals=frozenset(scope["approvals"]),
    )
    rules = [
        rgate.validate_rule(
            RuleClaim.model_validate({**raw, "version_id": version_id})
        ).rule
        for raw in sc["rules"]
    ]
    assert all(rule is not None for rule in rules), "all rules must gate"
    rule_keys = {rule.rule_key for rule in rules}
    exceptions = [
        rgate.validate_exception(
            RuleExceptionClaim.model_validate({**raw, "version_id": version_id}),
            rule_keys,
        ).exception
        for raw in sc["exceptions"]
    ]
    assert all(exc is not None for exc in exceptions), "all exceptions must gate"
    return build_entity_candidate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=version_id,
        entities=entities,
        links=links,
        rules=rules,
        exceptions=exceptions,
    )


async def _seed_owner_novel(db_session: AsyncSession):
    owner = User(username="wm-owner", email="wm@example.com", hashed_password="x")
    db_session.add(owner)
    await db_session.flush()
    novel = Novel(owner_id=owner.id, title="世界模型书", status="ready")
    db_session.add(novel)
    await db_session.flush()
    await db_session.commit()
    return owner, novel


# ---------------------------------------------------------------------------
# Repository: append / replay / list / fail-closed drift
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_append_and_replay_projection_roundtrip(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    projection = build_valid(version_id=1)
    repo = WorldEntityRepository(db_session)
    await repo.append_projection(projection)
    await db_session.commit()

    replayed = await repo.replay_projection(
        owner_id=projection.owner_id,
        novel_id=projection.novel_id,
        version_id=1,
    )
    assert replayed.projection_hash == projection.projection_hash
    assert [e.entity_key for e in replayed.entities] == [
        e.entity_key for e in projection.entities
    ]
    assert len(replayed.links) == len(projection.links)
    assert len(replayed.rules) == len(projection.rules)
    assert len(replayed.exceptions) == len(projection.exceptions)
    assert len(replayed.alias_reviews) == len(projection.alias_reviews)


@pytest.mark.asyncio
async def test_append_projection_unsealed_hash_fails(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    projection = build_valid(version_id=1)
    tampered = projection.model_copy(update={"projection_hash": "1" * 64})
    with pytest.raises(WorldEntityRepositoryError, match="not sealed"):
        await WorldEntityRepository(db_session).append_projection(tampered)


@pytest.mark.asyncio
async def test_append_projection_is_idempotent_on_key_conflict(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    repo = WorldEntityRepository(db_session)
    projection = build_valid(version_id=1)
    await repo.append_projection(projection)
    await db_session.commit()
    await repo.append_projection(projection)
    await db_session.commit()
    rows = list((await db_session.scalars(select(WorldModelEntity))).all())
    assert len(rows) == len(projection.entities)  # no duplicates


@pytest.mark.asyncio
async def test_replay_projection_missing_version_raises(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    with pytest.raises(WorldEntityRepositoryError, match="not found"):
        await WorldEntityRepository(db_session).replay_projection(
            owner_id=owner.id, novel_id=novel.id, version_id=999
        )


@pytest.mark.asyncio
async def test_append_stale_version_write_rejected(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    repo = WorldEntityRepository(db_session)
    await repo.append_projection(build_valid(version_id=5))
    await db_session.commit()
    with pytest.raises(WorldEntityRepositoryError, match="stale-version"):
        await repo.append_projection(build_valid(version_id=4))


@pytest.mark.asyncio
async def test_replay_detects_checksum_drift(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    repo = WorldEntityRepository(db_session)
    projection = build_valid(version_id=1)
    await repo.append_projection(projection)
    await db_session.commit()

    row = (await db_session.scalars(select(WorldModelEntity))).first()
    row.canonical_payload_hash = "0" * 64
    await db_session.commit()
    with pytest.raises(WorldEntityRepositoryError, match="checksum drift"):
        await repo.replay_projection(
            owner_id=projection.owner_id,
            novel_id=projection.novel_id,
            version_id=1,
        )


@pytest.mark.asyncio
async def test_replay_rejects_chat_source_row(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    repo = WorldEntityRepository(db_session)
    projection = build_valid(version_id=1)
    await repo.append_projection(projection)
    await db_session.commit()

    from app.services.world_model.rules import CHAT_SOURCE_KINDS

    chat_kind = next(iter(CHAT_SOURCE_KINDS)).value
    from app.services.world_model.entities import WorldEntity, entity_checksum

    row = (await db_session.scalars(select(WorldModelEntity))).first()
    row.source_kind = chat_kind
    # re-seal the payload hash so the drift check passes and the chat gate fires
    row.canonical_payload_hash = entity_checksum(
        WorldEntity.model_validate(row.canonical_payload)
    )
    await db_session.commit()
    with pytest.raises(WorldEntityRepositoryError, match="Reader Chat"):
        await repo.replay_projection(
            owner_id=projection.owner_id,
            novel_id=projection.novel_id,
            version_id=1,
        )


@pytest.mark.asyncio
async def test_list_versions_ascending(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    repo = WorldEntityRepository(db_session)
    await repo.append_projection(build_valid(version_id=2))
    await db_session.commit()
    await repo.append_projection(build_valid(version_id=3))
    await db_session.commit()
    versions = await repo.list_versions(owner_id=1, novel_id=1)
    assert versions == [2, 3]


# ---------------------------------------------------------------------------
# Queries: scope-first, disclosure-filtered reads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_query_world_projection_filters_by_cutoff(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    repo = WorldEntityRepository(db_session)
    projection = build_valid(version_id=1)
    await repo.append_projection(projection)
    await db_session.commit()

    queries = WorldEntityQueries(db_session)
    view = await queries.query_world_projection(
        owner_id=1, novel_id=1, version_id=1, cutoff=1
    )
    assert view is not None
    # e-item-seal has disclosure 3; hidden at cutoff 1
    keys = {e.entity_key for e in view.entities}
    assert "e-item-seal" not in keys
    at_3 = await queries.query_world_projection(
        owner_id=1, novel_id=1, version_id=1, cutoff=3
    )
    assert "e-item-seal" in {e.entity_key for e in at_3.entities}


@pytest.mark.asyncio
async def test_query_world_projection_none_when_no_visible_entities(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    view = await WorldEntityQueries(db_session).query_world_projection(
        owner_id=owner.id, novel_id=novel.id, version_id=1, cutoff=1
    )
    assert view is None


@pytest.mark.asyncio
async def test_query_entities_by_type_and_links_by_kind(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    repo = WorldEntityRepository(db_session)
    projection = build_valid(version_id=1)
    await repo.append_projection(projection)
    await db_session.commit()

    queries = WorldEntityQueries(db_session)
    places = await queries.query_entities(
        owner_id=1, novel_id=1, version_id=1, entity_type=EntityType.PLACE
    )
    assert all(e.entity_type == EntityType.PLACE for e in places)
    owns = await queries.query_links(
        owner_id=1, novel_id=1, version_id=1, link_kind=LinkKind.OWNS
    )
    assert owns[0].source_key == "e-char-lin-an"
    assert owns[0].target_key == "e-item-seal"


@pytest.mark.asyncio
async def test_query_rules_exceptions_and_alias_reviews(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    repo = WorldEntityRepository(db_session)
    projection = build_valid("rule_exception", version_id=3)
    await repo.append_projection(projection)
    await db_session.commit()

    queries = WorldEntityQueries(db_session)
    rules = await queries.query_rules(owner_id=1, novel_id=1, version_id=3)
    assert [r.rule_key for r in rules] == ["rule-magic"]
    exceptions = await queries.query_rule_exceptions(
        owner_id=1, novel_id=1, version_id=3, rule_key="rule-magic"
    )
    assert exceptions[0].exception_key == "exc-magic-moon"
    reviews = await queries.query_alias_reviews(owner_id=1, novel_id=1, version_id=3)
    assert reviews == ()


@pytest.mark.asyncio
async def test_query_lineage_matches_by_key_or_lineage(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    repo = WorldEntityRepository(db_session)
    await repo.append_projection(build_valid(version_id=1))
    await db_session.commit()
    await repo.append_projection(build_valid(version_id=2))
    await db_session.commit()

    queries = WorldEntityQueries(db_session)
    lineage = await queries.query_entity_lineage(
        owner_id=1, novel_id=1, entity_key="e-char-lin-an"
    )
    assert len(lineage) == 2
    rule_lineage = await queries.query_rule_lineage(
        owner_id=1, novel_id=1, rule_key="rule-magic"
    )
    assert rule_lineage == ()  # "valid" scenario carries no rules


@pytest.mark.asyncio
async def test_query_rule_lineage_across_versions(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    repo = WorldEntityRepository(db_session)
    await repo.append_projection(build_valid("rule_exception", version_id=1))
    await db_session.commit()
    await repo.append_projection(build_valid("rule_exception", version_id=2))
    await db_session.commit()

    queries = WorldEntityQueries(db_session)
    rule_lineage = await queries.query_rule_lineage(
        owner_id=1, novel_id=1, rule_key="rule-magic"
    )
    assert len(rule_lineage) == 2


@pytest.mark.asyncio
async def test_query_rule_exceptions_bound_and_alias_review_filter(db_session):
    owner, novel = await _seed_owner_novel(db_session)
    repo = WorldEntityRepository(db_session)
    projection = build_valid("alias_collision", version_id=2)
    await repo.append_projection(projection)
    await db_session.commit()

    queries = WorldEntityQueries(db_session)
    reviews = await queries.query_alias_reviews(owner_id=1, novel_id=1, version_id=2)
    assert len(reviews) == 2
    assert {r.review_key for r in reviews} == {
        "alias-review:e-faction-nan:e-faction-nanjiang",
        "alias-review:e-place-lin-an:e-place-lin-anfu",
    }
    # status filter is strict: no "resolved" reviews exist
    from app.services.world_model.entities import AliasReviewStatus

    none_resolved = await queries.query_alias_reviews(
        owner_id=1, novel_id=1, version_id=2, status=AliasReviewStatus.RESOLVED
    )
    assert none_resolved == ()


@pytest.mark.asyncio
async def test_queries_list_versions_and_read_only_surface():
    members = {
        name
        for name, _ in WorldEntityQueries.__dict__.items()
        if callable(getattr(WorldEntityQueries, name, None))
    }
    assert "query_world_projection" in members
    assert "query_entity_lineage" in members
    assert not {m for m in members if m.startswith(("append", "write", "update"))}
