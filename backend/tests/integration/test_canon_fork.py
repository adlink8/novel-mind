"""Phase 35-02 Canon Fork PostgreSQL API integration tests (REQ-FORK-01/CRE-01).

Covers the fork create/read matrix on the real CI database:
- fork creation freezes owner/novel, the Original Canon version, the
  server-derived cutoff, the source snapshot/hash and the citation lineage;
  identical input replays the identical manifest hash;
- a conflicting ``fork_key`` retry and a stale ``expected_source_snapshot_hash``
  fail closed (409);
- a future cutoff can never expand the scope (400) and full-book requires an
  explicit server-side authorization (403 for an unauthorized owner);
- every route is owner-scoped: a mismatched owner/novel is an identical 404 and
  a foreign owner reads nothing;
- mutation only creates a candidate fork: ``active`` stays false, no active
  pointer is created, and the Original chapter bodies are never rewritten.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.canon_fork import CanonFork
from app.models.novel import Chapter, Novel
from app.models.user import User
from app.services.canon_fork.snapshot import (
    ForkChapterRecord,
    chapter_content_hash,
    compute_source_snapshot_hash,
)
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

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
    """ASGI client bound to module-migrated PostgreSQL (head incl. 35-02)."""

    aengine = create_async_engine(
        async_url(migrated_postgres),
        pool_pre_ping=True,
        poolclass=NullPool,
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


def _seed_owner(
    sync_url: str,
    *,
    suffix: str,
    chapter_count: int = 3,
    superuser: bool = False,
) -> dict:
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        user = User(
            username=f"fk_{suffix}",
            email=f"fk_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
            is_superuser=superuser,
        )
        session.add(user)
        session.flush()
        novel = Novel(
            title=f"FK Novel {suffix}",
            owner_id=user.id,
            status="ready",
            reading_progress={},
            chapter_count=chapter_count,
            word_count=sum(len(f"chapter {i} body") for i in range(1, chapter_count + 1)),
        )
        session.add(novel)
        session.flush()
        chapter_ids: list[int] = []
        records: list[ForkChapterRecord] = []
        for i in range(1, chapter_count + 1):
            content = f"chapter {i} body"
            chapter = Chapter(
                novel_id=novel.id,
                chapter_number=i,
                title=f"C{i}",
                content=content,
                word_count=len(content),
            )
            session.add(chapter)
            session.flush()
            chapter_ids.append(chapter.id)
            records.append(
                ForkChapterRecord(
                    chapter_id=chapter.id, chapter_number=i, content=content
                )
            )
        snapshot_hash = compute_source_snapshot_hash(
            owner_id=user.id, novel_id=novel.id, chapters=tuple(records)
        )
        session.commit()
        data = {
            "owner_id": user.id,
            "novel_id": novel.id,
            "chapter_ids": chapter_ids,
            "snapshot_hash": snapshot_hash,
            "token": create_access_token({"sub": str(user.id)}),
        }
    engine.dispose()
    return data


def _fork_payload(
    fork_key: str = "ff-main",
    *,
    cutoff: int | None = None,
    full_book: bool = False,
    expected_hash: str | None = None,
) -> dict:
    body: dict = {"fork_key": fork_key}
    if cutoff is not None:
        body["requested_cutoff_chapter"] = cutoff
    if full_book:
        body["full_book_requested"] = True
    if expected_hash is not None:
        body["expected_source_snapshot_hash"] = expected_hash
    return body


# ---------------------------------------------------------------------------
# Frozen fork manifest (owner/novel/version/cutoff/snapshot/lineage)
# ---------------------------------------------------------------------------


async def test_create_fork_freezes_scope_cutoff_snapshot_and_lineage(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"freeze_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/canon-fork"

    resp = await client.post(base, json=_fork_payload(), headers=headers)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    fork = body["fork"]
    assert body["publication_status"] == "candidate"
    assert body["replayed"] is False

    assert fork["owner_id"] == ids["owner_id"]
    assert fork["novel_id"] == ids["novel_id"]
    assert fork["fork_key"] == "ff-main"
    assert fork["space"] == "fanfiction_canon"
    assert fork["status"] == "candidate"
    assert fork["active"] is False

    # Server-derived cutoff defaults to the novel chapter count.
    assert fork["through_chapter"] == 3
    assert fork["full_book_authorized"] is False

    # Frozen source snapshot/hash replay from the seeded chapters.
    assert fork["source_snapshot_hash"] == ids["snapshot_hash"]
    assert len(fork["source_snapshot_hash"]) == 64
    assert fork["source_snapshot_id"].startswith("novel:")
    # Frozen Original Canon version (server-derived, deterministic fallback).
    assert fork["source_version_key"].startswith("original:")

    # Frozen citation lineage sealed at the cutoff, bound to the snapshot.
    assert len(fork["citation_lineage"]) == 3
    for leaf in fork["citation_lineage"]:
        assert leaf["source_snapshot_hash"] == ids["snapshot_hash"]
        assert len(leaf["content_hash"]) == 64
        assert leaf["chapter_number"] <= fork["through_chapter"]

    # Deterministic sealed hashes and the auditable authorization record.
    assert len(fork["scope_hash"]) == 64
    assert len(fork["cutoff_snapshot_hash"]) == 64
    assert len(fork["manifest_hash"]) == 64
    assert fork["authorization"]["source"] == "server_chapter_limit"
    assert fork["authorization"]["novel_chapter_count"] == 3
    assert fork["authorization"]["granted_full_book"] is False


async def test_cutoff_scoped_fork_seals_only_visible_leaves(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"cutoff_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/canon-fork"

    resp = await client.post(
        base, json=_fork_payload("ff-ch2", cutoff=2), headers=headers
    )
    assert resp.status_code == 201, resp.text
    fork = resp.json()["fork"]
    assert fork["through_chapter"] == 2
    assert {leaf["chapter_number"] for leaf in fork["citation_lineage"]} == {1, 2}
    assert fork["full_book_authorized"] is False


# ---------------------------------------------------------------------------
# Determinism / replay / conflicting retry
# ---------------------------------------------------------------------------


async def test_same_input_replays_same_manifest_hash(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"replay_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/canon-fork"
    payload = _fork_payload(cutoff=2)

    first = await client.post(base, json=payload, headers=headers)
    assert first.status_code == 201, first.text
    second = await client.post(base, json=payload, headers=headers)
    assert second.status_code == 201, second.text
    assert second.json()["replayed"] is True
    assert second.json()["fork"]["id"] == first.json()["fork"]["id"]
    assert (
        second.json()["fork"]["manifest_hash"] == first.json()["fork"]["manifest_hash"]
    )

    listing = await client.get(base, headers=headers)
    assert listing.json()["forks"][0]["manifest_hash"] == first.json()["fork"]["manifest_hash"]


async def test_conflicting_fork_key_fails_closed(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"conflict_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/canon-fork"

    assert (
        await client.post(base, json=_fork_payload(cutoff=2), headers=headers)
    ).status_code == 201
    # Same fork_key with a different frozen scope -> immutable conflict.
    conflict = await client.post(base, json=_fork_payload(cutoff=3), headers=headers)
    assert conflict.status_code == 409, conflict.text
    assert "fork_key_conflict" in conflict.json()["detail"]


async def test_stale_expected_source_snapshot_fails_closed(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"stale_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/canon-fork"

    resp = await client.post(
        base,
        json=_fork_payload("ff-stale", expected_hash=HEX64),
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    assert "stale_source_snapshot" in resp.json()["detail"]

    ok = await client.post(
        base,
        json=_fork_payload("ff-ok", expected_hash=ids["snapshot_hash"]),
        headers=headers,
    )
    assert ok.status_code == 201, ok.text


# ---------------------------------------------------------------------------
# Server-derived cutoff: no client expansion
# ---------------------------------------------------------------------------


async def test_future_cutoff_cannot_expand_scope(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"future_{uuid.uuid4().hex[:8]}", chapter_count=3)
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/canon-fork"

    resp = await client.post(
        base, json=_fork_payload("ff-future", cutoff=4), headers=headers
    )
    assert resp.status_code == 400, resp.text
    assert "cutoff_exceeds_scope" in resp.json()["detail"]


async def test_full_book_requires_explicit_authorization(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"fb_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/canon-fork"

    # Unauthorized owner cannot elevate to full-book.
    denied = await client.post(
        base, json=_fork_payload("ff-full", full_book=True), headers=headers
    )
    assert denied.status_code == 403, denied.text
    assert "full_book_requires_authorization" in denied.json()["detail"]

    # An authorized (superuser) owner seals an auditable full-book scope.
    sup = _seed_owner(sync_url, suffix=f"sup_{uuid.uuid4().hex[:8]}", superuser=True)
    sup_headers = {"Authorization": f"Bearer {sup['token']}"}
    allowed = await client.post(
        f"/api/novels/{sup['novel_id']}/canon-fork",
        json=_fork_payload("ff-full", full_book=True),
        headers=sup_headers,
    )
    assert allowed.status_code == 201, allowed.text
    fork = allowed.json()["fork"]
    assert fork["full_book_authorized"] is True
    assert fork["authorization"]["source"] == "server_superuser"
    assert fork["authorization"]["granted_full_book"] is True
    assert fork["through_chapter"] == 3


# ---------------------------------------------------------------------------
# Owner scope / IDOR matrix
# ---------------------------------------------------------------------------


async def test_cross_owner_fork_routes_return_404(api_client):
    client, _, sync_url = api_client
    ids_a = _seed_owner(sync_url, suffix=f"a_{uuid.uuid4().hex[:8]}")
    ids_b = _seed_owner(sync_url, suffix=f"b_{uuid.uuid4().hex[:8]}")
    headers_a = {"Authorization": f"Bearer {ids_a['token']}"}
    headers_b = {"Authorization": f"Bearer {ids_b['token']}"}
    base_a = f"/api/novels/{ids_a['novel_id']}/canon-fork"

    created = await client.post(
        base_a, json=_fork_payload("ff-a"), headers=headers_a
    )
    assert created.status_code == 201, created.text
    fork_id = created.json()["fork"]["id"]

    # Owner B probing owner A's novel: every route is an identical 404.
    foreign_list = await client.get(base_a, headers=headers_b)
    assert foreign_list.status_code == 404
    foreign_detail = await client.get(f"{base_a}/{fork_id}", headers=headers_b)
    assert foreign_detail.status_code == 404
    foreign_create = await client.post(
        base_a, json=_fork_payload("ff-b"), headers=headers_b
    )
    assert foreign_create.status_code == 404

    missing_novel = await client.get(
        "/api/novels/999999991/canon-fork", headers=headers_b
    )
    assert missing_novel.status_code == 404
    assert foreign_list.json() == missing_novel.json()

    # B reads nothing for B's own novel; A still reads A's sealed fork.
    ok_b = await client.get(
        f"/api/novels/{ids_b['novel_id']}/canon-fork", headers=headers_b
    )
    assert ok_b.status_code == 200
    assert ok_b.json()["forks"] == []
    ok_a = await client.get(base_a, headers=headers_a)
    assert ok_a.json()["forks"][0]["id"] == fork_id


async def test_foreign_owner_detail_is_404(api_client):
    client, _, sync_url = api_client
    ids_a = _seed_owner(sync_url, suffix=f"ad_{uuid.uuid4().hex[:8]}")
    ids_b = _seed_owner(sync_url, suffix=f"bd_{uuid.uuid4().hex[:8]}")
    headers_a = {"Authorization": f"Bearer {ids_a['token']}"}
    headers_b = {"Authorization": f"Bearer {ids_b['token']}"}

    created = await client.post(
        f"/api/novels/{ids_a['novel_id']}/canon-fork",
        json=_fork_payload("ff-a"),
        headers=headers_a,
    )
    assert created.status_code == 201
    fork_id = created.json()["fork"]["id"]

    # B asks for A's fork_id under B's own novel: identical 404.
    resp = await client.get(
        f"/api/novels/{ids_b['novel_id']}/canon-fork/{fork_id}", headers=headers_b
    )
    assert resp.status_code == 404

    # A reads its own fork by id.
    ok = await client.get(
        f"/api/novels/{ids_a['novel_id']}/canon-fork/{fork_id}", headers=headers_a
    )
    assert ok.status_code == 200
    assert ok.json()["fork"]["id"] == fork_id


# ---------------------------------------------------------------------------
# Candidate-only / no active pointer / Original immutability
# ---------------------------------------------------------------------------


async def test_create_never_creates_active_pointer_or_rewrites_source(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"immut_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = f"/api/novels/{ids['novel_id']}/canon-fork"

    resp = await client.post(base, json=_fork_payload(), headers=headers)
    assert resp.status_code == 201, resp.text
    fork = resp.json()["fork"]
    # Candidate-only envelope: no active pointer fields, publication is candidate.
    assert "active_pointer" not in fork
    assert resp.json()["publication_status"] == "candidate"
    assert fork["active"] is False

    # The persisted row is candidate-only at the database level too.
    async with factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(CanonFork).where(
                        CanonFork.owner_id == ids["owner_id"],
                        CanonFork.novel_id == ids["novel_id"],
                    )
                )
            ).all()
        )
        assert len(rows) == 1
        assert rows[0].active is False
        assert rows[0].status == "candidate"

    # The authoritative Original chapter bodies are untouched by fork creation.
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        stored = session.get(Chapter, ids["chapter_ids"][0])
        assert stored.content == "chapter 1 body"
    engine.dispose()


async def test_unauthenticated_fork_routes_reject(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"anon_{uuid.uuid4().hex[:8]}")
    base = f"/api/novels/{ids['novel_id']}/canon-fork"
    resp = await client.get(base)
    assert resp.status_code == 401
    resp2 = await client.post(base, json=_fork_payload())
    assert resp2.status_code == 401
