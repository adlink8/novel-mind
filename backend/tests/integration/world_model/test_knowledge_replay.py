"""Phase 27-02 durable epistemic history: migration + restart replay.

Covers (REQ-WM-02):
- migration ``20260801_2702`` is single-head, reversible (upgrade/downgrade
  roundtrip), and reads back old rows written through the migration schema
  (old-DB compat).
- append projection -> restart (fresh engine/session on the same file) -> replay
  is byte-equivalent: sealed projection hash, per-row checksums, row counts and
  mistaken beliefs / contradictions are unchanged; idempotent re-append creates
  no duplicate rows.
- repository rejects UPDATE (no update/delete/promote API), wrong-owner reads,
  and stale-version writes.
- durable cutoff/POV query (D-05) returns only visible rows with no raw leak;
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
from app.models.world_model_knowledge import WorldModelKnowledge
from app.services.world_model.contracts import Authority
from app.services.world_model.knowledge import (
    EpistemicClaim,
    EpistemicGate,
    EpistemicStatus,
    GateReason,
    KnowledgeCandidateProjection,
    KnowledgeResultStatus,
    projection_checksum,
)
from app.services.world_model.knowledge_queries import KnowledgeQueries
from app.services.world_model.knowledge_repository import (
    KnowledgeRepository,
    KnowledgeRepositoryError,
)

pytestmark = pytest.mark.integration

FIXTURE = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "world_model"
        / "epistemic_v1.json"
    ).read_text(encoding="utf-8")
)

MIGRATION_PATH = (
    Path(__file__).resolve().parents[3]
    / "migrations"
    / "versions"
    / ("20260801_2702_world_knowledge_projection.py")
)


def load_scenario(name: str) -> dict:
    return FIXTURE["scenarios"][name]


def build_valid_projection(*, version_id: int = 1) -> KnowledgeCandidateProjection:
    """Run the 'valid' fixture through the epistemic gate into a candidate."""
    scenario = load_scenario("valid")
    scope = scenario["scope"]
    gate = EpistemicGate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=version_id,
        source_snapshot_hash=scope["source_snapshot_hash"],
        disclosure_cutoff=scope["disclosure_cutoff"],
        approvals=frozenset(scope["approvals"]),
    )
    claims: list[EpistemicClaim] = []
    for raw in scenario["claims"]:
        result = gate.validate_claim(
            EpistemicClaim.model_validate({**raw, "version_id": version_id})
        )
        assert result.claim is not None, result.verdicts
        claims.append(result.claim)
    from app.services.world_model.knowledge import build_knowledge_candidate

    return build_knowledge_candidate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=version_id,
        claims=claims,
    )


def build_contradiction_projection(
    *, version_id: int = 10
) -> KnowledgeCandidateProjection:
    """Fixture contradiction scenario: both claims preserved, never overwritten."""
    scenario = load_scenario("contradiction")
    scope = scenario["scope"]
    gate = EpistemicGate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=version_id,
        source_snapshot_hash=scope["source_snapshot_hash"],
        disclosure_cutoff=scope["disclosure_cutoff"],
        approvals=frozenset(scope["approvals"]),
    )
    claims: list[EpistemicClaim] = []
    for raw in scenario["claims"]:
        result = gate.validate_claim(
            EpistemicClaim.model_validate({**raw, "version_id": version_id})
        )
        assert result.claim is not None, result.verdicts
        claims.append(result.claim)
    from app.services.world_model.knowledge import build_knowledge_candidate

    return build_knowledge_candidate(
        owner_id=scope["owner_id"],
        novel_id=scope["novel_id"],
        version_id=version_id,
        claims=claims,
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


async def make_engine_and_factory(tmp_path, db_name: str = "knowledge.db"):
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
    actual: KnowledgeCandidateProjection, expected: KnowledgeCandidateProjection
) -> None:
    assert actual.projection_hash == expected.projection_hash
    assert actual.owner_id == expected.owner_id
    assert actual.novel_id == expected.novel_id
    assert actual.version_id == expected.version_id
    assert [c.model_dump(mode="json") for c in actual.claims] == [
        c.model_dump(mode="json") for c in expected.claims
    ]


# ---------------------------------------------------------------------------
# Migration compatibility
# ---------------------------------------------------------------------------


def test_migration_revision_chain_single_head():
    spec = importlib.util.spec_from_file_location("m_2702", MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.revision == "20260801_2702"
    assert mod.down_revision == "20260801_2701"


def test_model_table_registered_in_metadata():
    tables = set(Base.metadata.tables)
    assert "world_model_knowledge" in tables


def test_model_table_has_no_active_pointer_or_promotion_columns():
    columns = {column.name for column in WorldModelKnowledge.__table__.columns}
    for forbidden in ("active_pointer", "promotion", "current_revision", "cutover"):
        assert forbidden not in columns, f"forbidden column leaked: {forbidden}"


def test_migration_upgrade_downgrade_is_reversible_and_old_rows_compat(tmp_path):
    """Migration DDL roundtrip + old-row readback (bypasses env.py)."""
    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    from sqlalchemy import create_engine, inspect as sa_inspect

    spec = importlib.util.spec_from_file_location("m_2702b", MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    conn = engine.connect()
    ctx = MigrationContext.configure(conn, opts={})

    with Operations.context(ctx):
        mod.upgrade()
        assert sa_inspect(conn).has_table("world_model_knowledge")
        # Old-row compatibility: a row written through the migration schema can
        # be read back by the ORM model (same column layout).
        conn.execute(
            text(
                """
                INSERT INTO world_model_knowledge
                (knowledge_key, subject, aspect, known_at, disclosure_cutoff, pov,
                 pov_kind, source_kind, authority, confidence, epistemic_status,
                 transition_from, lineage, source_refs, gate_status, owner_id,
                 novel_id, version_id, canonical_payload, canonical_payload_hash,
                 idempotency_key, projection_hash, schema_version, created_at,
                 updated_at)
                VALUES (:knowledge_key, :subject, :aspect, 1, 2, :pov,
                 'character', 'canon_source', 'probable_inference', 0.8,
                 'asserted', NULL, '["k-old"]', '[]', 'passed', 1,
                 1, 1, '{"knowledge_key":"k-old"}',
                 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                 'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
                 'world-model-knowledge.v1', '2026-08-03 00:00:00',
                 '2026-08-03 00:00:00')
                """
            ),
            {
                "knowledge_key": "k-old",
                "subject": "lin-an",
                "pov": "lin-an",
                "aspect": "knowledge",
            },
        )
        conn.commit()
        row = conn.execute(
            text(
                "SELECT knowledge_key, subject, gate_status FROM world_model_knowledge"
            )
        ).fetchone()
        assert row == ("k-old", "lin-an", "passed")

        mod.downgrade()
        assert not sa_inspect(conn).has_table("world_model_knowledge")
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
        repo = KnowledgeRepository(session)
        await repo.append_projection(projection)
        await session.commit()
        row_count = await session.scalar(
            select(func.count()).select_from(WorldModelKnowledge)
        )

    await engine.dispose()
    engine2, factory2 = await make_engine_and_factory(tmp_path)

    async with factory2() as session:
        repo2 = KnowledgeRepository(session)
        replayed = await repo2.replay_projection(owner_id=1, novel_id=1, version_id=1)
        assert_projection_matches(replayed, projection)
        assert len(replayed.claims) == row_count == 8
        assert projection_checksum(replayed) == replayed.projection_hash
        # Mistaken belief and hidden knowledge survive the restart intact.
        statuses = {
            claim.knowledge_key: claim.epistemic_status for claim in replayed.claims
        }
        assert statuses["k-belief-ally"] == EpistemicStatus.MISTAKEN_BELIEF
        assert statuses["k-hidden-inheritance"] == EpistemicStatus.HIDDEN_KNOWLEDGE

        # Idempotent re-append replays; no duplicate rows.
        await repo2.append_projection(projection)
        await session.flush()
        assert (
            await session.scalar(select(func.count()).select_from(WorldModelKnowledge))
        ) == 8

        assert await repo2.list_versions(owner_id=1, novel_id=1) == [1]
    await engine2.dispose()


async def test_contradictions_and_mistaken_beliefs_survive_restart(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    projection = build_contradiction_projection(version_id=10)

    async with factory() as session:
        await seed_scope(session)
        await KnowledgeRepository(session).append_projection(projection)
        await session.commit()

    await engine.dispose()
    engine2, factory2 = await make_engine_and_factory(tmp_path)
    async with factory2() as session:
        replayed = await KnowledgeRepository(session).replay_projection(
            owner_id=1, novel_id=1, version_id=10
        )
        assert_projection_matches(replayed, projection)
        assert len(replayed.claims) == 2
        by_key = {claim.knowledge_key: claim for claim in replayed.claims}
        # The contradiction is explicit, never resolved by overwrite (D-04).
        assert by_key["k-alive-fact"].epistemic_status == EpistemicStatus.CONTRADICTION
        assert by_key["k-death-rumor"].epistemic_status == EpistemicStatus.ASSERTED
        assert replayed.projection_hash == projection.projection_hash
    await engine2.dispose()


async def test_wrong_owner_replay_fails_closed(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        await KnowledgeRepository(session).append_projection(
            build_valid_projection(version_id=1)
        )
        with pytest.raises(KnowledgeRepositoryError):
            await KnowledgeRepository(session).replay_projection(
                owner_id=2, novel_id=1, version_id=1
            )
        with pytest.raises(KnowledgeRepositoryError):
            await KnowledgeRepository(session).replay_projection(
                owner_id=1, novel_id=1, version_id=99
            )
    await engine.dispose()


async def test_stale_version_append_is_rejected(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        repo = KnowledgeRepository(session)
        await repo.append_projection(build_valid_projection(version_id=1))
        await repo.append_projection(build_valid_projection(version_id=2))
        with pytest.raises(KnowledgeRepositoryError) as excinfo:
            await repo.append_projection(build_valid_projection(version_id=1))
        assert "stale-version" in str(excinfo.value)
    await engine.dispose()


def test_repository_exposes_no_update_api():
    """Immutability: no UPDATE / DELETE / promote path (D-02)."""
    members = {
        name for name, _ in inspect.getmembers(KnowledgeRepository, predicate=callable)
    }
    assert not {m for m in members if m.startswith(("update", "delete", "promote"))}
    assert "append_projection" in members
    assert "replay_projection" in members


async def test_tampered_row_checksum_fails_closed(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        await KnowledgeRepository(session).append_projection(
            build_valid_projection(version_id=1)
        )
        row = (await session.scalars(select(WorldModelKnowledge).limit(1))).first()
        row.canonical_payload_hash = "0" * 64  # tamper (test-only; no update API)
        await session.flush()
        with pytest.raises(KnowledgeRepositoryError):
            await KnowledgeRepository(session).replay_projection(
                owner_id=1, novel_id=1, version_id=1
            )
    await engine.dispose()


async def test_reader_chat_claims_never_become_durable_rows(tmp_path):
    """D-06: chat contamination is rejected before any durable write."""
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        scenario = load_scenario("chat_contamination")
        scope = scenario["scope"]
        gate = EpistemicGate(
            owner_id=scope["owner_id"],
            novel_id=scope["novel_id"],
            version_id=scope["version_id"],
            source_snapshot_hash=scope["source_snapshot_hash"],
            disclosure_cutoff=scope["disclosure_cutoff"],
            approvals=frozenset(scope["approvals"]),
        )
        for raw in scenario["claims"]:
            result = gate.validate_claim(EpistemicClaim.model_validate(raw))
            assert result.claim is None
            assert GateReason.CHAT_NOT_FACT_SOURCE in result.reason_codes
        await session.flush()
        assert (
            await session.scalar(select(func.count()).select_from(WorldModelKnowledge))
        ) == 0
    await engine.dispose()


# ---------------------------------------------------------------------------
# Durable cutoff/POV query (D-05) with evidence and lineage
# ---------------------------------------------------------------------------


async def test_durable_cutoff_query_hides_future_and_hidden_knowledge(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        await KnowledgeRepository(session).append_projection(
            build_valid_projection(version_id=1)
        )
        queries = KnowledgeQueries(session)

        # Chapter-4 reader view: hidden inheritance (disclosure 8) stays hidden.
        at_4 = await queries.query_character_knowledge(
            owner_id=1, novel_id=1, version_id=1, subject="lin-an", cutoff=4
        )
        assert at_4.status == KnowledgeResultStatus.ANSWERED
        keys = {claim.knowledge_key for claim in at_4.claims}
        assert "k-hidden-inheritance" not in keys
        assert "k-state-declare" not in keys
        assert all(claim.disclosure_cutoff <= 4 for claim in at_4.claims)

        # Full-book view discloses the hidden inheritance (author POV).
        full = await queries.query_character_knowledge(
            owner_id=1, novel_id=1, version_id=1, subject="lin-an", cutoff=8
        )
        assert "k-hidden-inheritance" in {claim.knowledge_key for claim in full.claims}

        # Cross-owner query fails closed.
        missing = await queries.query_character_knowledge(
            owner_id=2, novel_id=1, version_id=1, subject="lin-an", cutoff=8
        )
        assert missing.status == KnowledgeResultStatus.ABSTAINED
    await engine.dispose()


async def test_durable_history_keeps_mistaken_belief_and_lineage(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        await KnowledgeRepository(session).append_projection(
            build_valid_projection(version_id=1)
        )
        queries = KnowledgeQueries(session)

        history = await queries.query_character_history(
            owner_id=1, novel_id=1, version_id=1, subject="lin-an"
        )
        by_key = {claim.knowledge_key: claim for claim in history}
        assert (
            by_key["k-belief-ally"].epistemic_status == EpistemicStatus.MISTAKEN_BELIEF
        )
        assert (
            by_key["k-hidden-inheritance"].epistemic_status
            == EpistemicStatus.HIDDEN_KNOWLEDGE
        )

        statuses = await queries.query_by_status(
            owner_id=1,
            novel_id=1,
            version_id=1,
            status=EpistemicStatus.MISTAKEN_BELIEF,
        )
        assert [claim.knowledge_key for claim in statuses] == ["k-belief-ally"]

        lineage = await queries.query_lineage(
            owner_id=1, novel_id=1, knowledge_key="k-state-court"
        )
        assert "k-state-court" in {claim.knowledge_key for claim in lineage}

        # Evidence travels with the answer.
        answered = await queries.query_character_knowledge(
            owner_id=1, novel_id=1, version_id=1, subject="lin-an", cutoff=8
        )
        assert answered.has_approval
        assert answered.evidence
    await engine.dispose()


async def test_durable_pov_and_authority_filters(tmp_path):
    engine, factory = await make_engine_and_factory(tmp_path)
    async with factory() as session:
        await seed_scope(session)
        await KnowledgeRepository(session).append_projection(
            build_valid_projection(version_id=1)
        )
        queries = KnowledgeQueries(session)

        # Wrong POV sees nothing authored from another character perspective.
        # (The valid fixture is all lin-an POV or omniscient, so lin-an POV works.)
        own = await queries.query_character_knowledge(
            owner_id=1,
            novel_id=1,
            version_id=1,
            subject="lin-an",
            cutoff=8,
            pov="lin-an",
        )
        assert own.status == KnowledgeResultStatus.ANSWERED
        assert all(
            claim.pov == "lin-an" or claim.pov_kind.value == "omniscient"
            for claim in own.claims
        )

        # Authority filter applies after scoping.
        only_canon = await queries.query_character_knowledge(
            owner_id=1,
            novel_id=1,
            version_id=1,
            subject="lin-an",
            cutoff=8,
            authorities=frozenset({Authority.CANON_FACT}),
        )
        assert {claim.knowledge_key for claim in only_canon.claims} == {"k-truth-ally"}
    await engine.dispose()


def test_query_module_exposes_read_only_api():
    members = {
        name for name, _ in inspect.getmembers(KnowledgeQueries, predicate=callable)
    }
    assert "query_character_knowledge" in members
    assert "query_character_history" in members
    assert "query_lineage" in members
    assert "query_by_status" in members
    assert not {m for m in members if m.startswith(("append", "write", "update"))}
