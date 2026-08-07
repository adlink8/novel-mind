"""Phase 36-01 Wave0 editor fixtures and isolation contract (REQ-FORK-02/CRE-03).

Wave0 declares the editor fixture matrix up front, before the revision/autosave
machinery of 36-02/03:

- two owners × two projects × two forks: owner A owns novel A + fork A1 (project
  P1); owner B owns novel B + fork B1 (project P2). Owner A additionally seals a
  second fork A2 and a second project bound to it to prove the client must
  choose the fork explicitly (D-36-01).
- stale-base / crash-before-ack / rollback fixtures: the durable project root is
  the data dependency for those revision tests. 36-01 proves crash-before-ack
  durability at the project level (the row is committed even though the client
  never saw the response); stale-base and rollback behavior tests belong to the
  36-02/36-03 revision plans and reuse this dataset.

Isolation gates proven here on the real CI database:
- a project **cannot exist without an explicit fork** (422 without fork_id);
- the fork must be inside the owner/novel scope (foreign fork -> identical 404);
- all responses echo the frozen fork scope + version lineage;
- the wire cannot inject owner/novel/space/status (extra="forbid");
- the schema migration replays (upgrade -> downgrade -> upgrade round trip).
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
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
    """ASGI client bound to module-migrated PostgreSQL (head incl. 36-01)."""
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


def _seed_owner(sync_url: str, *, suffix: str, chapter_count: int = 3) -> dict:
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        user = User(
            username=f"ed_{suffix}",
            email=f"ed_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
            is_superuser=False,
        )
        session.add(user)
        session.flush()
        novel = Novel(
            title=f"ED Novel {suffix}",
            owner_id=user.id,
            status="ready",
            reading_progress={"chapter_id": 1, "progress_percent": 10},
            chapter_count=chapter_count,
            word_count=sum(
                len(f"chapter {i} body") for i in range(1, chapter_count + 1)
            ),
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


async def _create_fork(client, headers, novel_id, fork_key, cutoff=None) -> dict:
    body: dict = {"fork_key": fork_key}
    if cutoff is not None:
        body["requested_cutoff_chapter"] = cutoff
    resp = await client.post(
        FORK_BASE.format(novel_id=novel_id), json=body, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["fork"]


# ---------------------------------------------------------------------------
# Wave0 fixture: two owners x two projects x two forks
# ---------------------------------------------------------------------------


@pytest.fixture
async def wave0_editor_fixture(api_client):
    """Dataset for the Wave0 matrix: owner A (fork A1 -> P1), owner B (fork B1 -> P2).

    Owner A also seals fork A2 with a distinct cutoff so the explicit-fork
    selection contract (two forks on one novel) is exercised.
    """
    client, factory, sync_url = api_client
    a = _seed_owner(sync_url, suffix=f"a_{uuid.uuid4().hex[:8]}")
    b = _seed_owner(sync_url, suffix=f"b_{uuid.uuid4().hex[:8]}")
    headers_a = {"Authorization": f"Bearer {a['token']}"}
    headers_b = {"Authorization": f"Bearer {b['token']}"}

    fork_a1 = await _create_fork(client, headers_a, a["novel_id"], "ff-a1")
    fork_a2 = await _create_fork(client, headers_a, a["novel_id"], "ff-a2", cutoff=2)
    fork_b1 = await _create_fork(client, headers_b, b["novel_id"], "ff-b1")

    def _project_payload(fork_id: int, name: str, **extra) -> dict:
        payload = {"fork_id": fork_id, "name": name}
        payload.update(extra)
        return payload

    p1_resp = await client.post(
        PROJECT_BASE.format(novel_id=a["novel_id"]),
        json=_project_payload(fork_a1["id"], "Project A1"),
        headers=headers_a,
    )
    assert p1_resp.status_code == 201, p1_resp.text
    p1 = p1_resp.json()["project"]
    p2_resp = await client.post(
        PROJECT_BASE.format(novel_id=b["novel_id"]),
        json=_project_payload(fork_b1["id"], "Project B1"),
        headers=headers_b,
    )
    assert p2_resp.status_code == 201, p2_resp.text
    p2 = p2_resp.json()["project"]

    return {
        "client": client,
        "factory": factory,
        "a": a,
        "b": b,
        "headers_a": headers_a,
        "headers_b": headers_b,
        "fork_a1": fork_a1,
        "fork_a2": fork_a2,
        "fork_b1": fork_b1,
        "project_a1": p1,
        "project_b1": p2,
    }


# ---------------------------------------------------------------------------
# Explicit fork selection: the project cannot exist without a fork (D-36-01)
# ---------------------------------------------------------------------------


async def test_project_requires_explicit_fork_id(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"nofork_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = PROJECT_BASE.format(novel_id=ids["novel_id"])

    resp = await client.post(base, json={"name": "No Fork"}, headers=headers)
    assert resp.status_code == 422, resp.text
    # The editor can never fall back to an implicit fork from the reading page.
    assert "fork_id" in resp.text


async def test_two_projects_on_two_forks_bind_explicitly(wave0_editor_fixture):
    fx = wave0_editor_fixture
    # Owner A created a second fork with a different cutoff; a project bound to
    # it must carry that fork's lineage, not fork A1's (explicit selection).
    client = fx["client"]
    resp = await client.post(
        PROJECT_BASE.format(novel_id=fx["a"]["novel_id"]),
        json={"fork_id": fx["fork_a2"]["id"], "name": "Project A2 on cutoff fork"},
        headers=fx["headers_a"],
    )
    assert resp.status_code == 201, resp.text
    p = resp.json()["project"]
    assert p["fork_id"] == fx["fork_a2"]["id"]
    assert p["through_chapter"] == 2
    assert p["manifest_hash"] == fx["fork_a2"]["manifest_hash"]
    assert p["scope_hash"] == fx["fork_a2"]["scope_hash"]
    assert p["manifest_hash"] != fx["project_a1"]["manifest_hash"]


# ---------------------------------------------------------------------------
# Frozen scope/version lineage on every response (owner/novel/version/cutoff)
# ---------------------------------------------------------------------------


async def test_project_creation_freezes_scope_version_and_cutoff(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"freeze_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = PROJECT_BASE.format(novel_id=ids["novel_id"])
    fork = await _create_fork(client, headers, ids["novel_id"], "ff-freeze")

    resp = await client.post(
        base, json={"fork_id": fork["id"], "name": "Frozen Project"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    p = resp.json()["project"]

    assert p["owner_id"] == ids["owner_id"]
    assert p["novel_id"] == ids["novel_id"]
    assert p["fork_id"] == fork["id"]
    assert p["space"] == "fanfiction_canon"
    assert p["status"] == "active"
    assert p["fork_key"] == "ff-freeze"
    # Frozen version + cutoff lineage copied from the chosen fork.
    assert p["source_version_key"] == fork["source_version_key"]
    assert p["source_snapshot_hash"] == fork["source_snapshot_hash"]
    assert p["through_chapter"] == fork["through_chapter"]
    assert p["full_book_authorized"] is False
    assert p["cutoff_snapshot_hash"] == fork["cutoff_snapshot_hash"]
    assert p["scope_hash"] == fork["scope_hash"]
    assert p["manifest_hash"] == fork["manifest_hash"]
    assert p["project_key"] == "frozen-project"

    # Detail + list responses carry the same scope/version lineage.
    detail = await client.get(f"{base}/{p['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["manifest_hash"] == fork["manifest_hash"]
    listing = await client.get(base, headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["scope_hash"] == fork["scope_hash"]


async def test_wire_cannot_inject_scope_or_space(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"inj_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = PROJECT_BASE.format(novel_id=ids["novel_id"])
    fork = await _create_fork(client, headers, ids["novel_id"], "ff-inj")

    for forged in (
        {"fork_id": fork["id"], "name": "X", "owner_id": 999},
        {"fork_id": fork["id"], "name": "X", "novel_id": 999},
        {"fork_id": fork["id"], "name": "X", "space": "original_canon"},
        {"fork_id": fork["id"], "name": "X", "status": "approved"},
    ):
        resp = await client.post(base, json=forged, headers=headers)
        assert resp.status_code == 422, (forged, resp.text)


# ---------------------------------------------------------------------------
# Owner isolation: foreign fork / foreign project are identical 404s
# ---------------------------------------------------------------------------


async def test_project_cannot_bind_to_a_foreign_fork(api_client):
    client, _, sync_url = api_client
    a = _seed_owner(sync_url, suffix=f"fa_{uuid.uuid4().hex[:8]}")
    b = _seed_owner(sync_url, suffix=f"fb_{uuid.uuid4().hex[:8]}")
    headers_a = {"Authorization": f"Bearer {a['token']}"}
    headers_b = {"Authorization": f"Bearer {b['token']}"}
    fork_a = await _create_fork(client, headers_a, a["novel_id"], "ff-a")

    # Owner B tries to anchor a project on A's fork.
    resp = await client.post(
        PROJECT_BASE.format(novel_id=b["novel_id"]),
        json={"fork_id": fork_a["id"], "name": "Sneaky"},
        headers=headers_b,
    )
    assert resp.status_code == 404, resp.text
    assert "fork_not_found" in resp.json()["detail"]

    # A's fork under B's novel is also unreachable for listing.
    resp = await client.post(
        PROJECT_BASE.format(novel_id=a["novel_id"]),
        json={"fork_id": fork_a["id"], "name": "Sneaky"},
        headers=headers_b,
    )
    assert resp.status_code == 404, resp.text


async def test_cross_owner_project_routes_return_404(wave0_editor_fixture):
    fx = wave0_editor_fixture
    base_a = PROJECT_BASE.format(novel_id=fx["a"]["novel_id"])
    base_b = PROJECT_BASE.format(novel_id=fx["b"]["novel_id"])
    pid_a = fx["project_a1"]["id"]

    # Owner B probing owner A's project under A's novel: identical 404.
    assert (
        await fx["client"].get(f"{base_a}/{pid_a}", headers=fx["headers_b"])
    ).status_code == 404
    assert (
        await fx["client"].patch(
            f"{base_a}/{pid_a}", json={"name": "Hijack"}, headers=fx["headers_b"]
        )
    ).status_code == 404
    assert (
        await fx["client"].delete(f"{base_a}/{pid_a}", headers=fx["headers_b"])
    ).status_code == 404

    # B asking for A's project_id under B's own novel: identical 404.
    assert (
        await fx["client"].get(f"{base_b}/{pid_a}", headers=fx["headers_b"])
    ).status_code == 404

    # A still reads its own project; B reads its own.
    assert (
        await fx["client"].get(f"{base_a}/{pid_a}", headers=fx["headers_a"])
    ).status_code == 200
    assert (await fx["client"].get(base_b, headers=fx["headers_b"])).json()[
        "total"
    ] == 1


async def test_missing_project_is_404(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"miss_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    base = PROJECT_BASE.format(novel_id=ids["novel_id"])
    resp = await client.get(f"{base}/999999991", headers=headers)
    assert resp.status_code == 404
    assert "project_not_found" in resp.json()["detail"]


async def test_unauthenticated_project_routes_reject(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"anon_{uuid.uuid4().hex[:8]}")
    base = PROJECT_BASE.format(novel_id=ids["novel_id"])
    assert (await client.get(base)).status_code == 401
    assert (
        await client.post(base, json={"fork_id": 1, "name": "x"})
    ).status_code == 401


# ---------------------------------------------------------------------------
# Crash-before-ack durability of the project root
# ---------------------------------------------------------------------------


async def test_project_row_is_durable_after_crash_before_ack(wave0_editor_fixture):
    """The committed project root survives even if the client never saw the ack.

    This is the project-level half of the Wave0 crash-before-ack fixture: the
    row is visible to a fresh session (commit durability), so a lost response
    never loses the project. Revision-level crash-before-ack tests belong to the
    36-02/36-03 autosave plans and reuse this dataset.
    """
    fx = wave0_editor_fixture
    factory = fx["factory"]
    # The wave0 fixture already committed P1/P2; re-read P1 from a fresh session.
    async with factory() as session:
        row = await session.get(DerivativeProject, fx["project_a1"]["id"])
        assert row is not None
        assert row.owner_id == fx["a"]["owner_id"]
        assert row.novel_id == fx["a"]["novel_id"]
        assert row.fork_id == fx["fork_a1"]["id"]
        assert row.space == "fanfiction_canon"
        assert row.status == "active"


# ---------------------------------------------------------------------------
# Schema migration replay (upgrade -> downgrade -> upgrade)
# ---------------------------------------------------------------------------


async def test_derivative_project_migration_replays(pg_sync_url, require_postgres):
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "head", database_url=pg_sync_url)

    engine = create_engine(pg_sync_url, poolclass=NullPool)
    with engine.connect() as conn:
        insp = sa.inspect(conn)
        assert "derivative_projects" in insp.get_table_names()
        fks = {
            (tuple(f["constrained_columns"]), f["referred_table"])
            for f in insp.get_foreign_keys("derivative_projects")
        }
        assert (("fork_id",), "canon_forks") in fks
        assert (("owner_id",), "users") in fks
        assert (("novel_id",), "novels") in fks
        uniques = {
            c["name"] for c in insp.get_unique_constraints("derivative_projects")
        }
        assert "uq_derivative_projects_key" in uniques
        checks = {c["name"] for c in insp.get_check_constraints("derivative_projects")}
        assert "ck_derivative_projects_space" in checks
        assert "ck_derivative_projects_status" in checks
    engine.dispose()

    # Downgrade to the pre-36-01 revision drops the table.
    run_alembic("downgrade", "20260801_canon_contamination04", database_url=pg_sync_url)
    engine = create_engine(pg_sync_url, poolclass=NullPool)
    with engine.connect() as conn:
        assert "derivative_projects" not in sa.inspect(conn).get_table_names()
    engine.dispose()

    # Re-upgrade restores the table (replayable migration).
    run_alembic("upgrade", "head", database_url=pg_sync_url)
    engine = create_engine(pg_sync_url, poolclass=NullPool)
    with engine.connect() as conn:
        assert "derivative_projects" in sa.inspect(conn).get_table_names()
    engine.dispose()
