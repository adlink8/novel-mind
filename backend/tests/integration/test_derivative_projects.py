"""Phase 36-01 derivative project CRUD PostgreSQL API tests (REQ-CRE-03).

Covers the full owner-scoped CRUD surface plus the fail-closed gates on the
real CI database:

- create/list/detail/patch/delete/archive with the frozen fork lineage intact;
- duplicate name and duplicate explicit ``project_key`` conflict (409);
- a rejected/archived fork can never anchor a project (409) and a foreign fork
  is an identical 404;
- the Fanfiction-only write gate is enforced at the database level: a raw
  ``original_canon``/``user_interpretation`` fork row and a non-Fanfiction
  project row both violate their check constraints;
- project FK behavior: a project cannot reference a missing fork or owner;
- all mutations preserve the Fanfiction authority and fork lineage.
"""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.derivative_project import DerivativeProject
from app.models.novel import Chapter, Novel
from app.models.user import User
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

PROJECT_BASE = "/api/novels/{novel_id}/derivative-projects"
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
            username=f"dp_{suffix}",
            email=f"dp_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
            is_superuser=False,
        )
        session.add(user)
        session.flush()
        novel = Novel(
            title=f"DP Novel {suffix}",
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


async def _create_project(client, headers, novel_id, fork_id, name, **extra) -> dict:
    payload = {"fork_id": fork_id, "name": name}
    payload.update(extra)
    resp = await client.post(
        PROJECT_BASE.format(novel_id=novel_id), json=payload, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["project"]


# ---------------------------------------------------------------------------
# CRUD: create / list / detail / patch / archive / delete
# ---------------------------------------------------------------------------


async def test_crud_round_trip(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"crud_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = PROJECT_BASE.format(novel_id=ids["novel_id"])
    fork = await _create_fork(client, headers, ids["novel_id"], "ff-crud")

    created = await _create_project(client, headers, ids["novel_id"], fork["id"], "Round Trip")
    pid = created["id"]

    # List contains the row with scope/version lineage.
    listing = await client.get(base, headers=headers)
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == pid

    # Detail.
    detail = await client.get(f"{base}/{pid}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["name"] == "Round Trip"
    assert detail.json()["project_key"] == "round-trip"

    # Patch mutable state only.
    patched = await client.patch(
        f"{base}/{pid}", json={"name": "Renamed", "description": "hello"}, headers=headers
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["name"] == "Renamed"
    assert body["description"] == "hello"
    assert body["status"] == "active"
    # Frozen fork lineage is unchanged by the rename.
    assert body["manifest_hash"] == created["manifest_hash"]
    assert body["fork_id"] == fork["id"]
    assert body["space"] == "fanfiction_canon"

    # Archive (soft option) is reflected in list.
    archived = await client.patch(
        f"{base}/{pid}", json={"status": "archived"}, headers=headers
    )
    assert archived.json()["status"] == "archived"
    listing = await client.get(base, headers=headers)
    assert listing.json()["items"][0]["status"] == "archived"

    # Delete removes the row.
    deleted = await client.delete(f"{base}/{pid}", headers=headers)
    assert deleted.status_code == 204
    assert (await client.get(f"{base}/{pid}", headers=headers)).status_code == 404


async def test_empty_patch_is_rejected(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"patch_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = PROJECT_BASE.format(novel_id=ids["novel_id"])
    fork = await _create_fork(client, headers, ids["novel_id"], "ff-patch")
    pid = (await _create_project(client, headers, ids["novel_id"], fork["id"], "Patch Me"))["id"]
    resp = await client.patch(f"{base}/{pid}", json={}, headers=headers)
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Duplicate name / project_key conflicts
# ---------------------------------------------------------------------------


async def test_duplicate_name_fails_closed(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"dup_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = PROJECT_BASE.format(novel_id=ids["novel_id"])
    fork_a = await _create_fork(client, headers, ids["novel_id"], "ff-dup-a")
    fork_b = await _create_fork(client, headers, ids["novel_id"], "ff-dup-b")

    await _create_project(client, headers, ids["novel_id"], fork_a["id"], "Duplicate Name")

    # Same name under the same owner/novel is a conflict even on another fork.
    resp = await client.post(
        base,
        json={"fork_id": fork_b["id"], "name": "Duplicate Name"},
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    assert "name_conflict" in resp.json()["detail"]

    # Renaming an existing project onto a taken name is also a conflict.
    pid = (await _create_project(client, headers, ids["novel_id"], fork_a["id"], "Second"))["id"]
    resp = await client.patch(
        f"{base}/{pid}", json={"name": "Duplicate Name"}, headers=headers
    )
    assert resp.status_code == 409, resp.text


async def test_duplicate_project_key_fails_closed(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"key_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = PROJECT_BASE.format(novel_id=ids["novel_id"])
    fork_a = await _create_fork(client, headers, ids["novel_id"], "ff-key-a")
    fork_b = await _create_fork(client, headers, ids["novel_id"], "ff-key-b")

    await _create_project(
        client, headers, ids["novel_id"], fork_a["id"], "First", project_key="fixed-key"
    )
    resp = await client.post(
        base,
        json={"fork_id": fork_b["id"], "name": "Second", "project_key": "fixed-key"},
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    assert "project_key_conflict" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Fork usability gates: foreign / rejected / archived / wrong space
# ---------------------------------------------------------------------------


async def test_foreign_fork_is_identical_404(api_client):
    client, _, sync_url = api_client
    a = _seed_owner(sync_url, suffix=f"fa_{uuid.uuid4().hex[:8]}")
    b = _seed_owner(sync_url, suffix=f"fb_{uuid.uuid4().hex[:8]}")
    headers_a = {"Authorization": f"Bearer {a['token']}"}
    headers_b = {"Authorization": f"Bearer {b['token']}"}
    fork_a = await _create_fork(client, headers_a, a["novel_id"], "ff-fa")

    resp = await client.post(
        PROJECT_BASE.format(novel_id=b["novel_id"]),
        json={"fork_id": fork_a["id"], "name": "Foreign"},
        headers=headers_b,
    )
    assert resp.status_code == 404, resp.text
    assert "fork_not_found" in resp.json()["detail"]


async def test_rejected_or_archived_fork_cannot_anchor_a_project(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"rej_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = PROJECT_BASE.format(novel_id=ids["novel_id"])
    fork = await _create_fork(client, headers, ids["novel_id"], "ff-rej")

    # Directly move the candidate fork to a terminal status (append-only forks
    # only expose status; a rejected/archived fork must fail closed as an anchor).
    engine = create_engine(sync_url, poolclass=NullPool)
    with engine.connect() as conn:
        conn.execute(
            text("UPDATE canon_forks SET status = 'archived' WHERE id = :fid"),
            {"fid": fork["id"]},
        )
        conn.commit()
    engine.dispose()

    resp = await client.post(
        base, json={"fork_id": fork["id"], "name": "On Archived"}, headers=headers
    )
    assert resp.status_code == 409, resp.text
    assert "fork_not_usable" in resp.json()["detail"]


async def test_database_rejects_non_fanfiction_fork_space(api_client):
    """The Fanfiction-only fork gate exists at the database level (D-36-03)."""
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"sp_{uuid.uuid4().hex[:8]}")
    engine = create_engine(sync_url, poolclass=NullPool)
    with engine.connect() as conn:
        try:
            conn.execute(
                text(
                    "INSERT INTO canon_forks (owner_id, novel_id, fork_key, space,"
                    " status, source_version_key, source_snapshot_id,"
                    " source_snapshot_hash, through_chapter, full_book_authorized,"
                    " cutoff_snapshot_hash, scope_hash, manifest_hash,"
                    " citation_lineage, \"authorization\", active)"
                    " VALUES (:owner_id, :novel_id, 'ff-original', 'original_canon',"
                    " 'candidate', 'original:1', 'snap-1', :h, 1, false, :h, :h, :h,"
                    " '[]', '{}', false)"
                ),
                {
                    "owner_id": ids["owner_id"],
                    "novel_id": ids["novel_id"],
                    "h": HEX64,
                },
            )
        except IntegrityError as exc:
            assert "ck_canon_forks_space" in str(exc)
        else:
            pytest.fail("original_canon fork must be rejected by the DB constraint")
        finally:
            conn.rollback()
    engine.dispose()


async def test_database_rejects_non_fanfiction_project_space(api_client):
    """A project row can never be written into a non-Fanfiction space (D-36-03)."""
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"ps_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    fork = await _create_fork(client, headers, ids["novel_id"], "ff-ps")

    engine = create_engine(sync_url, poolclass=NullPool)
    with engine.connect() as conn:
        try:
            conn.execute(
                text(
                    "INSERT INTO derivative_projects (owner_id, novel_id, fork_id,"
                    " project_key, name, status, space, fork_key, source_version_key,"
                    " source_snapshot_hash, through_chapter, full_book_authorized,"
                    " cutoff_snapshot_hash, scope_hash, manifest_hash)"
                    " VALUES (:owner_id, :novel_id, :fork_id, 'pk', 'n', 'active',"
                    " 'original_canon', 'fk', 'v', :h, 1, false, :h, :h, :h)"
                ),
                {
                    "owner_id": ids["owner_id"],
                    "novel_id": ids["novel_id"],
                    "fork_id": fork["id"],
                    "h": HEX64,
                },
            )
        except IntegrityError as exc:
            assert "ck_derivative_projects_space" in str(exc)
        else:
            pytest.fail("original_canon project row must be rejected by the DB constraint")
        finally:
            conn.rollback()
    engine.dispose()


# ---------------------------------------------------------------------------
# Project FK behavior
# ---------------------------------------------------------------------------


async def test_project_cannot_reference_missing_fork(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"fk_{uuid.uuid4().hex[:8]}")
    resp = await client.post(
        PROJECT_BASE.format(novel_id=ids["novel_id"]),
        json={"fork_id": 999999991, "name": "Missing Fork"},
        headers={"Authorization": f"Bearer {ids['token']}"},
    )
    assert resp.status_code == 404, resp.text
    assert "fork_not_found" in resp.json()["detail"]


async def test_database_fk_rejects_unknown_fork_id(api_client):
    """The project FK is enforced at the database level (T-36-01-02)."""
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"fkdb_{uuid.uuid4().hex[:8]}")
    engine = create_engine(sync_url, poolclass=NullPool)
    with engine.connect() as conn:
        try:
            conn.execute(
                text(
                    "INSERT INTO derivative_projects (owner_id, novel_id, fork_id,"
                    " project_key, name, status, space, fork_key, source_version_key,"
                    " source_snapshot_hash, through_chapter, full_book_authorized,"
                    " cutoff_snapshot_hash, scope_hash, manifest_hash)"
                    " VALUES (:owner_id, :novel_id, 999999991, 'pk', 'n', 'active',"
                    " 'fanfiction_canon', 'fk', 'v', :h, 1, false, :h, :h, :h)"
                ),
                {
                    "owner_id": ids["owner_id"],
                    "novel_id": ids["novel_id"],
                    "h": HEX64,
                },
            )
        except IntegrityError as exc:
            assert "derivative_projects_fork_id_fkey" in str(exc)
        else:
            pytest.fail("project row with an unknown fork_id must violate the FK")
        finally:
            conn.rollback()
    engine.dispose()
