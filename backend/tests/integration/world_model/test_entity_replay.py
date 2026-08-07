"""Phase 27-03 durable world entity projection: migration + restart replay.

Covers (REQ-WM-03):
- migration ``20260801_2703`` is single-head, reversible (upgrade/downgrade
  roundtrip), and reads back old rows written through the migration schema
  (old-DB compat).
- append projection -> restart (fresh engine/session on the same file) -> replay
  is byte-equivalent: sealed projection hash, per-row checksums and row counts
  are unchanged. First-class rule exceptions and review-only alias collisions
  survive the restart intact and stay queryable; alias collisions are never
  auto-resolved into a merge.
- repository rejects UPDATE (no update/delete/promote API), wrong-owner reads,
  and stale-version writes; tampered checksums fail closed.
- durable cutoff query (D-05) returns only visible rows with no raw leak;
  Reader Chat claims never become durable rows (D-06).
"""

from __future__ import annotations

import importlib.util
import inspect
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.base import Base
from app.models.novel import Novel
from app.models.user import User
from app.models.world_model_entity import (
    WorldModelAliasReview,
    WorldModelEntity,
    WorldModelEntityLink,
    WorldModelRule,
    WorldModelRuleException,
)
from app.services.world_model.entities import (
    AliasReviewStatus,
    EntityCandidateProjection,
    EntityClaim,
    EntityGate,
    EntityLinkClaim,
    EntityType,
    LinkKind,
    build_entity_candidate,
    entity_projection_checksum,
)
from app.services.world_model.entity_queries import WorldEntityQueries
from app.services.world_model.entity_repository import (
    WorldEntityRepository,
    WorldEntityRepositoryError,
)
from app.services.world_model.rules import (
    GateReason,
    RuleClaim,
    RuleExceptionClaim,
    RuleGate,
)

pytestmark = pytest.mark.integration

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "world_model"
        / "entities_v1.json"
    ).read_text(encoding="utf-8")
)

MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "migrations"
    / "versions"
    / ("20260801_2703_world_entity_projection.py")
)


def load_scenario(name: str) -> dict:
    return FIXTURE["scenarios"][name]


def build_valid_projection(*, version_id: int = 1) -> EntityCandidateProjection:
    """Run the 'valid' fixture through the gates into an immutable candidate."""
    scenario = load_scenario("valid")
    scope = scenario["scope"]
    egate = EntityGate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=version_id,
        source_snapshot_hash=scope["source_snapshot_hash"],
        disclosure_cutoff=scope["disclosure_cutoff"],
        approvals=frozenset(scope["approvals"]),
    )
    entities = []
    for raw in scenario["entities"]:
        result = egate.validate_entity(
            EntityClaim.model_validate({**raw, "version_id": version_id})
        )
        assert result.entity is not None, result.verdicts
        entities.append(result.entity)
    links = []
    for raw in scenario["links"]:
        result = egate.validate_link(
            EntityLinkClaim.model_validate({**raw, "version_id": version_id})
        )
        assert result.link is not None, result.verdicts
        links.append(result.link)
    rgate = RuleGate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=version_id,
        source_snapshot_hash=scope["source_snapshot_hash"],
        disclosure_cutoff=scope["disclosure_cutoff"],
        approvals=frozenset(scope["approvals"]),
    )
    rules = []
    for raw in scenario["rules"]:
        result = rgate.validate_rule(
            RuleClaim.model_validate({**raw, "version_id": version_id})
        )
        assert result.rule is not None, result.verdicts
        rules.append(result.rule)
    rule_keys = {rule.rule_key for rule in rules}
    exceptions = []
    for raw in scenario["exceptions"]:
        result = rgate.validate_exception(
            RuleExceptionClaim.model_validate({**raw, "version_id": version_id}),
            rule_keys,
        )
        assert result.exception is not None, result.verdicts
        exceptions.append(result.exception)
    return build_entity_candidate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=version_id,
        entities=entities,
        links=links,
        rules=rules,
        exceptions=exceptions,
    )


def build_collision_projection(*, version_id: int = 2) -> EntityCandidateProjection:
    """Alias-collision scenario: both entities retained, reviews produced."""
    scenario = load_scenario("alias_collision")
    scope = scenario["scope"]
    egate = EntityGate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=version_id,
        source_snapshot_hash=scope["source_snapshot_hash"],
        disclosure_cutoff=scope["disclosure_cutoff"],
        approvals=frozenset(scope["approvals"]),
    )
    entities = []
    for raw in scenario["entities"]:
        result = egate.validate_entity(
            EntityClaim.model_validate({**raw, "version_id": version_id})
        )
        assert result.entity is not None, result.verdicts
        entities.append(result.entity)
    return build_entity_candidate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=version_id,
        entities=entities,
    )


async def seed_scope(session: AsyncSession) -> None:
    session.add(
        User(id=1, username="author", email="author@example.com", hashed_password="x")
    )
    session.add(
        User(id=2, username="other", email="other@example.com", hashed_password="x")
    )
    session.add(Novel(id=1, owner_id=1, title="测试小说"))
    await session.flush()


async def make_engine_and_factory(tmp_path, db_name: str = "entity.db"):
    url = f"sqlite+aiosqlite:///{tmp_path / db_name}"
    engine = create_async_engine(url)
    # PostgreSQL owns the tsvector generated expression; SQLite needs the plain
    # Text variant (same workaround as tests/conftest.py db_session fixture).
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


def assert_projection_matches(
    actual: EntityCandidateProjection, expected: EntityCandidateProjection
) -> None:
    assert actual.projection_hash == expected.projection_hash
    assert actual.owner_id == expected.owner_id
    assert actual.novel_id == expected.novel_id
    assert actual.version_id == expected.version_id
    assert [e.model_dump(mode="json") for e in actual.entities] == [
        e.model_dump(mode="json") for e in expected.entities
    ]
    assert [link.model_dump(mode="json") for link in actual.links] == [
        link.model_dump(mode="json") for link in expected.links
    ]
    assert [r.model_dump(mode="json") for r in actual.rules] == [
        r.model_dump(mode="json") for r in expected.rules
    ]
    assert [x.model_dump(mode="json") for x in actual.exceptions] == [
        x.model_dump(mode="json") for x in expected.exceptions
    ]
    assert [r.model_dump(mode="json") for r in actual.alias_reviews] == [
        r.model_dump(mode="json") for r in expected.alias_reviews
    ]


# ---------------------------------------------------------------------------
# Migration compatibility
# ---------------------------------------------------------------------------


def test_migration_revision_chain_single_head():
    spec = importlib.util.spec_from_file_location("m_2703", MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "20260801_2703"
    assert mod.down_revision == "20260801_2702"


def test_model_tables_registered_in_metadata():
    tables = set(Base.metadata.tables)
    assert "world_model_entities" in tables
    assert "world_model_rules" in tables
    assert "world_model_rule_exceptions" in tables
    assert "world_model_entity_links" in tables
    assert "world_model_alias_reviews" in tables


def test_model_tables_have_no_active_pointer_or_promotion_columns():
    for table in (
        WorldModelEntity.__table__,
        WorldModelRule.__table__,
        WorldModelRuleException.__table__,
        WorldModelEntityLink.__table__,
        WorldModelAliasReview.__table__,
    ):
        columns = {column.name for column in table.columns}
        for forbidden in ("active_pointer", "promotion", "current_revision", "cutover"):
            assert forbidden not in columns, f"forbidden column leaked: {forbidden}"


def test_migration_upgrade_downgrade_is_reversible_and_old_rows_compat(tmp_path):
    """Migration DDL roundtrip + old-row readback (bypasses env.py)."""
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    from sqlalchemy import create_engine, inspect as sa_inspect

    spec = importlib.util.spec_from_file_location("m_2703b", MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    conn = engine.connect()
    ctx = MigrationContext.configure(conn, opts={})

    with Operations.context(ctx):
        mod.upgrade()
        for table in (
            "world_model_entities",
            "world_model_rules",
            "world_model_rule_exceptions",
            "world_model_entity_links",
            "world_model_alias_reviews",
        ):
            assert sa_inspect(conn).has_table(table), f"{table} missing after upgrade"

        # Old-row compatibility: a row written through the migration schema can
        # be read back by the ORM model (same column layout).
        conn.execute(
            text(
                """
                INSERT INTO world_model_entities
                (entity_key, entity_type, disclosure_cutoff, source_kind, authority,
                 confidence, gate_status, source_refs, aliases, lineage, owner_id,
                 novel_id, version_id, canonical_payload, canonical_payload_hash,
                 idempotency_key, projection_hash, schema_version, created_at,
                 updated_at)
                VALUES (:entity_key, 'place', 1, 'canon_source', 'probable_inference',
                 0.9, 'passed', '[]', '[]', '["e-old"]', 1,
                 1, 1, '{"entity_key":"e-old"}',
                 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                 'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
                 'world-model-entity.v1', '2026-08-03 00:00:00',
                 '2026-08-03 00:00:00')
                """
            ),
            {"entity_key": "e-old"},
        )
        conn.commit()
        row = conn.execute(
            text(
                "SELECT entity_key, entity_type, gate_status FROM world_model_entities"
            )
        ).fetchone()
        assert row == ("e-old", "place", "passed")

        mod.downgrade()
        for table in (
            "world_model_entities",
            "world_model_rules",
            "world_model_rule_exceptions",
            "world_model_entity_links",
            "world_model_alias_reviews",
        ):
            assert not sa_inspect(conn).has_table(table), (
                f"{table} still present after downgrade"
            )
    conn.close()
    engine.dispose()


# ---------------------------------------------------------------------------
# Restart replay byte-equivalence
# ---------------------------------------------------------------------------


async def test_restart_replay_is_byte_equivalent(tmp_path):
    """Commit -> restart (fresh engine/session on the same file) -> identical."""
    engine, factory = await make_engine_and_factory(tmp_path)
    projection = build_valid_projection(version_id=1)

    async with factory() as session:
        await seed_scope(session)
        repo = WorldEntityRepository(session)
        await repo.append_projection(projection)
        await session.commit()
        counts = {
            "entities": await session.scalar(
                select(func.count()).select_from(WorldModelEntity)
            ),
            "rules": await session.scalar(
                select(func.count()).select_from(WorldModelRule)
            ),
            "exceptions": await session.scalar(
                select(func.count()).select_from(WorldModelRuleException)
            ),
            "links": await session.scalar(
                select(func.count()).select_from(WorldModelEntityLink)
            ),
            "reviews": await session.scalar(
                select(func.count()).select_from(WorldModelAliasReview)
            ),
        }

    await engine.dispose()
    engine2, factory2 = await make_engine_and_factory(tmp_path)

    async with factory2() as session:
        repo2 = WorldEntityRepository(session)
        replayed = await repo2.replay_projection(owner_id=1, novel_id=1, version_id=1)
        assert_projection_matches(replayed, projection)
        assert len(replayed.entities) == counts["entities"] == 4
        assert len(replayed.rules) == counts["rules"] == 1
        assert len(replayed.exceptions) == counts["exceptions"] == 1
        assert len(replayed.links) == counts["links"] == 4
        assert len(replayed.alias_reviews) == counts["reviews"] == 0
        assert entity_projection_checksum(replayed) == replayed.projection_hash

        # Idempotent re-append replays; no duplicate rows.
        await repo2.append_projection(projection)
        await session.flush()
        assert (
            await session.scalar(select(func.count()).select_from(WorldModelEntity))
        ) == 4
        assert (
            await session.scalar(
                select(func.count()).select_from(WorldModelRuleException)
            )
        ) == 1

        assert await repo2.list_versions(owner_id=1, novel_id=1) == [1]
    await engine2.dispose()


async def test_rule_exceptions_and_alias_reviews_survive_restart(tmp_path):
    """First-class exceptions and review-only alias collisions are durable."""
    engine, factory = await make_engine_and_factory(tmp_path)

    async with factory() as session:
        await seed_scope(session)
        repo = WorldEntityRepository(session)
        await repo.append_projection(build_valid_projection(version_id=1))
        await repo.append_projection(build_collision_projection(version_id=2))
        await session.commit()

    await engine.dispose()
    engine2, factory2 = await make_engine_and_factory(tmp_path)
    async with factory2() as session:
        queries = WorldEntityQueries(session)
        # Exception survived replay, still bound to its rule.
        exceptions = await queries.query_rule_exceptions(
            owner_id=1, novel_id=1, version_id=1, rule_key="rule-seal"
        )
        assert [exc.exception_key for exc in exceptions] == ["exc-seal-usurp"]
        assert exceptions[0].applies_to == "e-char-lin-an"

        # Alias reviews survived replay as review candidates — never merged.
        reviews = await queries.query_alias_reviews(
            owner_id=1, novel_id=1, version_id=2
        )
        assert len(reviews) == 2
        assert all(review.status == AliasReviewStatus.REVIEW for review in reviews)
        assert {review.review_key for review in reviews} == {
            "alias-review:e-faction-nan:e-faction-nanjiang",
            "alias-review:e-place-lin-an:e-place-lin-anfu",
        }
        # Both entities are still distinct after replay — nothing was merged.
        entities = await queries.query_entities(owner_id=1, novel_id=1, version_id=2)
        keys = {entity.entity_key for entity in entities}
        assert keys == {
            "e-faction-nan",
            "e-faction-nanjiang",
            "e-place-lin-an",
            "e-place-lin-anfu",
        }
    await engine2.dispose()


# ---------------------------------------------------------------------------
# Fail-closed: wrong owner, stale version, no UPDATE, tampering
# ---------------------------------------------------------------------------


async def test_wrong_owner_replay_fails_closed(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        await WorldEntityRepository(session).append_projection(
            build_valid_projection(version_id=1)
        )
        with pytest.raises(WorldEntityRepositoryError):
            await WorldEntityRepository(session).replay_projection(
                owner_id=2, novel_id=1, version_id=1
            )
        with pytest.raises(WorldEntityRepositoryError):
            await WorldEntityRepository(session).replay_projection(
                owner_id=1, novel_id=1, version_id=99
            )
    await engine.dispose()


async def test_stale_version_append_is_rejected(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        repo = WorldEntityRepository(session)
        await repo.append_projection(build_valid_projection(version_id=1))
        await repo.append_projection(build_valid_projection(version_id=2))
        with pytest.raises(WorldEntityRepositoryError) as excinfo:
            await repo.append_projection(build_valid_projection(version_id=1))
        assert "stale-version" in str(excinfo.value)
    await engine.dispose()


def test_repository_exposes_no_update_api():
    """Immutability: no UPDATE / DELETE / promote path (D-02)."""
    members = {
        name
        for name, _ in inspect.getmembers(WorldEntityRepository, predicate=callable)
    }
    assert not {m for m in members if m.startswith(("update", "delete", "promote"))}
    assert "append_projection" in members
    assert "replay_projection" in members


async def test_tampered_row_checksum_fails_closed(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        await WorldEntityRepository(session).append_projection(
            build_valid_projection(version_id=1)
        )
        row = (await session.scalars(select(WorldModelRuleException).limit(1))).first()
        assert row is not None
        row.canonical_payload_hash = "0" * 64  # tamper (test-only; no update API)
        await session.flush()
        with pytest.raises(WorldEntityRepositoryError):
            await WorldEntityRepository(session).replay_projection(
                owner_id=1, novel_id=1, version_id=1
            )
    await engine.dispose()


# ---------------------------------------------------------------------------
# Cutoff query (D-05) with typed entities, links, exceptions, lineage
# ---------------------------------------------------------------------------


async def test_cutoff_query_hides_future_rows(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        await WorldEntityRepository(session).append_projection(
            build_valid_projection(version_id=1)
        )
        queries = WorldEntityQueries(session)

        at_3 = await queries.query_world_projection(
            owner_id=1, novel_id=1, version_id=1, cutoff=3
        )
        assert at_3 is not None
        assert len(at_3.entities) == 4
        assert len(at_3.links) == 4
        assert [rule.rule_key for rule in at_3.rules] == ["rule-seal"]
        assert [exc.exception_key for exc in at_3.exceptions] == ["exc-seal-usurp"]

        at_2 = await queries.query_world_projection(
            owner_id=1, novel_id=1, version_id=1, cutoff=2
        )
        assert at_2 is not None
        assert {e.entity_key for e in at_2.entities} == {
            "e-place-lin-an",
            "e-faction-southern",
            "e-char-lin-an",
        }
        assert "e-item-seal" not in {e.entity_key for e in at_2.entities}
        assert {link.link_kind for link in at_2.links} == {
            LinkKind.MEMBER_OF,
            LinkKind.LOCATED_IN,
        }
        # Future rules/exceptions are hidden at cutoff 2 too.
        assert at_2.rules == ()
        assert at_2.exceptions == ()

        cross_owner = await queries.query_world_projection(
            owner_id=2, novel_id=1, version_id=1, cutoff=3
        )
        assert cross_owner is None, "cross-owner query fails closed"
    await engine.dispose()


async def test_typed_queries_return_entities_links_exceptions_and_lineage(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        await WorldEntityRepository(session).append_projection(
            build_valid_projection(version_id=1)
        )
        queries = WorldEntityQueries(session)

        places = await queries.query_entities(
            owner_id=1, novel_id=1, version_id=1, entity_type=EntityType.PLACE
        )
        assert [entity.primary_name for entity in places] == ["临安"]
        assert [alias.alias for alias in places[0].aliases] == ["临安城"]

        owns = await queries.query_links(
            owner_id=1, novel_id=1, version_id=1, link_kind=LinkKind.OWNS
        )
        assert owns and owns[0].source_key == "e-char-lin-an"
        assert owns[0].target_key == "e-item-seal"

        exceptions = await queries.query_rule_exceptions(
            owner_id=1, novel_id=1, version_id=1
        )
        assert [exc.exception_key for exc in exceptions] == ["exc-seal-usurp"]
        # The exception keeps its rule binding after replay.
        assert exceptions[0].rule_key == "rule-seal"

        lineage = await queries.query_entity_lineage(
            owner_id=1, novel_id=1, entity_key="e-place-lin-an"
        )
        assert [entity.entity_key for entity in lineage] == ["e-place-lin-an"]
        rule_lineage = await queries.query_rule_lineage(
            owner_id=1, novel_id=1, rule_key="rule-seal"
        )
        assert [rule.rule_key for rule in rule_lineage] == ["rule-seal"]

        rules = await queries.query_rules(owner_id=1, novel_id=1, version_id=1)
        assert rules[0].source_refs[0].evidence_id == "ev-seal"
    await engine.dispose()


async def test_reader_chat_claims_never_become_durable_rows(tmp_path):
    """D-06: chat contamination is rejected before any durable write."""
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        scenario = load_scenario("chat_contamination")
        scope = scenario["scope"]
        egate = EntityGate(
            owner_id=scope["owner_id"],
            novel_id=scope["novel_id"],
            version_id=scope["version_id"],
            source_snapshot_hash=scope["source_snapshot_hash"],
            disclosure_cutoff=scope["disclosure_cutoff"],
            approvals=frozenset(scope["approvals"]),
        )
        rgate = RuleGate(
            owner_id=scope["owner_id"],
            novel_id=scope["novel_id"],
            version_id=scope["version_id"],
            source_snapshot_hash=scope["source_snapshot_hash"],
            disclosure_cutoff=scope["disclosure_cutoff"],
            approvals=frozenset(scope["approvals"]),
        )
        for raw in scenario["entities"]:
            result = egate.validate_entity(EntityClaim.model_validate(raw))
            assert result.entity is None
            assert GateReason.CHAT_NOT_FACT_SOURCE in result.reason_codes
        for raw in scenario["rules"]:
            result = rgate.validate_rule(RuleClaim.model_validate(raw))
            assert result.rule is None
            assert GateReason.CHAT_NOT_FACT_SOURCE in result.reason_codes
        await session.flush()
        assert (
            await session.scalar(select(func.count()).select_from(WorldModelEntity))
        ) == 0
        assert (
            await session.scalar(select(func.count()).select_from(WorldModelRule))
        ) == 0
    await engine.dispose()


def test_query_module_exposes_read_only_api():
    members = {
        name for name, _ in inspect.getmembers(WorldEntityQueries, predicate=callable)
    }
    assert "query_world_projection" in members
    assert "query_entities" in members
    assert "query_links" in members
    assert "query_rule_exceptions" in members
    assert "query_alias_reviews" in members
    assert not {m for m in members if m.startswith(("append", "write", "update"))}
