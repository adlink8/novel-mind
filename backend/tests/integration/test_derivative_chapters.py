"""Phase 36-02 derivative chapter plan PostgreSQL API tests (REQ-FORK-02/REQ-CRE-03).

Covers the full owner-scoped chapter plan surface on the real CI database:

- create appends at the end of the plan; list is stable (position, then id);
- Markdown is canonicalized server-side and the checksum is deterministic;
- patch bumps ``revision`` only on a real Markdown change; a stale
  ``base_revision`` returns 409 with the current revision/checksum;
- illegal patch fields (owner/project/revision/checksum/space) are rejected
  (422, extra="forbid") and empty patch payloads fail closed;
- reorder requires the exact full set (missing/extras/duplicates/foreign 409);
- delete hard-removes and archived projects block chapter writes;
- wrong owner/project/chapter is an identical 404 and the DB FK rejects a
  chapter referencing a missing project.
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
CHAPTER_BASE = "/api/novels/{novel_id}/derivative-projects/{project_id}/chapters"
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
            username=f"dc_{suffix}",
            email=f"dc_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
            is_superuser=False,
        )
        session.add(user)
        session.flush()
        novel = Novel(
            title=f"DC Novel {suffix}",
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


async def _create_chapter(client, headers, novel_id, project_id, title, **extra) -> dict:
    payload = {"title": title}
    payload.update(extra)
    resp = await client.post(
        CHAPTER_BASE.format(novel_id=novel_id, project_id=project_id),
        json=payload,
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["chapter"]


def _chapter_url(novel_id: int, project_id: int) -> str:
    return CHAPTER_BASE.format(novel_id=novel_id, project_id=project_id)


# ---------------------------------------------------------------------------
# Ordered plan: create appends / list is stable / scope echoed
# ---------------------------------------------------------------------------


async def test_chapter_plan_round_trip(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"plan_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-plan")
    project = await _create_project(client, headers, novel_id, fork["id"], "Plan")

    base = _chapter_url(novel_id, project["id"])
    c1 = await _create_chapter(client, headers, novel_id, project["id"], "Chapter One")
    c2 = await _create_chapter(
        client, headers, novel_id, project["id"], "Chapter Two", markdown="body"
    )
    assert c1["position"] == 0
    assert c2["position"] == 1
    assert c1["revision"] == 1
    assert c2["status"] == "draft"

    # List is stable and echoes the fork/version/cutoff scope.
    listing = await client.get(base, headers=headers)
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 2
    assert [item["id"] for item in body["items"]] == [c1["id"], c2["id"]]
    scope = body["scope"]
    assert scope["project_id"] == project["id"]
    assert scope["fork_id"] == fork["id"]
    assert scope["space"] == "fanfiction_canon"
    assert scope["fork_key"] == fork["fork_key"]
    assert scope["source_version_key"] == fork["source_version_key"]
    assert scope["cutoff_snapshot_hash"] == fork["cutoff_snapshot_hash"]
    assert scope["through_chapter"] == fork["through_chapter"]

    # Detail returns the canonicalized content + sealed checksum.
    detail = await client.get(f"{base}/{c2['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["markdown"] == "body"
    assert len(detail.json()["markdown_checksum"]) == 64


async def test_markdown_is_canonicalized_before_storage(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"canon_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-canon")
    project = await _create_project(client, headers, novel_id, fork["id"], "Canon")

    raw = "# Title\r\nBody line  \r\n\r\n\r\n"
    chapter = await _create_chapter(
        client, headers, novel_id, project["id"], "T", markdown=raw
    )
    assert chapter["markdown"] == "# Title\nBody line"

    # Same logical content through the patch produces the identical checksum.
    patch = await client.patch(
        f"{_chapter_url(novel_id, project['id'])}/{chapter['id']}",
        json={"markdown": "# Title\nBody line", "base_revision": 1},
        headers=headers,
    )
    assert patch.status_code == 200
    assert patch.json()["markdown_checksum"] == chapter["markdown_checksum"]
    # No-op canonical content did not bump the revision.
    assert patch.json()["revision"] == 1


# ---------------------------------------------------------------------------
# Optimistic concurrency: stale base_revision fails closed
# ---------------------------------------------------------------------------


async def test_stale_base_revision_returns_409_with_current_state(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"rev_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-rev")
    project = await _create_project(client, headers, novel_id, fork["id"], "Rev")
    chapter = await _create_chapter(client, headers, novel_id, project["id"], "T")

    # First writer advances the chapter.
    first = await client.patch(
        f"{_chapter_url(novel_id, project['id'])}/{chapter['id']}",
        json={"markdown": "newer content", "base_revision": 1},
        headers=headers,
    )
    assert first.status_code == 200
    assert first.json()["revision"] == 2

    # A stale writer (still on base_revision 1) is rejected without data loss.
    stale = await client.patch(
        f"{_chapter_url(novel_id, project['id'])}/{chapter['id']}",
        json={"markdown": "stale overwrite", "base_revision": 1},
        headers=headers,
    )
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert "revision_conflict" in detail
    assert "revision 2" in detail
    assert first.json()["markdown_checksum"] in detail

    # The server content is untouched by the stale write.
    detail_resp = await client.get(
        f"{_chapter_url(novel_id, project['id'])}/{chapter['id']}", headers=headers
    )
    assert detail_resp.json()["markdown"] == "newer content"
    assert detail_resp.json()["revision"] == 2


async def test_title_patch_does_not_bump_revision(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"tt_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-tt")
    project = await _create_project(client, headers, novel_id, fork["id"], "TT")
    chapter = await _create_chapter(client, headers, novel_id, project["id"], "Old")

    patched = await client.patch(
        f"{_chapter_url(novel_id, project['id'])}/{chapter['id']}",
        json={"title": "Renamed", "base_revision": 1},
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "Renamed"
    assert patched.json()["revision"] == 1  # title-only change is not a content bump


# ---------------------------------------------------------------------------
# Strict DTO: illegal/authority fields and empty patches are rejected
# ---------------------------------------------------------------------------


async def test_illegal_patch_fields_are_rejected(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"forbid_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-forbid")
    project = await _create_project(client, headers, novel_id, fork["id"], "Forbid")
    chapter = await _create_chapter(client, headers, novel_id, project["id"], "T")
    url = f"{_chapter_url(novel_id, project['id'])}/{chapter['id']}"

    for illegal in (
        {"owner_id": 1, "base_revision": 1},
        {"novel_id": 1, "base_revision": 1},
        {"project_id": 1, "base_revision": 1},
        {"revision": 99, "base_revision": 1},
        {"markdown_checksum": "x" * 64, "base_revision": 1},
        {"space": "original_canon", "base_revision": 1},
        {"position": 5, "base_revision": 1},
    ):
        resp = await client.patch(url, json=illegal, headers=headers)
        assert resp.status_code == 422, f"{illegal}: {resp.text}"

    # Missing base_revision and empty patch fail closed.
    assert (await client.patch(url, json={"markdown": "x"}, headers=headers)).status_code == 422
    assert (await client.patch(url, json={"base_revision": 1}, headers=headers)).status_code == 422


async def test_create_rejects_authority_fields(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"cf_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-cf")
    project = await _create_project(client, headers, novel_id, fork["id"], "CF")
    base = _chapter_url(novel_id, project["id"])

    resp = await client.post(
        base, json={"title": "T", "revision": 5}, headers=headers
    )
    assert resp.status_code == 422, resp.text
    resp = await client.post(
        base, json={"title": "T", "markdown_checksum": "x" * 64}, headers=headers
    )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Reorder: full-set permutation required; conflicts fail closed
# ---------------------------------------------------------------------------


async def test_reorder_applies_stable_positions(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"ord_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-ord")
    project = await _create_project(client, headers, novel_id, fork["id"], "Ord")
    c1 = await _create_chapter(client, headers, novel_id, project["id"], "A")
    c2 = await _create_chapter(client, headers, novel_id, project["id"], "B")
    c3 = await _create_chapter(client, headers, novel_id, project["id"], "C")

    resp = await client.put(
        f"{_chapter_url(novel_id, project['id'])}/order",
        json={"chapter_ids": [c3["id"], c1["id"], c2["id"]]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [item["id"] for item in body["items"]] == [c3["id"], c1["id"], c2["id"]]
    assert [item["position"] for item in body["items"]] == [0, 1, 2]

    # The new order is stable on the next list.
    listing = await client.get(_chapter_url(novel_id, project["id"]), headers=headers)
    assert [item["id"] for item in listing.json()["items"]] == [c3["id"], c1["id"], c2["id"]]


async def test_reorder_conflicts_fail_closed(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"oc_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-oc")
    project = await _create_project(client, headers, novel_id, fork["id"], "OC")
    c1 = await _create_chapter(client, headers, novel_id, project["id"], "A")
    c2 = await _create_chapter(client, headers, novel_id, project["id"], "B")
    url = f"{_chapter_url(novel_id, project['id'])}/order"

    # Missing one chapter.
    resp = await client.put(url, json={"chapter_ids": [c1["id"]]}, headers=headers)
    assert resp.status_code == 409 and "reorder_mismatch" in resp.json()["detail"]

    # Duplicate id.
    resp = await client.put(
        url, json={"chapter_ids": [c1["id"], c1["id"]]}, headers=headers
    )
    assert resp.status_code == 409 and "reorder_duplicate" in resp.json()["detail"]

    # Foreign chapter id.
    resp = await client.put(
        url, json={"chapter_ids": [c1["id"], 999999991]}, headers=headers
    )
    assert resp.status_code == 409 and "reorder_foreign_chapter" in resp.json()["detail"]

    # Empty list.
    resp = await client.put(url, json={"chapter_ids": []}, headers=headers)
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Delete + archived project write gate
# ---------------------------------------------------------------------------


async def test_delete_removes_chapter(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"del_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-del")
    project = await _create_project(client, headers, novel_id, fork["id"], "Del")
    chapter = await _create_chapter(client, headers, novel_id, project["id"], "T")
    url = f"{_chapter_url(novel_id, project['id'])}/{chapter['id']}"

    resp = await client.delete(url, headers=headers)
    assert resp.status_code == 204
    assert (await client.get(url, headers=headers)).status_code == 404


async def test_archived_project_blocks_chapter_writes(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"arch_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-arch")
    project = await _create_project(client, headers, novel_id, fork["id"], "Arch")
    chapter = await _create_chapter(client, headers, novel_id, project["id"], "T")

    # Archive the project.
    pid = project["id"]
    resp = await client.patch(
        PROJECT_BASE.format(novel_id=novel_id) + f"/{pid}",
        json={"status": "archived"},
        headers=headers,
    )
    assert resp.status_code == 200

    base = _chapter_url(novel_id, pid)
    create = await client.post(base, json={"title": "New"}, headers=headers)
    assert create.status_code == 409 and "project_archived" in create.json()["detail"]
    patch = await client.patch(
        f"{base}/{chapter['id']}",
        json={"markdown": "x", "base_revision": 1},
        headers=headers,
    )
    assert patch.status_code == 409 and "project_archived" in patch.json()["detail"]

    # Reads still work.
    listing = await client.get(base, headers=headers)
    assert listing.status_code == 200
    assert listing.json()["total"] == 1


# ---------------------------------------------------------------------------
# Owner isolation + DB-level FK gate
# ---------------------------------------------------------------------------


async def test_foreign_owner_project_chapter_are_identical_404(api_client):
    client, _, sync_url = api_client
    a = _seed_owner(sync_url, suffix=f"oa_{uuid.uuid4().hex[:8]}")
    b = _seed_owner(sync_url, suffix=f"ob_{uuid.uuid4().hex[:8]}")
    headers_a = {"Authorization": f"Bearer {a['token']}"}
    headers_b = {"Authorization": f"Bearer {b['token']}"}
    fork_a = await _create_fork(client, headers_a, a["novel_id"], "ff-oa")
    project_a = await _create_project(client, headers_a, a["novel_id"], fork_a["id"], "A")
    chapter_a = await _create_chapter(client, headers_a, a["novel_id"], project_a["id"], "A1")

    # Owner B cannot see or touch owner A's project chapters.
    base_b = _chapter_url(b["novel_id"], project_a["id"])
    listing = await client.get(base_b, headers=headers_b)
    assert listing.status_code == 404
    create = await client.post(base_b, json={"title": "X"}, headers=headers_b)
    assert create.status_code == 404

    # A foreign chapter id inside the wrong project is also an identical 404.
    wrong = await client.get(
        f"{_chapter_url(a['novel_id'], project_a['id'])}/{chapter_a['id'] + 999999991}",
        headers=headers_a,
    )
    assert wrong.status_code == 404


async def test_database_fk_rejects_unknown_project_id(api_client):
    """The chapter FK is enforced at the database level (T-36-02-01)."""
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"fk_{uuid.uuid4().hex[:8]}")
    engine = create_engine(sync_url, poolclass=NullPool)
    with engine.connect() as conn:
        try:
            conn.execute(
                text(
                    "INSERT INTO derivative_chapters (owner_id, novel_id, project_id,"
                    " position, title, markdown, markdown_checksum, status, revision)"
                    " VALUES (:owner_id, :novel_id, 999999991, 0, 't', '', :h,"
                    " 'draft', 1)"
                ),
                {"owner_id": ids["owner_id"], "novel_id": ids["novel_id"], "h": "a" * 64},
            )
        except IntegrityError as exc:
            assert "derivative_chapters_project_id_fkey" in str(exc)
        else:
            pytest.fail("chapter row with an unknown project_id must violate the FK")
        finally:
            conn.rollback()
    engine.dispose()


async def test_database_rejects_bad_checksum_and_status(api_client):
    """Chapter checksum length / status / position / revision DB gates."""
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"ck_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-ck")
    project = await _create_project(client, headers, novel_id, fork["id"], "CK")

    engine = create_engine(sync_url, poolclass=NullPool)
    base_values = {
        "owner_id": ids["owner_id"],
        "novel_id": novel_id,
        "project_id": project["id"],
    }
    with engine.connect() as conn:
        # Non-64 checksum.
        try:
            conn.execute(
                text(
                    "INSERT INTO derivative_chapters (owner_id, novel_id, project_id,"
                    " position, title, markdown, markdown_checksum, status, revision)"
                    " VALUES (:owner_id, :novel_id, :project_id, 0, 't', '', 'short',"
                    " 'draft', 1)"
                ),
                base_values,
            )
        except IntegrityError as exc:
            assert "ck_derivative_chapters_checksum" in str(exc)
        else:
            pytest.fail("short checksum must be rejected by the DB constraint")
        finally:
            conn.rollback()

        # Illegal status.
        try:
            conn.execute(
                text(
                    "INSERT INTO derivative_chapters (owner_id, novel_id, project_id,"
                    " position, title, markdown, markdown_checksum, status, revision)"
                    " VALUES (:owner_id, :novel_id, :project_id, 1, 't', '', :h,"
                    " 'published', 1)"
                ),
                {**base_values, "h": "a" * 64},
            )
        except IntegrityError as exc:
            assert "ck_derivative_chapters_status" in str(exc)
        else:
            pytest.fail("published chapter status must be rejected by the DB constraint")
        finally:
            conn.rollback()

        # Non-positive revision.
        try:
            conn.execute(
                text(
                    "INSERT INTO derivative_chapters (owner_id, novel_id, project_id,"
                    " position, title, markdown, markdown_checksum, status, revision)"
                    " VALUES (:owner_id, :novel_id, :project_id, 2, 't', '', :h,"
                    " 'draft', 0)"
                ),
                {**base_values, "h": "a" * 64},
            )
        except IntegrityError as exc:
            assert "ck_derivative_chapters_revision" in str(exc)
        else:
            pytest.fail("zero revision must be rejected by the DB constraint")
        finally:
            conn.rollback()
    engine.dispose()
