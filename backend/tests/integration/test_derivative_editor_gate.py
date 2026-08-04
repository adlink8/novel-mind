"""Phase 36-04 pre-release fail-closed gate (D-36-03/D-36-04, T-36-04-01/02).

The editor browser UAT gate proves that no editor path can release content
outside Fanfiction Canon and that owner/fork errors never leak data, on the real
CI database:

- **No publish/release surface**: the OpenAPI path table has no
  publish/release/promote route anywhere on the derivative surface, and the
  wire rejects any attempt to inject a published state (forged ``space`` /
  ``kind`` / ``status`` are 422 and a DB-level ``kind='publish'`` row violates
  ``ck_derivative_revisions_kind``).
- **Original Canon is immutable from the editor (T-36-04-01)**: a full editor
  session (fork + project + chapter + autosave + rollback) leaves every
  original ``chapters`` row byte-identical, and every derivative row is sealed
  to ``fanfiction_canon``.
- **Owner/fork isolation on the editor surface (T-36-04-01)**: a foreign
  owner probing any revision route receives an identical 404 whose body never
  contains the victim's Markdown.
- **Rollback control is server-approved and append-only (T-36-04-02)**:
  rollback requires the explicit ``base_revision`` CAS token (422 without it),
  journals ``kind=rollback / approval_state=approved / reason``, never rewrites
  history, and a stale base is a 409 that leaves the head untouched.
- **Phase 22 independence**: the derivative surface exposes no route that could
  change verification/phase-gate state; the gate only asserts the editor writes
  are scoped to derivative rows.
"""

from __future__ import annotations

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
            username=f"gt_{suffix}",
            email=f"gt_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
            is_superuser=False,
        )
        session.add(user)
        session.flush()
        novel = Novel(
            title=f"GT Novel {suffix}",
            owner_id=user.id,
            status="ready",
            reading_progress={},
            chapter_count=chapter_count,
            word_count=sum(len(f"original {i} body") for i in range(1, chapter_count + 1)),
        )
        session.add(novel)
        session.flush()
        for i in range(1, chapter_count + 1):
            content = f"original {i} body"
            session.add(
                Chapter(
                    novel_id=novel.id,
                    chapter_number=i,
                    title=f"OriginalC{i}",
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


def _original_canon_state(sync_url: str) -> list[tuple]:
    """Snapshot of every Original Canon ``chapters`` row (the truth of D-36-03)."""
    engine = create_engine(sync_url, poolclass=NullPool)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT novel_id, chapter_number, title, content, word_count "
                "FROM chapters ORDER BY novel_id, chapter_number"
            )
        ).fetchall()
    engine.dispose()
    return [tuple(row) for row in rows]


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
# Gate 1: no publish/release surface exists on the derivative routes
# ---------------------------------------------------------------------------


async def test_no_publish_or_release_route_on_derivative_surface(api_client):
    """Publishing is deferred to Phase 39; the editor surface cannot release.

    The approval-gated agent action tools under ``/api/agent-tools/``
    (``publish_derivative_revision``, 37-05) are **not** editor write routes:
    they only create pending Web ApprovalRequests and are consumed by the
    deterministic publisher seam. This gate scopes the no-publish check to the
    browser editor surfaces (``/api/novels/.../derivative-*``).
    """
    paths = list(app.openapi()["paths"].keys())
    derivative_paths = [
        p
        for p in paths
        if "derivative" in p and not p.startswith("/api/agent-tools/")
    ]
    assert derivative_paths, "expected the derivative route surface to exist"
    for path in derivative_paths:
        lowered = path.lower()
        assert not any(
            token in lowered for token in ("publish", "release", "promote", "ship")
        ), f"release-like route leaked onto the editor surface: {path}"
    # The exact chapter write surface is the known immutable-revision set only.
    assert any(p.endswith("/autosave") for p in derivative_paths)
    assert any(p.endswith("/rollback") for p in derivative_paths)
    assert any(p.endswith("/diff") for p in derivative_paths)
    assert any(p.endswith("/revisions") for p in derivative_paths)


async def test_forged_space_kind_status_are_rejected_on_the_wire(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"forg_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-forg")
    project = await _create_project(client, headers, novel_id, fork["id"], "Forg")

    # The client can never widen the scope to Original Canon / Interpretation.
    for forged in (
        {"fork_id": fork["id"], "name": "X", "space": "original_canon"},
        {"fork_id": fork["id"], "name": "X", "space": "user_interpretation"},
    ):
        resp = await client.post(
            PROJECT_BASE.format(novel_id=novel_id), json=forged, headers=headers
        )
        assert resp.status_code == 422, (forged, resp.text)

    # Chapter status is constrained to draft/archived — never published.
    resp = await client.post(
        CHAPTER_BASE.format(novel_id=novel_id, project_id=project["id"]),
        json={"title": "T", "status": "published"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text

    # Autosave can never inject kind/revision/checksum/approval.
    url = _chapter_url(novel_id, project["id"], 1)
    for forged in (
        {"content": "x", "base_revision": 1, "kind": "publish"},
        {"content": "x", "base_revision": 1, "approval_state": "approved"},
        {"content": "x", "base_revision": 1, "revision_number": 99},
        {"content": "x", "base_revision": 1, "content_checksum": "0" * 64},
    ):
        resp = await client.post(f"{url}/autosave", json=forged, headers=headers)
        assert resp.status_code == 422, (forged, resp.text)

    # Rollback can never inject its own approval state.
    resp = await client.post(
        f"{url}/rollback",
        json={"target_revision_id": 1, "base_revision": 1, "approval_state": "draft"},
        headers=headers,
    )
    assert resp.status_code == 422, resp.text


async def test_db_rejects_a_publish_kind_revision(api_client):
    """Even a direct DB write cannot create a publish-kind revision (fail closed)."""
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"pk_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-pk")
    project = await _create_project(client, headers, novel_id, fork["id"], "PK")
    chapter = await _create_chapter(client, headers, novel_id, project["id"], "T")

    engine = create_engine(sync_url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            try:
                conn.execute(
                    text(
                        "INSERT INTO derivative_revisions (chapter_id, owner_id,"
                        " novel_id, project_id, revision_number, kind, content,"
                        " content_checksum, approval_state) VALUES (:chapter_id,"
                        " :owner_id, :novel_id, :project_id, 99, 'publish', '', :h,"
                        " 'not_required')"
                    ),
                    {
                        "chapter_id": chapter["id"],
                        "owner_id": ids["owner_id"],
                        "novel_id": novel_id,
                        "project_id": project["id"],
                        "h": "a" * 64,
                    },
                )
            except IntegrityError as exc:
                assert "ck_derivative_revisions_kind" in str(exc)
            else:
                pytest.fail("a publish-kind revision must be rejected by the DB")
            finally:
                conn.rollback()
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Gate 2: Original Canon stays immutable through a full editor session
# ---------------------------------------------------------------------------


async def test_editor_session_never_touches_original_canon(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"orig_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]

    before = _original_canon_state(sync_url)

    # Full editor session: explicit fork -> project -> chapter -> autosave x2
    # -> rollback (the entire 36-04 browser UAT write path).
    fork = await _create_fork(client, headers, novel_id, "ff-orig")
    project = await _create_project(client, headers, novel_id, fork["id"], "Orig")
    chapter = await _create_chapter(
        client, headers, novel_id, project["id"], "T", markdown="draft one"
    )
    url = _chapter_url(novel_id, project["id"], chapter["id"])
    assert (await _autosave(client, headers, novel_id, project["id"], chapter["id"], "draft two", 1)).status_code == 200
    assert (await _autosave(client, headers, novel_id, project["id"], chapter["id"], "draft three", 2)).status_code == 200
    history = await client.get(f"{url}/revisions", headers=headers)
    root_id = history.json()["items"][-1]["id"]
    rollback = await _rollback(client, headers, novel_id, project["id"], chapter["id"], root_id, 3)
    assert rollback.status_code == 200

    # The original chapters (Original Canon) are byte-identical.
    assert _original_canon_state(sync_url) == before

    # Every derivative row is sealed to the Fanfiction space (D-36-03).
    engine = create_engine(sync_url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            spaces = conn.execute(
                text("SELECT DISTINCT space FROM derivative_projects")
            ).fetchall()
            assert spaces and all(
                row[0] == "fanfiction_canon" for row in spaces
            ), spaces
            revision_kinds = conn.execute(
                text("SELECT DISTINCT kind FROM derivative_revisions")
            ).fetchall()
            assert all(
                row[0] in ("create", "autosave", "rollback") for row in revision_kinds
            ), revision_kinds
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# Gate 3: owner/fork isolation — an identical 404 that never leaks content
# ---------------------------------------------------------------------------


async def test_cross_owner_editor_probe_does_not_leak(api_client):
    client, _, sync_url = api_client
    a = _seed_owner(sync_url, suffix=f"lea_{uuid.uuid4().hex[:8]}")
    b = _seed_owner(sync_url, suffix=f"leb_{uuid.uuid4().hex[:8]}")
    headers_a = {"Authorization": f"Bearer {a['token']}"}
    headers_b = {"Authorization": f"Bearer {b['token']}"}
    fork_a = await _create_fork(client, headers_a, a["novel_id"], "ff-lea")
    project_a = await _create_project(client, headers_a, a["novel_id"], fork_a["id"], "LeakA")
    chapter_a = await _create_chapter(
        client, headers_a, a["novel_id"], project_a["id"], "A", markdown="SECRET_MARKDOWN"
    )
    await _autosave(client, headers_a, a["novel_id"], project_a["id"], chapter_a["id"], "SECRET_MARKDOWN v2", 1)
    url_a = _chapter_url(a["novel_id"], project_a["id"], chapter_a["id"])

    # Owner B probes A's chapter under B's own novel: identical 404 everywhere.
    probes = [
        ("get", f"{_chapter_url(b['novel_id'], project_a['id'], chapter_a['id'])}/revisions", None),
        (
            "post",
            f"{_chapter_url(b['novel_id'], project_a['id'], chapter_a['id'])}/autosave",
            {"content": "SECRET_MARKDOWN v3", "base_revision": 1},
        ),
        (
            "post",
            f"{_chapter_url(b['novel_id'], project_a['id'], chapter_a['id'])}/rollback",
            {"target_revision_id": 1, "base_revision": 1},
        ),
    ]
    for method, path, body in probes:
        kwargs: dict = {"headers": headers_b}
        if body is not None:
            kwargs["json"] = body
        resp = await getattr(client, method)(path, **kwargs)
        assert resp.status_code == 404, (method, path, resp.text)
        # The 404 body never carries the victim's content (T-36-04-01).
        assert "SECRET_MARKDOWN" not in resp.text

    # B probing A's novel routes directly is also an identical 404 with no leak.
    for method, path, body in (
        ("get", f"{url_a}/revisions", None),
        (
            "post",
            f"{url_a}/autosave",
            {"content": "SECRET_MARKDOWN v3", "base_revision": 1},
        ),
    ):
        kwargs: dict = {"headers": headers_b}
        if body is not None:
            kwargs["json"] = body
        resp = await getattr(client, method)(path, **kwargs)
        assert resp.status_code == 404, (method, path, resp.text)
        assert "SECRET_MARKDOWN" not in resp.text

    # A foreign fork cannot anchor a project (identical 404, no data).
    resp = await client.post(
        PROJECT_BASE.format(novel_id=b["novel_id"]),
        json={"fork_id": fork_a["id"], "name": "Sneaky"},
        headers=headers_b,
    )
    assert resp.status_code == 404
    assert "SECRET_MARKDOWN" not in resp.text

    # A still reads its own history; the data was never leaked to B.
    assert (await client.get(f"{url_a}/revisions", headers=headers_a)).status_code == 200


# ---------------------------------------------------------------------------
# Gate 4: rollback control is server-approved and append-only (T-36-04-02)
# ---------------------------------------------------------------------------


async def test_rollback_requires_explicit_base_and_journals_approval(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"rb_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-rb")
    project = await _create_project(client, headers, novel_id, fork["id"], "RB")
    chapter = await _create_chapter(client, headers, novel_id, project["id"], "T")
    url = _chapter_url(novel_id, project["id"], chapter["id"])

    # Rollback without the CAS token is rejected up front (422).
    resp = await client.post(
        f"{url}/rollback", json={"target_revision_id": 1}, headers=headers
    )
    assert resp.status_code == 422, resp.text

    await _autosave(client, headers, novel_id, project["id"], chapter["id"], "A", 1)
    await _autosave(client, headers, novel_id, project["id"], chapter["id"], "B", 2)
    history = await client.get(f"{url}/revisions", headers=headers)
    items = history.json()["items"]  # newest first: [3(B), 2(A), 1(create)]
    rev_a_id = items[1]["id"]

    rollback = await _rollback(
        client, headers, novel_id, project["id"], chapter["id"], rev_a_id, 3,
        reason="revert bad chapter",
    )
    assert rollback.status_code == 200, rollback.text
    body = rollback.json()
    # Server-approved, journaled, and a NEW child revision — never in place.
    assert body["revision"]["kind"] == "rollback"
    assert body["revision"]["approval_state"] == "approved"
    assert body["revision"]["reason"] == "revert bad chapter"
    assert body["revision"]["parent_revision_id"] == items[0]["id"]
    assert body["chapter"]["revision"] == 4

    # History is append-only; the "B" row still holds its original content.
    after = await client.get(f"{url}/revisions", headers=headers)
    after_items = after.json()["items"]
    assert after.json()["total"] == 4
    assert [item["revision_number"] for item in after_items] == [4, 3, 2, 1]
    rev_b_detail = await client.get(f"{url}/revisions/{items[0]['id']}", headers=headers)
    assert rev_b_detail.json()["content"] == "B"

    # A stale rollback (base 3 while the head is now 4) fails closed with 409
    # and the head is untouched.
    stale = await _rollback(client, headers, novel_id, project["id"], chapter["id"], rev_a_id, 3)
    assert stale.status_code == 409, stale.text
    detail = stale.json()["detail"]
    assert detail["code"] == "revision_conflict"
    assert detail["current_revision_number"] == 4
    head = await client.get(url, headers=headers)
    assert head.json()["revision"] == 4
    assert head.json()["markdown"] == "A"


# ---------------------------------------------------------------------------
# Gate 5: the editor surface cannot touch phase/verification gate state
# ---------------------------------------------------------------------------


async def test_editor_surface_has_no_phase_gate_state_route(api_client):
    """Phase 22 BLOCKED/0-of-3 is untouched: the editor surface has no gate
    state route (other pre-existing /gate routes elsewhere are out of scope)."""
    paths = list(app.openapi()["paths"].keys())
    derivative_paths = [p for p in paths if "derivative" in p]
    assert derivative_paths
    for token in ("phase", "verification", "nightly", "gate"):
        for path in derivative_paths:
            assert token not in path.lower(), (
                f"editor-affecting route {path} could alter gate state"
            )


# ---------------------------------------------------------------------------
# Schema migration replay (the gate runs on the 36-03 head)
# ---------------------------------------------------------------------------


async def test_gate_migration_replays(pg_sync_url, require_postgres):
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "head", database_url=pg_sync_url)
    engine = create_engine(pg_sync_url, poolclass=NullPool)
    with engine.connect() as conn:
        insp = sa.inspect(conn)
        assert "derivative_revisions" in insp.get_table_names()
        checks = {c["name"] for c in insp.get_check_constraints("derivative_revisions")}
        assert "ck_derivative_revisions_kind" in checks
        assert "ck_derivative_revisions_approval" in checks
    engine.dispose()
