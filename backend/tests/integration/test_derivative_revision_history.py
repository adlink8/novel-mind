"""Phase 36-03 derivative revision/autosave/diff/rollback PostgreSQL API tests.

REQ-FORK-02 / REQ-CRE-04 / D-36-02. Covers the full owner-scoped append-only
lineage surface on the real CI database:

- chapter creation seeds the immutable root revision (kind=create);
- autosave appends immutable rows and bumps the chapter revision; duplicate /
  crash-retry autosaves resolve idempotently (noop, no new row);
- a stale/conflicting ``base_revision`` returns 409 carrying the latest
  revision and the newer content is never overwritten (no last-write-wins);
- concurrent autosaves against the same base never last-write-win;
- diff is deterministic and based on canonical Markdown (CRLF / trailing
  whitespace normalization is invisible);
- rollback creates a NEW child revision, restores the target content, keeps
  every historical row intact, and subsequent edits continue from the rollback;
- owner isolation: a foreign owner/novel/project/chapter/revision is an
  identical 404 on every revision route;
- archived projects block autosave/rollback while reads keep working;
- crash-before-ack durability: a committed autosave survives a lost response;
- the schema migration replays (upgrade -> downgrade -> upgrade round trip) and
  the database enforces the FK / checksum / kind / approval / uniqueness gates.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.derivative_revision import DerivativeRevision
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
    """ASGI client bound to module-migrated PostgreSQL (head incl. 36-03)."""
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
            username=f"rv_{suffix}",
            email=f"rv_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
            is_superuser=False,
        )
        session.add(user)
        session.flush()
        novel = Novel(
            title=f"RV Novel {suffix}",
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


def _chapter_url(novel_id: int, project_id: int, chapter_id: int) -> str:
    return (
        CHAPTER_BASE.format(novel_id=novel_id, project_id=project_id)
        + f"/{chapter_id}"
    )


async def _autosave(client, headers, novel_id, project_id, chapter_id, content, base):
    return await client.post(
        f"{_chapter_url(novel_id, project_id, chapter_id)}/autosave",
        json={"content": content, "base_revision": base},
        headers=headers,
    )


async def _rollback(client, headers, novel_id, project_id, chapter_id, target, base, **extra):
    payload = {"target_revision_id": target, "base_revision": base}
    payload.update(extra)
    return await client.post(
        f"{_chapter_url(novel_id, project_id, chapter_id)}/rollback",
        json=payload,
        headers=headers,
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Root revision seeding + append-only autosave lineage
# ---------------------------------------------------------------------------


async def test_create_seeds_root_revision(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"root_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-root")
    project = await _create_project(client, headers, novel_id, fork["id"], "Root")
    chapter = await _create_chapter(
        client, headers, novel_id, project["id"], "T", markdown="# Hello"
    )

    history = await client.get(
        f"{_chapter_url(novel_id, project['id'], chapter['id'])}/revisions",
        headers=headers,
    )
    assert history.status_code == 200, history.text
    body = history.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["kind"] == "create"
    assert item["revision_number"] == 1
    assert item["parent_revision_id"] is None
    assert item["content_checksum"] == _sha256("# Hello")

    # The root row holds the initial canonical Markdown snapshot.
    detail = await client.get(
        f"{_chapter_url(novel_id, project['id'], chapter['id'])}/revisions/{item['id']}",
        headers=headers,
    )
    assert detail.status_code == 200
    assert detail.json()["content"] == "# Hello"
    assert detail.json()["actor_id"] == ids["owner_id"]
    assert detail.json()["approval_state"] == "not_required"


async def test_autosave_appends_immutable_lineage(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"line_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-line")
    project = await _create_project(client, headers, novel_id, fork["id"], "Line")
    chapter = await _create_chapter(client, headers, novel_id, project["id"], "T")
    url = _chapter_url(novel_id, project["id"], chapter["id"])

    first = await _autosave(client, headers, novel_id, project["id"], chapter["id"], "A", 1)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "saved"
    assert first.json()["chapter"]["revision"] == 2
    assert first.json()["revision"]["revision_number"] == 2
    assert first.json()["revision"]["kind"] == "autosave"

    second = await _autosave(client, headers, novel_id, project["id"], chapter["id"], "B", 2)
    assert second.status_code == 200, second.text
    assert second.json()["chapter"]["revision"] == 3
    assert second.json()["revision"]["revision_number"] == 3

    history = await client.get(f"{url}/revisions", headers=headers)
    body = history.json()
    assert body["total"] == 3
    numbers = [item["revision_number"] for item in body["items"]]
    assert numbers == [3, 2, 1]
    parents = [item["parent_revision_id"] for item in body["items"]]
    assert parents[0] == body["items"][1]["id"]  # 3 -> 2
    assert parents[1] == body["items"][2]["id"]  # 2 -> 1
    assert parents[2] is None  # root

    # Every revision's content is preserved for audit.
    third_detail = await client.get(f"{url}/revisions/{body['items'][0]['id']}", headers=headers)
    assert third_detail.json()["content"] == "B"


async def test_autosave_noop_is_idempotent(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"noop_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-noop")
    project = await _create_project(client, headers, novel_id, fork["id"], "Noop")
    chapter = await _create_chapter(client, headers, novel_id, project["id"], "T")

    first = await _autosave(client, headers, novel_id, project["id"], chapter["id"], "A", 1)
    assert first.status_code == 200 and first.json()["status"] == "saved"

    # Saving the exact same content again is an idempotent no-op.
    dup = await _autosave(client, headers, novel_id, project["id"], chapter["id"], "A", 2)
    assert dup.status_code == 200, dup.text
    assert dup.json()["status"] == "noop"
    assert dup.json()["chapter"]["revision"] == 2  # no bump

    history = await client.get(
        f"{_chapter_url(novel_id, project['id'], chapter['id'])}/revisions", headers=headers
    )
    assert history.json()["total"] == 2  # root + first autosave only


# ---------------------------------------------------------------------------
# Optimistic concurrency: stale/conflicting writes never last-write-win
# ---------------------------------------------------------------------------


async def test_stale_base_conflict_returns_409_with_latest_revision(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"stale_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-stale")
    project = await _create_project(client, headers, novel_id, fork["id"], "Stale")
    chapter = await _create_chapter(client, headers, novel_id, project["id"], "T")

    first = await _autosave(client, headers, novel_id, project["id"], chapter["id"], "A", 1)
    assert first.status_code == 200 and first.json()["status"] == "saved"

    # A stale writer (still on base_revision 1) is rejected with the head state.
    stale = await _autosave(client, headers, novel_id, project["id"], chapter["id"], "B", 1)
    assert stale.status_code == 409, stale.text
    detail = stale.json()["detail"]
    assert detail["code"] == "revision_conflict"
    assert "stale write" in detail["message"]
    assert detail["current_revision_number"] == 2
    assert detail["current_checksum"] == _sha256("A")
    assert detail["current_revision"]["content"] == "A"
    assert detail["current_revision"]["kind"] == "autosave"

    # The newer content is untouched by the stale write.
    detail_resp = await client.get(
        _chapter_url(novel_id, project["id"], chapter["id"]), headers=headers
    )
    assert detail_resp.json()["markdown"] == "A"
    assert detail_resp.json()["revision"] == 2


async def test_crash_retry_replay_is_idempotent(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"retry_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-retry")
    project = await _create_project(client, headers, novel_id, fork["id"], "Retry")
    chapter = await _create_chapter(client, headers, novel_id, project["id"], "T")

    saved = await _autosave(client, headers, novel_id, project["id"], chapter["id"], "A", 1)
    assert saved.status_code == 200 and saved.json()["status"] == "saved"
    revision_id = saved.json()["revision"]["id"]

    # Crash-before-ack retry: the client replays the identical request (same
    # base + same content). It must resolve idempotently, not as a new row.
    replayed = await _autosave(client, headers, novel_id, project["id"], chapter["id"], "A", 1)
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["status"] == "noop"
    assert replayed.json()["revision"]["id"] == revision_id

    history = await client.get(
        f"{_chapter_url(novel_id, project['id'], chapter['id'])}/revisions", headers=headers
    )
    assert history.json()["total"] == 2  # root + one autosave row only


async def test_concurrent_autosaves_never_last_write_win(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"conc_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-conc")
    project = await _create_project(client, headers, novel_id, fork["id"], "Conc")
    chapter = await _create_chapter(client, headers, novel_id, project["id"], "T")

    url = _chapter_url(novel_id, project["id"], chapter["id"])
    r1 = _autosave(client, headers, novel_id, project["id"], chapter["id"], "X", 1)
    r2 = _autosave(client, headers, novel_id, project["id"], chapter["id"], "Y", 1)
    results = await asyncio.gather(r1, r2)

    statuses = sorted(result.status_code for result in results)
    assert statuses == [200, 409], [r.text for r in results]
    winner = next(r for r in results if r.status_code == 200)
    loser = next(r for r in results if r.status_code == 409)
    assert winner.json()["status"] == "saved"
    assert loser.json()["detail"]["code"] == "revision_conflict"
    assert loser.json()["detail"]["current_revision"]["content"] == winner.json()["revision"]["content"]

    # Exactly one autosave row exists and the head holds the winner's content.
    history = await client.get(f"{url}/revisions", headers=headers)
    assert history.json()["total"] == 2
    head = await client.get(url, headers=headers)
    assert head.json()["markdown"] == winner.json()["revision"]["content"]
    assert head.json()["revision"] == 2


# ---------------------------------------------------------------------------
# Deterministic canonical-Markdown diff
# ---------------------------------------------------------------------------


async def test_diff_between_revisions_is_deterministic(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"diff_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-diff")
    project = await _create_project(client, headers, novel_id, fork["id"], "Diff")
    chapter = await _create_chapter(
        client, headers, novel_id, project["id"], "T", markdown="a\nb\nc"
    )
    url = _chapter_url(novel_id, project["id"], chapter["id"])

    history = await client.get(f"{url}/revisions", headers=headers)
    root_id = history.json()["items"][0]["id"]

    replaced = await _autosave(client, headers, novel_id, project["id"], chapter["id"], "a\nB\nc", 1)
    assert replaced.status_code == 200
    replaced_id = replaced.json()["revision"]["id"]

    resp = await client.get(
        f"{url}/diff",
        params={"base_revision_id": root_id, "target_revision_id": replaced_id},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["base_revision_number"] == 1
    assert body["target_revision_number"] == 2
    assert body["additions"] == 1
    assert body["deletions"] == 1
    ops = [(line["op"], line["text"]) for hunk in body["hunks"] for line in hunk["lines"]]
    assert ("delete", "b") in ops
    assert ("add", "B") in ops

    # Deterministic: the same two rows always produce the identical diff.
    again = await client.get(
        f"{url}/diff",
        params={"base_revision_id": root_id, "target_revision_id": replaced_id},
        headers=headers,
    )
    assert again.json() == body

    # Append-only boundary: adding a line is pure insertion.
    appended = await _autosave(
        client, headers, novel_id, project["id"], chapter["id"], "a\nB\nc\nd", 2
    )
    appended_id = appended.json()["revision"]["id"]
    app_resp = await client.get(
        f"{url}/diff",
        params={"base_revision_id": replaced_id, "target_revision_id": appended_id},
        headers=headers,
    )
    app_body = app_resp.json()
    assert app_body["additions"] == 1
    assert app_body["deletions"] == 0
    app_ops = [
        (line["op"], line["text"])
        for hunk in app_body["hunks"]
        for line in hunk["lines"]
    ]
    assert ("add", "d") in app_ops


async def test_diff_identical_and_canonicalized_content_is_empty(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"dc_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-dc")
    project = await _create_project(client, headers, novel_id, fork["id"], "DC")
    chapter = await _create_chapter(
        client, headers, novel_id, project["id"], "T", markdown="# T\r\nBody  "
    )
    url = _chapter_url(novel_id, project["id"], chapter["id"])

    history = await client.get(f"{url}/revisions", headers=headers)
    root_id = history.json()["items"][0]["id"]

    # Identical rows -> zero hunks.
    same = await client.get(
        f"{url}/diff",
        params={"base_revision_id": root_id, "target_revision_id": root_id},
        headers=headers,
    )
    assert same.json()["additions"] == 0
    assert same.json()["deletions"] == 0
    assert same.json()["hunks"] == []

    # Canonicalization makes CRLF/whitespace-only differences invisible: saving
    # the normalized form is a noop, and the stored snapshot equals the logical
    # content, so the diff against a re-canonicalized string is empty.
    changed = await _autosave(
        client, headers, novel_id, project["id"], chapter["id"], "# T\nBody\nMore", 1
    )
    assert changed.status_code == 200 and changed.json()["status"] == "saved"
    changed_id = changed.json()["revision"]["id"]

    crlf_again = await _autosave(
        client, headers, novel_id, project["id"], chapter["id"], "# T\r\nBody\r\nMore", 2
    )
    assert crlf_again.status_code == 200 and crlf_again.json()["status"] == "noop"

    normalized = await client.get(
        f"{url}/diff",
        params={"base_revision_id": changed_id, "target_revision_id": changed_id},
        headers=headers,
    )
    assert normalized.json()["hunks"] == []


# ---------------------------------------------------------------------------
# Rollback: a new child revision, never an in-place history rewrite
# ---------------------------------------------------------------------------


async def test_rollback_creates_new_child_and_preserves_history(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"rb_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-rb")
    project = await _create_project(client, headers, novel_id, fork["id"], "RB")
    chapter = await _create_chapter(client, headers, novel_id, project["id"], "T")
    url = _chapter_url(novel_id, project["id"], chapter["id"])

    await _autosave(client, headers, novel_id, project["id"], chapter["id"], "A", 1)
    await _autosave(client, headers, novel_id, project["id"], chapter["id"], "B", 2)
    history = await client.get(f"{url}/revisions", headers=headers)
    items = history.json()["items"]  # newest first: [3(B), 2(A), 1(create)]
    rev_a_id = items[1]["id"]  # revision 2 holds "A"

    rollback = await _rollback(
        client, headers, novel_id, project["id"], chapter["id"], rev_a_id, 3,
        reason="go back to A",
    )
    assert rollback.status_code == 200, rollback.text
    body = rollback.json()
    assert body["chapter"]["markdown"] == "A"
    assert body["chapter"]["revision"] == 4
    assert body["revision"]["kind"] == "rollback"
    assert body["revision"]["revision_number"] == 4
    assert body["revision"]["content"] == "A"
    assert body["revision"]["reason"] == "go back to A"
    assert body["revision"]["approval_state"] == "approved"
    assert body["revision"]["parent_revision_id"] == items[0]["id"]  # child of rev 3

    # History is append-only: all four rows remain and no row was rewritten.
    after = await client.get(f"{url}/revisions", headers=headers)
    after_items = after.json()["items"]
    assert after.json()["total"] == 4
    assert [item["revision_number"] for item in after_items] == [4, 3, 2, 1]
    # The historical "B" row still holds its original content.
    rev_b_detail = await client.get(f"{url}/revisions/{items[0]['id']}", headers=headers)
    assert rev_b_detail.json()["content"] == "B"


async def test_rollback_requires_current_base_revision(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"rbv_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-rbv")
    project = await _create_project(client, headers, novel_id, fork["id"], "RBV")
    chapter = await _create_chapter(client, headers, novel_id, project["id"], "T")
    url = _chapter_url(novel_id, project["id"], chapter["id"])

    await _autosave(client, headers, novel_id, project["id"], chapter["id"], "A", 1)
    history = await client.get(f"{url}/revisions", headers=headers)
    root_id = history.json()["items"][1]["id"]  # create rev 1

    # The chapter is at revision 2; a rollback carrying base_revision 1 is stale.
    stale = await _rollback(client, headers, novel_id, project["id"], chapter["id"], root_id, 1)
    assert stale.status_code == 409, stale.text
    detail = stale.json()["detail"]
    assert detail["code"] == "revision_conflict"
    assert detail["current_revision_number"] == 2
    # The head content was not clobbered by the stale rollback.
    head = await client.get(url, headers=headers)
    assert head.json()["markdown"] == "A"
    assert head.json()["revision"] == 2


async def test_rollback_foreign_target_is_404(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"rbf_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-rbf")
    project = await _create_project(client, headers, novel_id, fork["id"], "RBF")
    chapter = await _create_chapter(client, headers, novel_id, project["id"], "T")

    resp = await _rollback(
        client, headers, novel_id, project["id"], chapter["id"], 999999991, 1
    )
    assert resp.status_code == 404, resp.text
    assert "revision_not_found" in resp.json()["detail"]["code"]


async def test_rollback_then_continue_editing(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"rbc_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-rbc")
    project = await _create_project(client, headers, novel_id, fork["id"], "RBC")
    chapter = await _create_chapter(client, headers, novel_id, project["id"], "T")
    url = _chapter_url(novel_id, project["id"], chapter["id"])

    await _autosave(client, headers, novel_id, project["id"], chapter["id"], "A", 1)
    await _autosave(client, headers, novel_id, project["id"], chapter["id"], "B", 2)
    history = await client.get(f"{url}/revisions", headers=headers)
    rev_a_id = history.json()["items"][1]["id"]

    rollback = await _rollback(client, headers, novel_id, project["id"], chapter["id"], rev_a_id, 3)
    assert rollback.status_code == 200
    assert rollback.json()["chapter"]["revision"] == 4

    # Editing continues from the rollback head with the new base token.
    cont = await _autosave(client, headers, novel_id, project["id"], chapter["id"], "C", 4)
    assert cont.status_code == 200, cont.text
    assert cont.json()["status"] == "saved"
    assert cont.json()["chapter"]["revision"] == 5
    assert cont.json()["revision"]["parent_revision_id"] == rollback.json()["revision"]["id"]

    history = await client.get(f"{url}/revisions", headers=headers)
    assert history.json()["total"] == 5
    assert history.json()["items"][0]["kind"] == "autosave"
    assert history.json()["items"][1]["kind"] == "rollback"


# ---------------------------------------------------------------------------
# Owner isolation: a foreign scope is an identical 404 on every route
# ---------------------------------------------------------------------------


async def test_foreign_owner_cannot_touch_revision_surface(api_client):
    client, _, sync_url = api_client
    a = _seed_owner(sync_url, suffix=f"oa_{uuid.uuid4().hex[:8]}")
    b = _seed_owner(sync_url, suffix=f"ob_{uuid.uuid4().hex[:8]}")
    headers_a = {"Authorization": f"Bearer {a['token']}"}
    headers_b = {"Authorization": f"Bearer {b['token']}"}
    fork_a = await _create_fork(client, headers_a, a["novel_id"], "ff-oa")
    project_a = await _create_project(client, headers_a, a["novel_id"], fork_a["id"], "A")
    chapter_a = await _create_chapter(client, headers_a, a["novel_id"], project_a["id"], "A1")
    await _autosave(client, headers_a, a["novel_id"], project_a["id"], chapter_a["id"], "A", 1)
    url_a = _chapter_url(a["novel_id"], project_a["id"], chapter_a["id"])

    # Owner B probing owner A's chapter under B's own novel: identical 404.
    base_b = _chapter_url(b["novel_id"], project_a["id"], chapter_a["id"])
    assert (await client.get(f"{base_b}/revisions", headers=headers_b)).status_code == 404
    assert (await client.post(f"{base_b}/autosave", json={"content": "x", "base_revision": 1}, headers=headers_b)).status_code == 404
    assert (await client.post(f"{base_b}/rollback", json={"target_revision_id": 1, "base_revision": 1}, headers=headers_b)).status_code == 404
    assert (
        await client.get(
            f"{base_b}/diff",
            params={"base_revision_id": 1, "target_revision_id": 1},
            headers=headers_b,
        )
    ).status_code == 404

    # A foreign revision id inside owner A's own scope is also an identical 404.
    foreign = await client.get(f"{url_a}/revisions/999999991", headers=headers_a)
    assert foreign.status_code == 404
    assert "revision_not_found" in foreign.json()["detail"]["code"]

    # Owner A's own surface still works.
    assert (await client.get(f"{url_a}/revisions", headers=headers_a)).status_code == 200


# ---------------------------------------------------------------------------
# Archived projects block writes, reads keep working
# ---------------------------------------------------------------------------


async def test_archived_project_blocks_revision_writes(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"arch_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-arch")
    project = await _create_project(client, headers, novel_id, fork["id"], "Arch")
    chapter = await _create_chapter(client, headers, novel_id, project["id"], "T")
    url = _chapter_url(novel_id, project["id"], chapter["id"])

    archived = await client.patch(
        PROJECT_BASE.format(novel_id=novel_id) + f"/{project['id']}",
        json={"status": "archived"},
        headers=headers,
    )
    assert archived.status_code == 200

    autosave = await _autosave(client, headers, novel_id, project["id"], chapter["id"], "x", 1)
    assert autosave.status_code == 409 and "project_archived" in autosave.json()["detail"]["code"]
    rollback = await _rollback(client, headers, novel_id, project["id"], chapter["id"], 1, 1)
    assert rollback.status_code == 409 and "project_archived" in rollback.json()["detail"]["code"]

    # Reads still work.
    assert (await client.get(f"{url}/revisions", headers=headers)).status_code == 200


async def test_unauthenticated_revision_routes_reject(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"anon_{uuid.uuid4().hex[:8]}")
    base = _chapter_url(ids["novel_id"], 1, 1)
    assert (await client.post(f"{base}/autosave", json={"content": "x", "base_revision": 1})).status_code == 401
    assert (await client.get(f"{base}/revisions")).status_code == 401
    assert (await client.get(f"{base}/revisions/1")).status_code == 401
    assert (
        await client.get(f"{base}/diff", params={"base_revision_id": 1, "target_revision_id": 1})
    ).status_code == 401
    assert (await client.post(f"{base}/rollback", json={"target_revision_id": 1, "base_revision": 1})).status_code == 401


# ---------------------------------------------------------------------------
# Crash-before-ack durability + model immutability
# ---------------------------------------------------------------------------


async def test_autosave_is_durable_after_crash_before_ack(api_client):
    """A committed autosave survives a lost response (commit-before-ack)."""
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"cba_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-cba")
    project = await _create_project(client, headers, novel_id, fork["id"], "CBA")
    chapter = await _create_chapter(client, headers, novel_id, project["id"], "T")

    saved = await _autosave(client, headers, novel_id, project["id"], chapter["id"], "durable", 1)
    assert saved.status_code == 200 and saved.json()["status"] == "saved"
    revision_id = saved.json()["revision"]["id"]

    # The response was "lost"; a fresh session still sees the committed row.
    async with factory() as session:
        row = await session.get(DerivativeRevision, revision_id)
        assert row is not None
        assert row.content == "durable"
        assert row.kind == "autosave"
        assert row.revision_number == 2


async def test_revision_rows_are_immutable_at_the_model(api_client):
    """In-place mutation of a revision row fails closed (T-36-03-01)."""
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"imm_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-imm")
    project = await _create_project(client, headers, novel_id, fork["id"], "Imm")
    chapter = await _create_chapter(client, headers, novel_id, project["id"], "T")
    await _autosave(client, headers, novel_id, project["id"], chapter["id"], "A", 1)
    url = _chapter_url(novel_id, project["id"], chapter["id"])
    history = await client.get(f"{url}/revisions", headers=headers)
    autosave_id = history.json()["items"][0]["id"]

    async with factory() as session:
        row = await session.get(DerivativeRevision, autosave_id)
        assert row is not None
        row.content = "tampered"
        with pytest.raises(ValueError, match="immutable"):
            await session.flush()
        await session.rollback()

    # The stored snapshot is untouched.
    detail = await client.get(f"{url}/revisions/{autosave_id}", headers=headers)
    assert detail.json()["content"] == "A"


# ---------------------------------------------------------------------------
# Schema migration replay + database-level gates
# ---------------------------------------------------------------------------


async def test_revision_migration_replays(pg_sync_url, require_postgres):
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "head", database_url=pg_sync_url)

    engine = create_engine(pg_sync_url, poolclass=NullPool)
    with engine.connect() as conn:
        insp = sa.inspect(conn)
        assert "derivative_revisions" in insp.get_table_names()
        fks = {
            (tuple(f["constrained_columns"]), f["referred_table"])
            for f in insp.get_foreign_keys("derivative_revisions")
        }
        assert (("chapter_id",), "derivative_chapters") in fks
        assert (("owner_id",), "users") in fks
        assert (("novel_id",), "novels") in fks
        assert (("project_id",), "derivative_projects") in fks
        assert (("parent_revision_id",), "derivative_revisions") in fks
        uniques = {c["name"] for c in insp.get_unique_constraints("derivative_revisions")}
        assert "uq_derivative_revisions_chapter_number" in uniques
        checks = {c["name"] for c in insp.get_check_constraints("derivative_revisions")}
        assert "ck_derivative_revisions_number" in checks
        assert "ck_derivative_revisions_checksum" in checks
        assert "ck_derivative_revisions_kind" in checks
        assert "ck_derivative_revisions_approval" in checks
    engine.dispose()

    # Downgrade to the pre-36-03 head drops the table.
    run_alembic("downgrade", "20260801_derivative_chapter01", database_url=pg_sync_url)
    engine = create_engine(pg_sync_url, poolclass=NullPool)
    with engine.connect() as conn:
        assert "derivative_revisions" not in sa.inspect(conn).get_table_names()
    engine.dispose()

    # Re-upgrade restores the table (replayable migration).
    run_alembic("upgrade", "head", database_url=pg_sync_url)
    engine = create_engine(pg_sync_url, poolclass=NullPool)
    with engine.connect() as conn:
        assert "derivative_revisions" in sa.inspect(conn).get_table_names()
    engine.dispose()


async def test_database_fk_and_constraint_gates(api_client):
    """Revision rows are sealed at the DB level (T-36-03-01)."""
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"ck_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-ck")
    project = await _create_project(client, headers, novel_id, fork["id"], "CK")
    chapter = await _create_chapter(client, headers, novel_id, project["id"], "T")

    engine = create_engine(sync_url, poolclass=NullPool)
    base = {
        "chapter_id": chapter["id"],
        "owner_id": ids["owner_id"],
        "novel_id": novel_id,
        "project_id": project["id"],
        "revision_number": 1,
        "kind": "autosave",
        "content": "",
        "content_checksum": "a" * 64,
        "approval_state": "not_required",
    }

    with engine.connect() as conn:
        # Unknown chapter id -> FK rejection.
        try:
            conn.execute(
                text(
                    "INSERT INTO derivative_revisions (chapter_id, owner_id, novel_id,"
                    " project_id, revision_number, kind, content, content_checksum,"
                    " approval_state) VALUES (999999991, :owner_id, :novel_id,"
                    " :project_id, 99, 'autosave', '', :h, 'not_required')"
                ),
                {**base, "h": "a" * 64},
            )
        except IntegrityError as exc:
            assert "derivative_revisions_chapter_id_fkey" in str(exc)
        else:
            pytest.fail("revision with an unknown chapter_id must violate the FK")
        finally:
            conn.rollback()

        # Non-64 checksum.
        try:
            conn.execute(
                text(
                    "INSERT INTO derivative_revisions (chapter_id, owner_id, novel_id,"
                    " project_id, revision_number, kind, content, content_checksum,"
                    " approval_state) VALUES (:chapter_id, :owner_id, :novel_id,"
                    " :project_id, 90, 'autosave', '', 'short', 'not_required')"
                ),
                base,
            )
        except IntegrityError as exc:
            assert "ck_derivative_revisions_checksum" in str(exc)
        else:
            pytest.fail("short checksum must be rejected by the DB constraint")
        finally:
            conn.rollback()

        # Unknown kind.
        try:
            conn.execute(
                text(
                    "INSERT INTO derivative_revisions (chapter_id, owner_id, novel_id,"
                    " project_id, revision_number, kind, content, content_checksum,"
                    " approval_state) VALUES (:chapter_id, :owner_id, :novel_id,"
                    " :project_id, 91, 'publish', '', :h, 'not_required')"
                ),
                {**base, "h": "a" * 64},
            )
        except IntegrityError as exc:
            assert "ck_derivative_revisions_kind" in str(exc)
        else:
            pytest.fail("unknown revision kind must be rejected by the DB constraint")
        finally:
            conn.rollback()

        # Non-positive revision_number.
        try:
            conn.execute(
                text(
                    "INSERT INTO derivative_revisions (chapter_id, owner_id, novel_id,"
                    " project_id, revision_number, kind, content, content_checksum,"
                    " approval_state) VALUES (:chapter_id, :owner_id, :novel_id,"
                    " :project_id, 0, 'autosave', '', :h, 'not_required')"
                ),
                {**base, "h": "a" * 64},
            )
        except IntegrityError as exc:
            assert "ck_derivative_revisions_number" in str(exc)
        else:
            pytest.fail("zero revision_number must be rejected by the DB constraint")
        finally:
            conn.rollback()

        # Unknown approval_state.
        try:
            conn.execute(
                text(
                    "INSERT INTO derivative_revisions (chapter_id, owner_id, novel_id,"
                    " project_id, revision_number, kind, content, content_checksum,"
                    " approval_state) VALUES (:chapter_id, :owner_id, :novel_id,"
                    " :project_id, 92, 'autosave', '', :h, 'draft')"
                ),
                {**base, "h": "a" * 64},
            )
        except IntegrityError as exc:
            assert "ck_derivative_revisions_approval" in str(exc)
        else:
            pytest.fail("unknown approval_state must be rejected by the DB constraint")
        finally:
            conn.rollback()

        # Duplicate (chapter_id, revision_number) — a real autosave already holds 1.
        try:
            conn.execute(
                text(
                    "INSERT INTO derivative_revisions (chapter_id, owner_id, novel_id,"
                    " project_id, revision_number, kind, content, content_checksum,"
                    " approval_state) VALUES (:chapter_id, :owner_id, :novel_id,"
                    " :project_id, 1, 'autosave', '', :h, 'not_required')"
                ),
                {**base, "h": "b" * 64},
            )
        except IntegrityError as exc:
            assert "uq_derivative_revisions_chapter_number" in str(exc)
        else:
            pytest.fail("duplicate (chapter_id, revision_number) must be rejected")
        finally:
            conn.rollback()
    engine.dispose()
