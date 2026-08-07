"""Phase 37-04 divergence override PostgreSQL API tests (D-37-03 / REQ-CRE-06).

Covers the explicit override review gate on the real CI database:

- a blocked / ``needs_override`` candidate accepts exactly one explicit
  override; a clean ``candidate`` never does;
- an override without a reason, without affected evidence or without an
  approval note is rejected (fail closed, T-37-04-01);
- approval materializes the candidate into a **Fanfiction Canon**
  ``derivative_revisions`` row only, returns the immutable
  ``PublishedDerivativeRevision`` DTO with the frozen Phase 39 field set, and
  never writes Original Canon chapters, revisions or active pointers;
- rejection terminates the override without any revision; a decided override
  cannot be re-approved;
- cross-owner overrides/approvals are identical 404s; a project bound to a
  different fork fails closed (``cross_fork_override``).
"""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.api.derivative_generation import (
    get_derivative_budget_gate,
    get_derivative_transport,
)
from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.derivative_override import DerivativeOverride
from app.models.novel import Chapter, Novel
from app.models.user import User
from app.services.derivative_generation.published_revision import (
    PUBLISHED_DERIVATIVE_REVISION_FIELDS,
)
from app.services.derivative_generation.runner import (
    DEFAULT_DERIVATIVE_BUDGET,
    DerivativeBudgetGate,
)
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

PACKAGE_BASE = "/api/novels/{novel_id}/derivative-context-packages"
JOB_BASE = "/api/novels/{novel_id}/derivative-generation-jobs"
PROJECT_BASE = "/api/novels/{novel_id}/derivative-projects"
OVERRIDE_BASE = "/api/novels/{novel_id}/derivative-overrides"
FORK_BASE = "/api/novels/{novel_id}/canon-fork"
HEX64 = "a" * 64


# ---------------------------------------------------------------------------
# Deterministic fake gateway (injected via dependency overrides)
# ---------------------------------------------------------------------------


class FakeGateway:
    def __init__(self) -> None:
        self.responses: list = []
        self.calls: list = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


gateway = FakeGateway()
current_budget_gate = DerivativeBudgetGate(DEFAULT_DERIVATIVE_BUDGET)


def _override_transport():
    return gateway


def _override_budget_gate():
    return current_budget_gate


def _candidate_json(
    *,
    intent="continuation",
    citations=None,
    divergence=None,
    draft="阿宁在竹林入口站定，深吸一口气。",
):
    payload = {
        "schema_version": "derivative-candidate.v1",
        "intent": intent,
        "draft_text": draft,
        "citation_keys": citations or [],
        "divergence": divergence,
        "branch_suggestions": [],
    }
    return json.dumps(payload, ensure_ascii=False)


def _divergence_payload(evidence):
    return {
        "divergence_type": "character",
        "reason": "the twist requires the hero to know the secret early",
        "affected_evidence": evidence,
        "scope": "derivative",
    }


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
    app.dependency_overrides[get_derivative_transport] = _override_transport
    app.dependency_overrides[get_derivative_budget_gate] = _override_budget_gate
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, factory, migrated_postgres

    app.dependency_overrides.clear()
    await aengine.dispose()


def _seed_owner(sync_url: str, *, suffix: str, chapter_count: int = 3) -> dict:
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        user = User(
            username=f"dgo_{suffix}",
            email=f"dgo_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
            is_superuser=False,
        )
        session.add(user)
        session.flush()
        novel = Novel(
            title=f"DGO Novel {suffix}",
            owner_id=user.id,
            status="ready",
            reading_progress={},
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


async def _create_fork(client, headers, novel_id, fork_key) -> dict:
    resp = await client.post(
        FORK_BASE.format(novel_id=novel_id),
        json={"fork_key": fork_key},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["fork"]


def _seed_world_model(sync_url: str, *, owner_id: int, novel_id: int) -> None:
    idem = uuid.uuid4().hex + uuid.uuid4().hex  # 64-hex, unique per call
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
                 :h, :idem, :h, 'world-model-entity.v1')
                """
            ),
            {"owner_id": owner_id, "novel_id": novel_id, "h": HEX64, "idem": idem},
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
                 :h, :idem, :h, 'world-model-rule.v1')
                """
            ),
            {"owner_id": owner_id, "novel_id": novel_id, "h": HEX64, "idem": idem},
        )
        conn.commit()
    engine.dispose()


async def _compile(
    client, headers, novel_id, fork_id, *, intent="continuation"
) -> dict:
    resp = await client.post(
        PACKAGE_BASE.format(novel_id=novel_id),
        json={"fork_id": fork_id, "intent": intent},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _first_evidence_key(pkg: dict) -> str:
    items = pkg["payload"]["dimensions"]["evidence"]["items"]
    assert items, "sealed package must carry evidence items"
    return str(items[0]["candidate_key"])


async def _create_project(client, headers, novel_id, fork_id, name) -> dict:
    resp = await client.post(
        PROJECT_BASE.format(novel_id=novel_id),
        json={
            "fork_id": fork_id,
            "name": name,
            "project_key": f"key-{uuid.uuid4().hex[:8]}",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["project"]


async def _create_chapter(
    client, headers, novel_id, project_id, title="Chapter 4"
) -> dict:
    resp = await client.post(
        PROJECT_BASE.format(novel_id=novel_id) + f"/{project_id}/chapters",
        json={"title": title},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["chapter"]


async def _create_job(client, headers, novel_id, package_id, job_key) -> dict:
    resp = await client.post(
        JOB_BASE.format(novel_id=novel_id),
        json={
            "context_package_id": package_id,
            "intent": "continuation",
            "job_key": job_key,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _run_job(client, headers, novel_id, job_id) -> dict:
    resp = await client.post(
        JOB_BASE.format(novel_id=novel_id) + f"/{job_id}/run", headers=headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _reset_gateway() -> None:
    gateway.responses = []
    gateway.calls = []
    current_budget_gate.__init__(DEFAULT_DERIVATIVE_BUDGET)


async def _build_override_candidate(
    client, headers, ids, suffix, *, divergence_evidence=None
) -> dict:
    """Fork + world model + package + job + run producing a needs_override candidate."""
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, f"ff-{suffix}")
    _seed_world_model(
        sync_url=ids["_sync_url"], owner_id=ids["owner_id"], novel_id=novel_id
    )
    pkg = await _compile(client, headers, novel_id, fork["id"])
    pkg_view = pkg["package"]
    cite = _first_evidence_key(pkg_view)
    _reset_gateway()
    divergence = _divergence_payload(
        [cite] if divergence_evidence is None else divergence_evidence
    )
    created = await _create_job(
        client, headers, novel_id, pkg_view["id"], job_key=f"{suffix}-key"
    )
    gateway.responses = [
        {
            "content": _candidate_json(citations=[cite], divergence=divergence),
            "usage": {"input_tokens": 5, "output_tokens": 2},
        }
    ]
    run = await _run_job(client, headers, novel_id, created["job"]["id"])
    assert run["job"]["status"] == "needs_override", run
    return {
        "fork": fork,
        "package": pkg_view,
        "candidate": run["candidate"],
        "cite": cite,
    }


async def _project_chapter(client, headers, ids, fork) -> dict:
    novel_id = ids["novel_id"]
    project = await _create_project(
        client, headers, novel_id, fork["id"], "Override Project"
    )
    chapter = await _create_chapter(client, headers, novel_id, project["id"])
    return {"project": project, "chapter": chapter}


async def _create_override(
    client,
    headers,
    novel_id,
    *,
    candidate_id,
    project_id,
    chapter_id,
    reason="the twist requires the hero to know the secret",
    evidence=None,
    kind=None,
):
    body = {
        "candidate_id": candidate_id,
        "project_id": project_id,
        "chapter_id": chapter_id,
        "reason": reason,
        "affected_evidence": evidence or [],
    }
    if kind is not None:
        body["kind"] = kind
    return await client.post(
        OVERRIDE_BASE.format(novel_id=novel_id), json=body, headers=headers
    )


# ---------------------------------------------------------------------------
# Explicit override gate (D-37-03 / T-37-04-01)
# ---------------------------------------------------------------------------


async def test_clean_candidate_is_never_overridable(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"clean_{uuid.uuid4().hex[:8]}")
    ids["_sync_url"] = sync_url
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-clean")
    _seed_world_model(sync_url, owner_id=ids["owner_id"], novel_id=novel_id)
    pkg = await _compile(client, headers, novel_id, fork["id"])
    cite = _first_evidence_key(pkg["package"])
    _reset_gateway()
    created = await _create_job(
        client, headers, novel_id, pkg["package"]["id"], job_key="clean-key"
    )
    gateway.responses = [
        {
            "content": _candidate_json(citations=[cite]),
            "usage": {"input_tokens": 5, "output_tokens": 2},
        }
    ]
    run = await _run_job(client, headers, novel_id, created["job"]["id"])
    assert run["candidate"]["gate_verdict"] == "candidate"
    project = await _create_project(
        client, headers, novel_id, fork["id"], "Clean Project"
    )
    chapter = await _create_chapter(client, headers, novel_id, project["id"])

    resp = await _create_override(
        client,
        headers,
        novel_id,
        candidate_id=run["candidate"]["id"],
        project_id=project["id"],
        chapter_id=chapter["id"],
        evidence=[cite],
    )
    assert resp.status_code == 409, resp.text
    assert "candidate_not_overridable" in resp.json()["detail"]


async def test_override_without_reason_or_evidence_is_rejected(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"req_{uuid.uuid4().hex[:8]}")
    ids["_sync_url"] = sync_url
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    data = await _build_override_candidate(client, headers, ids, "req")
    target = await _project_chapter(client, headers, ids, data["fork"])
    candidate_id = data["candidate"]["id"]
    project_id = target["project"]["id"]
    chapter_id = target["chapter"]["id"]

    # No reason -> rejected (service strips the blank and fails closed).
    resp = await _create_override(
        client,
        headers,
        novel_id,
        candidate_id=candidate_id,
        project_id=project_id,
        chapter_id=chapter_id,
        reason="   ",
    )
    assert resp.status_code == 400, resp.text
    assert "missing_reason" in resp.json()["detail"]

    # Evidence outside the sealed package allowlist -> rejected.
    resp = await _create_override(
        client,
        headers,
        novel_id,
        candidate_id=candidate_id,
        project_id=project_id,
        chapter_id=chapter_id,
        evidence=["fork:req:future:999"],
    )
    assert resp.status_code == 400, resp.text
    assert "evidence_outside_package" in resp.json()["detail"]


async def test_approve_without_approval_note_is_rejected(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"appr_{uuid.uuid4().hex[:8]}")
    ids["_sync_url"] = sync_url
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    data = await _build_override_candidate(client, headers, ids, "appr")
    target = await _project_chapter(client, headers, ids, data["fork"])
    created = await _create_override(
        client,
        headers,
        novel_id,
        candidate_id=data["candidate"]["id"],
        project_id=target["project"]["id"],
        chapter_id=target["chapter"]["id"],
        evidence=[data["cite"]],
    )
    assert created.status_code == 201, created.text
    override_id = created.json()["override"]["id"]

    resp = await client.post(
        OVERRIDE_BASE.format(novel_id=novel_id) + f"/{override_id}/approve",
        json={"approval_reason": "   "},
        headers=headers,
    )
    assert resp.status_code == 400, resp.text
    assert "missing_approval" in resp.json()["detail"]


async def test_approve_materializes_fanfiction_revision_only(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"full_{uuid.uuid4().hex[:8]}")
    ids["_sync_url"] = sync_url
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    data = await _build_override_candidate(client, headers, ids, "full")
    target = await _project_chapter(client, headers, ids, data["fork"])
    created = await _create_override(
        client,
        headers,
        novel_id,
        candidate_id=data["candidate"]["id"],
        project_id=target["project"]["id"],
        chapter_id=target["chapter"]["id"],
        evidence=[data["cite"]],
    )
    assert created.status_code == 201, created.text
    override = created.json()["override"]
    assert override["approval_state"] == "pending"
    assert override["kind"] == "character"
    assert len(override["canon_delta_hash"]) == 64
    assert override["evidence_snapshot"]["gate_verdict"] == "needs_override"

    resp = await client.post(
        OVERRIDE_BASE.format(novel_id=novel_id) + f"/{override['id']}/approve",
        json={"approval_reason": "owner approved the twist divergence"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["override"]["approval_state"] == "approved"
    assert body["override"]["approver_id"] == ids["owner_id"]
    assert body["override"]["approved_at"] is not None

    published = body["published"]
    assert set(published.keys()) == set(PUBLISHED_DERIVATIVE_REVISION_FIELDS)
    assert published["status"] == "derivative_revision"
    assert published["owner_id"] == ids["owner_id"]
    assert published["project_id"] == target["project"]["id"]
    assert published["fork_id"] == data["fork"]["id"]
    assert published["source_snapshot"] == data["fork"]["source_snapshot_hash"]
    assert len(published["citation_hash"]) == 64
    assert published["asset_hashes"] == []
    assert published["approval"]["kind"] == "character"
    assert published["review"]["gate_verdict"] == "needs_override"

    # Exactly one immutable Fanfiction `agent_proposal` revision row was appended
    # (plus the chapter's own ``create`` root row).
    async with factory() as session:
        override_revision_count = await session.scalar(
            text(
                "SELECT count(*) FROM derivative_revisions "
                "WHERE novel_id = :n AND kind = 'agent_proposal'"
            ),
            {"n": novel_id},
        )
        revision_count = await session.scalar(
            text("SELECT count(*) FROM derivative_revisions WHERE novel_id = :n"),
            {"n": novel_id},
        )
        override_count = await session.scalar(
            select(func.count())
            .select_from(DerivativeOverride)
            .where(DerivativeOverride.candidate_id == data["candidate"]["id"])
        )
        chapter_markdown = await session.scalar(
            text("SELECT markdown FROM derivative_chapters WHERE id = :c"),
            {"c": target["chapter"]["id"]},
        )
        original = list(
            (
                await session.execute(
                    text(
                        "SELECT chapter_number, content FROM chapters "
                        "WHERE novel_id = :n ORDER BY chapter_number"
                    ),
                    {"n": novel_id},
                )
            ).all()
        )
        artifact_count = await session.scalar(
            text(
                "SELECT count(*) FROM canon_space_artifacts "
                "WHERE owner_id = :o AND novel_id = :n"
            ),
            {"o": ids["owner_id"], "n": novel_id},
        )
    assert override_revision_count == 1
    assert revision_count == 2  # create root + one override materialization
    assert override_count == 1
    assert chapter_markdown == data["candidate"]["draft_text"]
    assert original == [
        (1, "chapter 1 body"),
        (2, "chapter 2 body"),
        (3, "chapter 3 body"),
    ]
    assert artifact_count == 0


async def test_reject_override_creates_no_revision(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"rej_{uuid.uuid4().hex[:8]}")
    ids["_sync_url"] = sync_url
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    data = await _build_override_candidate(client, headers, ids, "rej")
    target = await _project_chapter(client, headers, ids, data["fork"])
    created = await _create_override(
        client,
        headers,
        novel_id,
        candidate_id=data["candidate"]["id"],
        project_id=target["project"]["id"],
        chapter_id=target["chapter"]["id"],
        evidence=[data["cite"]],
    )
    assert created.status_code == 201, created.text
    override_id = created.json()["override"]["id"]

    resp = await client.post(
        OVERRIDE_BASE.format(novel_id=novel_id) + f"/{override_id}/reject",
        json={"rejection_reason": "author changed their mind"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["override"]["approval_state"] == "rejected"

    # Only the chapter's ``create`` root row exists; no override materialization.
    async with factory() as session:
        override_revision_count = await session.scalar(
            text(
                "SELECT count(*) FROM derivative_revisions "
                "WHERE novel_id = :n AND kind = 'agent_proposal'"
            ),
            {"n": novel_id},
        )
        revision_count = await session.scalar(
            text("SELECT count(*) FROM derivative_revisions WHERE novel_id = :n"),
            {"n": novel_id},
        )
    assert override_revision_count == 0
    assert revision_count == 1

    # A decided override can never be re-approved.
    resp = await client.post(
        OVERRIDE_BASE.format(novel_id=novel_id) + f"/{override_id}/approve",
        json={"approval_reason": "late approval"},
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    assert "already_decided" in resp.json()["detail"]


async def test_cross_owner_override_is_identical_404(api_client):
    client, _, sync_url = api_client
    a = _seed_owner(sync_url, suffix=f"ow_a_{uuid.uuid4().hex[:8]}")
    b = _seed_owner(sync_url, suffix=f"ow_b_{uuid.uuid4().hex[:8]}")
    a["_sync_url"] = sync_url
    headers_a = {"Authorization": f"Bearer {a['token']}"}
    headers_b = {"Authorization": f"Bearer {b['token']}"}
    data = await _build_override_candidate(client, headers_a, a, "owna")
    # Owner B's own project/chapter (valid scope for B).
    b_fork = await _create_fork(client, headers_b, b["novel_id"], "ff-ownb")
    b_project = await _create_project(
        client, headers_b, b["novel_id"], b_fork["id"], "B Project"
    )
    b_chapter = await _create_chapter(client, headers_b, b["novel_id"], b_project["id"])

    # Owner B cannot create an override on owner A's candidate (identical 404).
    resp = await _create_override(
        client,
        headers_b,
        b["novel_id"],
        candidate_id=data["candidate"]["id"],
        project_id=b_project["id"],
        chapter_id=b_chapter["id"],
        evidence=["fork:any"],
    )
    assert resp.status_code == 404, resp.text
    assert "candidate_not_found" in resp.json()["detail"]


async def test_cross_fork_project_override_fails_closed(api_client):
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"fork_{uuid.uuid4().hex[:8]}")
    ids["_sync_url"] = sync_url
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    data = await _build_override_candidate(client, headers, ids, "cf")
    # A second project bound to a different fork.
    other_fork = await _create_fork(client, headers, novel_id, "ff-cf-other")
    foreign_project = await _create_project(
        client, headers, novel_id, other_fork["id"], "Wrong Fork Project"
    )
    foreign_chapter = await _create_chapter(
        client, headers, novel_id, foreign_project["id"]
    )

    resp = await _create_override(
        client,
        headers,
        novel_id,
        candidate_id=data["candidate"]["id"],
        project_id=foreign_project["id"],
        chapter_id=foreign_chapter["id"],
        evidence=[data["cite"]],
    )
    assert resp.status_code == 409, resp.text
    assert "cross_fork_override" in resp.json()["detail"]


async def test_override_row_frozen_surface_is_immutable_at_database_level(
    api_client, migrated_postgres
):
    """T-37-04-01: the divergence surface cannot be rewritten or deleted."""
    client, _, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"imm_{uuid.uuid4().hex[:8]}")
    ids["_sync_url"] = sync_url
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    data = await _build_override_candidate(client, headers, ids, "imm")
    target = await _project_chapter(client, headers, ids, data["fork"])
    created = await _create_override(
        client,
        headers,
        novel_id,
        candidate_id=data["candidate"]["id"],
        project_id=target["project"]["id"],
        chapter_id=target["chapter"]["id"],
        evidence=[data["cite"]],
    )
    assert created.status_code == 201, created.text
    override_id = created.json()["override"]["id"]

    aengine = create_async_engine(async_url(migrated_postgres), poolclass=NullPool)
    factory = async_sessionmaker(aengine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        row = await session.scalar(
            select(DerivativeOverride).where(DerivativeOverride.id == override_id)
        )
        assert row is not None
        row.kind = "timeline"  # frozen divergence surface
        with pytest.raises(ValueError, match="immutable"):
            await session.flush()
        await session.rollback()

        row2 = await session.scalar(
            select(DerivativeOverride).where(DerivativeOverride.id == override_id)
        )
        with pytest.raises(ValueError, match="cannot be deleted"):
            await session.delete(row2)
            await session.flush()
        await session.rollback()
    await aengine.dispose()


async def test_blocked_candidate_accepts_owner_supplied_divergence(api_client):
    """A blocked candidate without a CanonDelta accepts an owner-declared kind."""
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"block_{uuid.uuid4().hex[:8]}")
    ids["_sync_url"] = sync_url
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-block")
    _seed_world_model(sync_url, owner_id=ids["owner_id"], novel_id=novel_id)
    pkg = await _compile(client, headers, novel_id, fork["id"])
    cite = _first_evidence_key(pkg["package"])
    _reset_gateway()
    created = await _create_job(
        client, headers, novel_id, pkg["package"]["id"], job_key="block-key"
    )
    # Provider cites evidence outside the package -> deterministic blocked.
    gateway.responses = [
        {
            "content": _candidate_json(citations=["fork:ff-block:chapter:999"]),
            "usage": {"input_tokens": 5, "output_tokens": 2},
        }
    ]
    run = await _run_job(client, headers, novel_id, created["job"]["id"])
    assert run["candidate"]["gate_verdict"] == "blocked"
    assert run["candidate"]["divergence"] == {}
    target = await _project_chapter(client, headers, ids, fork)

    # Without a kind the override is rejected (the candidate declares no CanonDelta).
    resp = await _create_override(
        client,
        headers,
        novel_id,
        candidate_id=run["candidate"]["id"],
        project_id=target["project"]["id"],
        chapter_id=target["chapter"]["id"],
        evidence=[cite],
    )
    assert resp.status_code == 400, resp.text
    assert "missing_kind" in resp.json()["detail"]

    # A kind without affected evidence is rejected (missing_evidence).
    resp = await _create_override(
        client,
        headers,
        novel_id,
        candidate_id=run["candidate"]["id"],
        project_id=target["project"]["id"],
        chapter_id=target["chapter"]["id"],
        evidence=[],
        kind="timeline",
    )
    assert resp.status_code == 400, resp.text
    assert "missing_evidence" in resp.json()["detail"]

    # With an explicit owner-declared kind the override is accepted.
    resp = await _create_override(
        client,
        headers,
        novel_id,
        candidate_id=run["candidate"]["id"],
        project_id=target["project"]["id"],
        chapter_id=target["chapter"]["id"],
        evidence=[cite],
        kind="timeline",
    )
    assert resp.status_code == 201, resp.text
    override_id = resp.json()["override"]["id"]

    approve = await client.post(
        OVERRIDE_BASE.format(novel_id=novel_id) + f"/{override_id}/approve",
        json={"approval_reason": "owner approves the blocked timeline divergence"},
        headers=headers,
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["published"]["status"] == "derivative_revision"
    assert approve.json()["published"]["approval"]["kind"] == "timeline"
    async with factory() as session:
        override_revision_count = await session.scalar(
            text(
                "SELECT count(*) FROM derivative_revisions "
                "WHERE novel_id = :n AND kind = 'agent_proposal'"
            ),
            {"n": novel_id},
        )
    assert override_revision_count == 1
