"""Phase 37-01 derivative context package PostgreSQL API tests (REQ-FORK-03/REQ-CRE-05).

Covers the full compile/seal surface on the real CI database:

- compile one sealed package for an owned fork: complete fields, deterministic
  ordering, lineage + evidence + world/timeline/clue/world-rule dimensions and
  user intent all frozen; ``package_hash`` replays;
- identical re-compile replays the same sealed row; list/detail read it back;
- a requested cutoff can only shrink (future cutoff fails closed 400);
- world-state/timeline/rules/clues are cutoff-visible; a paid-off clue is never
  reported unresolved;
- a novel with no world/clue dimensions reports them ``unavailable`` — never
  fabricated; evidence stays available from the fork lineage;
- foreign-owner fork and rejected/archived fork fail closed (404/409);
- compilation writes nothing to any Canon space (Original chapters unchanged);
- package rows are immutable at the database level (update/delete fail closed).
"""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.derivative_context import ContextPackageRecord
from app.models.novel import Chapter, Novel
from app.models.user import User
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

PACKAGE_BASE = "/api/novels/{novel_id}/derivative-context-packages"
FORK_BASE = "/api/novels/{novel_id}/canon-fork"
HEX64 = "a" * 64


def async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    if sync_url.startswith("postgresql://"):
        return sync_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return sync_url


@pytest.fixture(scope="module")
def migrated_postgres(pg_sync_url: str, require_postgres: None) -> str:
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "head", database_url=pg_sync_url)
    return pg_sync_url


@pytest.fixture
async def api_client(migrated_postgres: str):
    aengine = create_async_engine(
        async_url(migrated_postgres), pool_pre_ping=True, poolclass=NullPool
    )
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, factory, migrated_postgres

    app.dependency_overrides.clear()
    await aengine.dispose()


def _seed_owner(sync_url: str, *, suffix: str, chapter_count: int = 3) -> dict:
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        user = User(
            username=f"dg_{suffix}",
            email=f"dg_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
            is_superuser=False,
        )
        session.add(user)
        session.flush()
        novel = Novel(
            title=f"DG Novel {suffix}",
            owner_id=user.id,
            status="ready",
            reading_progress={},
            chapter_count=chapter_count,
            word_count=sum(len(f"chapter {i} body") for i in range(1, chapter_count + 1)),
        )
        session.add(novel)
        session.flush()
        for i in range(1, chapter_count + 1):
            content = f"chapter {i} body"
            session.add(
                Chapter(
                    novel_id=novel.id,
                    chapter_number=i,
                    title=f"C{i}",
                    content=content,
                    word_count=len(content),
                )
            )
        session.commit()
        data = {
            "owner_id": user.id,
            "novel_id": novel.id,
            "token": create_access_token({"sub": str(user.id)}),
        }
    engine.dispose()
    return data


async def _create_fork(client, headers, novel_id, fork_key) -> dict:
    resp = await client.post(
        FORK_BASE.format(novel_id=novel_id), json={"fork_key": fork_key}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["fork"]


def _seed_world_model(sync_url: str, *, owner_id: int, novel_id: int) -> None:
    """One passed, cutoff-visible entity/rule/event/edge at version_id=1."""
    engine = create_engine(sync_url, poolclass=NullPool)
    with engine.connect() as conn:
        conn.execute(
            text(
                """
                INSERT INTO world_model_entities
                (entity_key, entity_type, disclosure_cutoff, source_kind, authority,
                 confidence, gate_status, source_refs, aliases, lineage, owner_id,
                 novel_id, version_id, canonical_payload, canonical_payload_hash,
                 idempotency_key, projection_hash, schema_version)
                VALUES ('hero', 'entity', 1, 'canon_source', 'canon_fact',
                 0.95, 'passed', '[]', '["Hero"]', '[]', :owner_id,
                 :novel_id, 1, '{"entity_key":"hero","name":"Aria"}',
                 :h, :h, :h, 'world-model-entity.v1')
                """
            ),
            {"owner_id": owner_id, "novel_id": novel_id, "h": HEX64},
        )
        conn.execute(
            text(
                """
                INSERT INTO world_model_rules
                (rule_key, disclosure_cutoff, source_kind, authority,
                 confidence, gate_status, source_refs, lineage, owner_id,
                 novel_id, version_id, canonical_payload, canonical_payload_hash,
                 idempotency_key, projection_hash, schema_version)
                VALUES ('magic-no-resurrection', 1, 'canon_source', 'canon_fact',
                 0.9, 'passed', '[]', '[]', :owner_id,
                 :novel_id, 1, '{"rule_key":"magic-no-resurrection","statement":"x"}',
                 :h, :h, :h, 'world-model-rule.v1')
                """
            ),
            {"owner_id": owner_id, "novel_id": novel_id, "h": HEX64},
        )
        conn.execute(
            text(
                """
                INSERT INTO world_model_events
                (event_key, owner_id, novel_id, version_id, authority,
                 confidence, effective_start, effective_end, disclosure_cutoff,
                 gate_status, source_refs, canonical_payload, canonical_payload_hash,
                 idempotency_key, projection_hash, schema_version)
                VALUES ('ev-seal-breaks', :owner_id, :novel_id, 1, 'canon_fact',
                 0.9, 1, 1, 1, 'passed', '[]',
                 '{"event_key":"ev-seal-breaks"}', :h, :h, :h, 'world-model-event.v1')
                """
            ),
            {"owner_id": owner_id, "novel_id": novel_id, "h": HEX64},
        )
        conn.execute(
            text(
                """
                INSERT INTO world_model_causal_edges
                (edge_key, owner_id, novel_id, version_id, source_event_key,
                 target_event_key, edge_type, authority, confidence,
                 disclosure_cutoff, gate_status, source_refs, canonical_payload,
                 canonical_payload_hash, idempotency_key, projection_hash,
                 schema_version)
                VALUES ('edge-seal', :owner_id, :novel_id, 1, 'ev-seal-breaks',
                 'ev-seal-breaks', 'caused', 'canon_fact', 0.9,
                 1, 'passed', '[]', '{"edge_key":"edge-seal"}',
                 :h, :h, :h, 'world-model-edge.v1')
                """
            ),
            {"owner_id": owner_id, "novel_id": novel_id, "h": HEX64},
        )
        conn.commit()
    engine.dispose()


def _seed_clues(sync_url: str, *, owner_id: int, novel_id: int) -> None:
    """One active (unresolved) clue and one paid-off clue in a validated version."""
    engine = create_engine(sync_url, poolclass=NullPool)
    with engine.connect() as conn:
        version_id = conn.execute(
            text(
                """
                INSERT INTO clue_analysis_versions (
                    owner_id, novel_id, version_key, status,
                    source_snapshot_hash, hierarchy_build_id, hierarchy_checksum,
                    prompt_hash, schema_hash, decoding_hash, config_hash, policy_hash,
                    model_lineage, price_snapshot, manifest
                ) VALUES (
                    :owner_id, :novel_id, 'ctx-v1', 'validated',
                    :h, 'build-1', :h, :h, :h, :h, :h, :h,
                    CAST('{}' AS json), CAST('{}' AS json), CAST('{}' AS json)
                ) RETURNING id
                """
            ),
            {"owner_id": owner_id, "novel_id": novel_id, "h": HEX64},
        ).scalar_one()

        def insert_clue(logical_id: str, title: str, first_cue: int) -> int:
            return conn.execute(
                text(
                    """
                    INSERT INTO machine_clues (
                        owner_id, novel_id, version_id, logical_clue_id, title, summary,
                        package_hash, package_snapshot, confidence, publication_status,
                        first_cue_chapter, first_cue_source_start
                    ) VALUES (
                        :owner_id, :novel_id, :version_id, :logical_id, :title, '',
                        :h, CAST('{}' AS json), 0.85, 'published', :first_cue, 0
                    ) RETURNING id
                    """
                ),
                {
                    "owner_id": owner_id,
                    "novel_id": novel_id,
                    "version_id": version_id,
                    "logical_id": logical_id,
                    "title": title,
                    "first_cue": first_cue,
                    "h": HEX64,
                },
            ).scalar_one()

        open_clue_id = insert_clue("clue-open", "Open secret", 1)
        payoff_clue_id = insert_clue("clue-payoff", "Paid off secret", 1)

        # Unresolved: candidate -> active.
        conn.execute(
            text(
                """
                INSERT INTO clue_lifecycle_events (
                    owner_id, novel_id, version_id, logical_clue_id, machine_clue_id,
                    from_status, to_status, actor_source, reason, event_key,
                    evidence_identities, gate_audit
                ) VALUES (
                    :owner_id, :novel_id, :version_id, 'clue-open', :clue_id,
                    'candidate', 'active', 'machine', 'detected', 'open-1',
                    CAST('[]' AS json), CAST('{}' AS json)
                )
                """
            ),
            {"owner_id": owner_id, "novel_id": novel_id, "version_id": version_id, "clue_id": open_clue_id},
        )
        # Paid off: candidate -> active -> reinforced -> paid_off (legal path).
        conn.execute(
            text(
                """
                INSERT INTO clue_lifecycle_events (
                    owner_id, novel_id, version_id, logical_clue_id, machine_clue_id,
                    from_status, to_status, actor_source, reason, event_key,
                    evidence_identities, cue_chapter, cue_source_start,
                    payoff_chapter, payoff_source_start, gate_audit
                ) VALUES (
                    :owner_id, :novel_id, :version_id, 'clue-payoff', :clue_id,
                    'candidate', 'active', 'machine', 'detected', 'payoff-1',
                    CAST('[]' AS json), 1, 0, NULL, NULL, CAST('{}' AS json)
                )
                """
            ),
            {"owner_id": owner_id, "novel_id": novel_id, "version_id": version_id, "clue_id": payoff_clue_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO clue_lifecycle_events (
                    owner_id, novel_id, version_id, logical_clue_id, machine_clue_id,
                    from_status, to_status, actor_source, reason, event_key,
                    evidence_identities, cue_chapter, cue_source_start,
                    payoff_chapter, payoff_source_start, gate_audit
                ) VALUES (
                    :owner_id, :novel_id, :version_id, 'clue-payoff', :clue_id,
                    'active', 'reinforced', 'machine', 'reinforced', 'payoff-2',
                    CAST('[]' AS json), 1, 0, NULL, NULL, CAST('{}' AS json)
                )
                """
            ),
            {"owner_id": owner_id, "novel_id": novel_id, "version_id": version_id, "clue_id": payoff_clue_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO clue_lifecycle_events (
                    owner_id, novel_id, version_id, logical_clue_id, machine_clue_id,
                    from_status, to_status, actor_source, reason, event_key,
                    evidence_identities, cue_chapter, cue_source_start,
                    payoff_chapter, payoff_source_start, gate_audit
                ) VALUES (
                    :owner_id, :novel_id, :version_id, 'clue-payoff', :clue_id,
                    'reinforced', 'paid_off', 'machine', 'payoff', 'payoff-3',
                    CAST('[]' AS json), 1, 0, 2, 0, CAST('{}' AS json)
                )
                """
            ),
            {"owner_id": owner_id, "novel_id": novel_id, "version_id": version_id, "clue_id": payoff_clue_id},
        )
        # Active pointer -> the validated version.
        conn.execute(
            text(
                """
                INSERT INTO clue_active_pointers (owner_id, novel_id, version_id, revision, manifest_checksum)
                VALUES (:owner_id, :novel_id, :version_id, 1, :h)
                """
            ),
            {"owner_id": owner_id, "novel_id": novel_id, "version_id": version_id, "h": HEX64},
        )
        conn.commit()
    engine.dispose()


async def _compile(client, headers, novel_id, fork_id, *, intent="continuation", **extra) -> dict:
    payload = {"fork_id": fork_id, "intent": intent}
    payload.update(extra)
    resp = await client.post(
        PACKAGE_BASE.format(novel_id=novel_id), json=payload, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Compile / seal / replay
# ---------------------------------------------------------------------------


async def test_compile_seals_complete_package(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"seal_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-seal")
    _seed_world_model(sync_url, owner_id=ids["owner_id"], novel_id=novel_id)
    _seed_clues(sync_url, owner_id=ids["owner_id"], novel_id=novel_id)

    body = await _compile(client, headers, novel_id, fork["id"], intent="continuation")
    pkg = body["package"]

    assert body["replayed"] is False
    assert pkg["space"] == "fanfiction_canon"
    assert pkg["intent"] == "continuation"
    assert pkg["fork_key"] == "ff-seal"
    assert pkg["through_chapter"] == 3
    assert pkg["source_snapshot_hash"] == fork["source_snapshot_hash"]
    assert pkg["cutoff_snapshot_hash"] == fork["cutoff_snapshot_hash"]
    assert pkg["scope_hash"] == fork["scope_hash"]
    assert pkg["manifest_hash"] == fork["manifest_hash"]
    assert len(pkg["package_hash"]) == 64
    assert pkg["budget_estimate"]["blocked"] is False

    payload = pkg["payload"]
    assert payload["schema_version"] == "derivative-context.v1"
    assert payload["intent"] == "continuation"
    assert payload["space"] == "fanfiction_canon"
    assert payload["version"]["through_chapter"] == 3

    dims = payload["dimensions"]
    assert dims["world_state"]["status"] == "available"
    assert [e["entity_key"] for e in dims["world_state"]["items"]] == ["hero"]
    assert dims["world_rules"]["status"] == "available"
    assert [r["rule_key"] for r in dims["world_rules"]["items"]] == ["magic-no-resurrection"]
    assert dims["timeline"]["status"] == "available"
    assert any(e.get("event_key") == "ev-seal-breaks" for e in dims["timeline"]["items"])
    assert any(e.get("edge_key") == "edge-seal" for e in dims["timeline"]["items"])

    # Only the unresolved clue is reported; the paid-off clue is not.
    clues = dims["unresolved_clues"]
    assert clues["status"] == "available"
    clue_ids = {c["logical_clue_id"] for c in clues["items"]}
    assert clue_ids == {"clue-open"}
    open_clue = next(c for c in clues["items"] if c["logical_clue_id"] == "clue-open")
    assert open_clue["status"] == "active"

    # Evidence refs come from the fork's citation lineage (branch-aware retrieval).
    evidence = dims["evidence"]
    assert evidence["status"] == "available"
    assert len(evidence["items"]) == 3
    assert all(ref["chapter_number"] <= 3 for ref in evidence["items"])
    assert evidence["trace"]["through_chapter"] == 3

    assert dims["user_intent"]["kind"] == "continuation"
    assert len(dims["user_intent"]["hash"]) == 64


async def test_compile_replay_returns_same_sealed_row(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"replay_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-replay")

    first = await _compile(client, headers, novel_id, fork["id"])
    second = await _compile(client, headers, novel_id, fork["id"])
    assert second["replayed"] is True
    assert second["package"]["id"] == first["package"]["id"]
    assert second["package"]["package_hash"] == first["package"]["package_hash"]


async def test_list_and_detail_read_back_sealed_package(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"read_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    base = PACKAGE_BASE.format(novel_id=novel_id)
    fork = await _create_fork(client, headers, novel_id, "ff-read")

    created = await _compile(client, headers, novel_id, fork["id"])
    pid = created["package"]["id"]

    listing = await client.get(base, headers=headers)
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == pid
    assert listing.json()["items"][0]["package_hash"] == created["package"]["package_hash"]

    detail = await client.get(f"{base}/{pid}", headers=headers)
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["package_hash"] == created["package"]["package_hash"]
    assert detail_body["payload"]["dimensions"]["evidence"]["status"] == "available"


# ---------------------------------------------------------------------------
# Cutoff semantics (client can only shrink)
# ---------------------------------------------------------------------------


async def test_requested_cutoff_can_shrink_scope(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"cut_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-cut")

    body = await _compile(
        client, headers, novel_id, fork["id"], through_chapter=2
    )
    pkg = body["package"]
    assert pkg["through_chapter"] == 2
    assert pkg["scope_hash"] != fork["scope_hash"]
    evidence = pkg["payload"]["dimensions"]["evidence"]
    assert all(ref["chapter_number"] <= 2 for ref in evidence["items"])
    assert len(evidence["items"]) == 2


async def test_future_cutoff_fails_closed(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"fut_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-fut")

    resp = await client.post(
        PACKAGE_BASE.format(novel_id=novel_id),
        json={"fork_id": fork["id"], "intent": "continuation", "through_chapter": 4},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    assert "cutoff_exceeds_scope" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Missing dimensions are honest (never fabricated)
# ---------------------------------------------------------------------------


async def test_missing_dimensions_reported_unavailable(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"naked_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-naked")

    body = await _compile(client, headers, novel_id, fork["id"])
    dims = body["package"]["payload"]["dimensions"]
    for key in ("world_state", "timeline", "unresolved_clues", "world_rules"):
        assert dims[key]["status"] == "unavailable", key
        assert dims[key]["items"] == []
    # Evidence still comes from the fork lineage — never fabricated empty.
    assert dims["evidence"]["status"] == "available"
    assert len(dims["evidence"]["items"]) == 3


# ---------------------------------------------------------------------------
# Owner / fork isolation and no original write-back
# ---------------------------------------------------------------------------


async def test_foreign_owner_fork_is_identical_404(api_client):
    client, _, sync_url = api_client
    a = _seed_owner(sync_url, suffix=f"fa_{uuid.uuid4().hex[:8]}")
    b = _seed_owner(sync_url, suffix=f"fb_{uuid.uuid4().hex[:8]}")
    headers_a = {"Authorization": f"Bearer {a['token']}"}
    headers_b = {"Authorization": f"Bearer {b['token']}"}
    fork_a = await _create_fork(client, headers_a, a["novel_id"], "ff-fa")

    resp = await client.post(
        PACKAGE_BASE.format(novel_id=b["novel_id"]),
        json={"fork_id": fork_a["id"], "intent": "continuation"},
        headers=headers_b,
    )
    assert resp.status_code == 404, resp.text
    assert "fork_not_found" in resp.json()["detail"]


async def test_archived_fork_cannot_anchor_package(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"arch_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-arch")

    engine = create_engine(sync_url, poolclass=NullPool)
    with engine.connect() as conn:
        conn.execute(
            text("UPDATE canon_forks SET status = 'archived' WHERE id = :fid"),
            {"fid": fork["id"]},
        )
        conn.commit()
    engine.dispose()

    resp = await client.post(
        PACKAGE_BASE.format(novel_id=novel_id),
        json={"fork_id": fork["id"], "intent": "continuation"},
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    assert "fork_not_usable" in resp.json()["detail"]


async def test_compile_writes_nothing_to_original_canon(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"noop_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-noop")

    engine = create_engine(sync_url, poolclass=NullPool)
    with engine.connect() as conn:
        before = conn.execute(
            text(
                "SELECT chapter_number, content FROM chapters "
                "WHERE novel_id = :novel_id ORDER BY chapter_number"
            ),
            {"novel_id": novel_id},
        ).fetchall()
    engine.dispose()

    await _compile(client, headers, novel_id, fork["id"])

    engine = create_engine(sync_url, poolclass=NullPool)
    with engine.connect() as conn:
        after = conn.execute(
            text(
                "SELECT chapter_number, content FROM chapters "
                "WHERE novel_id = :novel_id ORDER BY chapter_number"
            ),
            {"novel_id": novel_id},
        ).fetchall()
        assert after == before, "compilation must not mutate Original Canon chapters"
        # No Original / Interpretation artifacts were created.
        other = conn.execute(
            text(
                "SELECT COUNT(*) FROM canon_space_artifacts "
                "WHERE owner_id = :owner_id AND novel_id = :novel_id"
            ),
            {"owner_id": ids["owner_id"], "novel_id": novel_id},
        ).scalar_one()
        assert other == 0
        pkg_count = conn.execute(
            text(
                "SELECT COUNT(*) FROM derivative_context_packages "
                "WHERE owner_id = :owner_id AND novel_id = :novel_id"
            ),
            {"owner_id": ids["owner_id"], "novel_id": novel_id},
        ).scalar_one()
        assert pkg_count == 1
    engine.dispose()


# ---------------------------------------------------------------------------
# Database-level immutability
# ---------------------------------------------------------------------------


async def test_package_row_is_immutable_at_database_level(api_client, migrated_postgres):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"imm_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-imm")
    created = await _compile(client, headers, novel_id, fork["id"])

    aengine = create_async_engine(async_url(migrated_postgres), poolclass=NullPool)
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        row = await session.scalar(
            select(ContextPackageRecord).where(
                ContextPackageRecord.id == created["package"]["id"]
            )
        )
        assert row is not None
        row.intent = "rewrite"
        with pytest.raises(ValueError, match="immutable"):
            await session.flush()
        await session.rollback()

        row2 = await session.scalar(
            select(ContextPackageRecord).where(
                ContextPackageRecord.id == created["package"]["id"]
            )
        )
        with pytest.raises(ValueError, match="immutable"):
            await session.delete(row2)
            await session.flush()
        await session.rollback()
    await aengine.dispose()
