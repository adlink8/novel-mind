"""Phase 37-02 derivative generation job PostgreSQL API tests (REQ-FORK-03/REQ-CRE-06).

Covers the full constrained candidate-generation surface on the real CI DB:

- create a job for an owned sealed package: frozen package hash, intent,
  prompt/schema/config hashes and idempotency key; duplicate idempotency key
  replays the same job (one charge, one candidate);
- run the job through a deterministic fake gateway: strict-schema candidate,
  gate verdict ``candidate``, usage/cost/budget lineage persisted;
- schema-invalid provider output blocks (``schema_invalid``) with no publish;
- evidence outside the package blocks (``evidence_outside_package``) with the
  candidate lineage kept; explicit divergence yields ``needs_override``;
- budget overrun pauses the job before any provider call and is recoverable;
- terminal jobs are never silently re-called; cancel prevents the call;
- cross-fork package is an identical 404; intent mismatch is a 409;
- provider output never writes Original Canon chapters, revisions or active
  pointers (D-37-02 forbidden publish path).
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

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
from app.models.derivative_generation_job import (
    DerivativeGenerationCandidate,
    DerivativeGenerationJob,
)
from app.models.novel import Chapter, Novel
from app.models.user import User
from app.services.derivative_generation.runner import (
    DEFAULT_DERIVATIVE_BUDGET,
    DerivativeBudgetGate,
    DerivativeBudgetPolicy,
)
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

PACKAGE_BASE = "/api/novels/{novel_id}/derivative-context-packages"
JOB_BASE = "/api/novels/{novel_id}/derivative-generation-jobs"
FORK_BASE = "/api/novels/{novel_id}/canon-fork"
HEX64 = "a" * 64


# ---------------------------------------------------------------------------
# Deterministic fake gateway (replayable; injected via dependency overrides)
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
    draft="阿宁走向竹林深处。",
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


def _divergence_payload():
    return {
        "divergence_type": "character",
        "reason": "hero acts out of character for the twist",
        "affected_evidence": [],
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
            username=f"dgj_{suffix}",
            email=f"dgj_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
            is_superuser=False,
        )
        session.add(user)
        session.flush()
        novel = Novel(
            title=f"DGJ Novel {suffix}",
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
    """One passed, cutoff-visible entity/rule at version_id=1.

    The idempotency key is unique per call because the world-model tables carry
    a global unique idempotency constraint; the hash fields stay HEX64.
    """
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


async def _create_job(
    client, headers, novel_id, package_id, intent="continuation", job_key=None
) -> dict:
    resp = await client.post(
        JOB_BASE.format(novel_id=novel_id),
        json={
            "context_package_id": package_id,
            "intent": intent,
            "job_key": job_key or f"job-{uuid.uuid4().hex[:8]}",
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


def _setup(ids, suffix):
    gateway.responses = []
    gateway.calls = []
    current_budget_gate.__init__(DEFAULT_DERIVATIVE_BUDGET)
    return suffix


# ---------------------------------------------------------------------------
# Happy path: sealed package -> candidate (no original write)
# ---------------------------------------------------------------------------


async def test_create_and_run_produces_candidate_only(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"happy_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-happy")
    _seed_world_model(sync_url, owner_id=ids["owner_id"], novel_id=novel_id)
    pkg = await _compile(client, headers, novel_id, fork["id"], intent="continuation")
    pkg_view = pkg["package"]
    cite = _first_evidence_key(pkg_view)

    _setup(ids, "happy")
    gateway.responses = [
        {
            "content": _candidate_json(citations=[cite]),
            "usage": {"input_tokens": 120, "output_tokens": 60},
            "id": "req-happy",
        }
    ]
    created = await _create_job(
        client,
        headers,
        novel_id,
        pkg_view["id"],
        intent="continuation",
        job_key="happy-key",
    )
    job = created["job"]
    assert job["status"] == "queued"
    assert job["package_hash"] == pkg_view["package_hash"]
    assert len(job["idempotency_key"]) == 64
    assert len(job["prompt_hash"]) == 64 and len(job["schema_hash"]) == 64
    assert job["budget_policy"]["max_cost_usd"]

    run = await _run_job(client, headers, novel_id, job["id"])
    assert run["job"]["status"] == "succeeded"
    candidate = run["candidate"]
    assert candidate is not None
    assert candidate["gate_verdict"] == "candidate"
    assert candidate["draft_text"] == "阿宁走向竹林深处。"
    assert candidate["citation_keys"] == [cite]
    assert candidate["package_hash"] == pkg_view["package_hash"]
    assert candidate["usage"]["input_tokens"] == 120
    assert candidate["cost_usd"] is not None
    assert candidate["approval_state"] == "candidate"
    attempt = run["attempts"][0]
    assert attempt["status"] == "succeeded"
    assert attempt["reserved_input_tokens"] >= 1
    assert attempt["usage"]["output_tokens"] == 60
    assert len(gateway.calls) == 1

    # REQ-FORK-03: Original chapters unchanged; no revisions/pointers created.
    async with factory() as session:
        before = list(
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
        revision_count = await session.scalar(
            text("SELECT count(*) FROM derivative_revisions WHERE novel_id = :n"),
            {"n": novel_id},
        )
        fork_active = await session.scalar(
            text("SELECT active FROM canon_forks WHERE id = :f"), {"f": fork["id"]}
        )
        job_row = await session.get(DerivativeGenerationJob, job["id"])
    assert before == [
        (1, "chapter 1 body"),
        (2, "chapter 2 body"),
        (3, "chapter 3 body"),
    ]
    assert revision_count == 0
    assert fork_active is False
    assert job_row is not None and job_row.response_hash is not None


async def test_duplicate_idempotency_key_replays_job(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"idem_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-idem")
    _seed_world_model(sync_url, owner_id=ids["owner_id"], novel_id=novel_id)
    pkg = await _compile(client, headers, novel_id, fork["id"])
    pkg_view = pkg["package"]
    cite = _first_evidence_key(pkg_view)

    _setup(ids, "idem")
    gateway.responses = [
        {
            "content": _candidate_json(citations=[cite]),
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
        # The second create must replay; it must NOT make a provider call.
    ]
    created = await _create_job(
        client, headers, novel_id, pkg_view["id"], job_key="dup"
    )
    replayed = await _create_job(
        client, headers, novel_id, pkg_view["id"], job_key="dup"
    )
    assert replayed["replayed"] is True
    assert replayed["job"]["id"] == created["job"]["id"]
    assert len(gateway.calls) == 0  # no provider call during creation
    run = await _run_job(client, headers, novel_id, created["job"]["id"])
    assert run["job"]["status"] == "succeeded"
    async with factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(DerivativeGenerationJob)
            .where(
                DerivativeGenerationJob.idempotency_key
                == created["job"]["idempotency_key"]
            )
        )
        candidate_count = await session.scalar(
            select(func.count())
            .select_from(DerivativeGenerationCandidate)
            .where(DerivativeGenerationCandidate.job_id == created["job"]["id"])
        )
    assert count == 1
    assert candidate_count == 1
    assert len(gateway.calls) == 1


async def test_run_is_candidate_only_recovery_on_paused_job(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"recover_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-recover")
    _seed_world_model(sync_url, owner_id=ids["owner_id"], novel_id=novel_id)
    pkg = await _compile(client, headers, novel_id, fork["id"])
    pkg_view = pkg["package"]
    cite = _first_evidence_key(pkg_view)

    _setup(ids, "recover")
    created = await _create_job(
        client, headers, novel_id, pkg_view["id"], job_key="recover-key"
    )
    job_id = created["job"]["id"]
    # Budget-exhausted run: no provider call.
    current_budget_gate.__init__(
        DerivativeBudgetPolicy(1, 1, 1, Decimal("0.0000000001"))
    )
    gateway.responses = [{"content": _candidate_json(citations=[cite]), "usage": {}}]
    resp = await client.post(
        JOB_BASE.format(novel_id=novel_id) + f"/{job_id}/run", headers=headers
    )
    assert resp.status_code == 200, resp.text
    paused = resp.json()
    assert paused["job"]["status"] == "paused_budget"
    assert paused["job"]["error_code"] == "budget_exhausted"
    assert gateway.calls == []
    assert paused["candidate"] is None
    # Recover with a healthy gate: the same job succeeds.
    current_budget_gate.__init__(DEFAULT_DERIVATIVE_BUDGET)
    gateway.responses = [
        {
            "content": _candidate_json(citations=[cite]),
            "usage": {"input_tokens": 9, "output_tokens": 4},
        }
    ]
    run = await _run_job(client, headers, novel_id, job_id)
    assert run["job"]["status"] == "succeeded"
    assert run["candidate"]["gate_verdict"] == "candidate"
    assert len(gateway.calls) == 1


async def test_schema_invalid_blocks_and_never_publishes(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"schema_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-schema")
    _seed_world_model(sync_url, owner_id=ids["owner_id"], novel_id=novel_id)
    pkg = await _compile(client, headers, novel_id, fork["id"])
    pkg_view = pkg["package"]
    _setup(ids, "schema")
    created = await _create_job(
        client, headers, novel_id, pkg_view["id"], job_key="schema-key"
    )
    gateway.responses = [
        {"content": "not json", "usage": {"input_tokens": 4, "output_tokens": 1}}
    ]
    run = await _run_job(client, headers, novel_id, created["job"]["id"])
    assert run["job"]["status"] == "blocked"
    assert run["job"]["error_code"] == "schema_invalid"
    assert run["candidate"] is None
    assert run["attempts"][0]["status"] == "failed"
    assert run["attempts"][0]["error_code"] == "schema_invalid"
    # A terminal blocked job is never silently re-called.
    calls_before = len(gateway.calls)
    gateway.responses = [
        {
            "content": _candidate_json(citations=[_first_evidence_key(pkg_view)]),
            "usage": {},
        }
    ]
    resp = await client.post(
        JOB_BASE.format(novel_id=novel_id) + f"/{created['job']['id']}/run",
        headers=headers,
    )
    assert resp.status_code == 409
    assert "job_not_runnable" in resp.json()["detail"]
    assert len(gateway.calls) == calls_before  # re-run did not reach the provider


async def test_evidence_outside_package_blocks_with_lineage(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"ev_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-ev")
    _seed_world_model(sync_url, owner_id=ids["owner_id"], novel_id=novel_id)
    pkg = await _compile(client, headers, novel_id, fork["id"])
    pkg_view = pkg["package"]
    _setup(ids, "ev")
    created = await _create_job(
        client, headers, novel_id, pkg_view["id"], job_key="ev-key"
    )
    gateway.responses = [
        {
            "content": _candidate_json(citations=["fork:ff-ev:chapter:999"]),
            "usage": {"input_tokens": 5, "output_tokens": 2},
        }
    ]
    run = await _run_job(client, headers, novel_id, created["job"]["id"])
    assert run["job"]["status"] == "blocked"
    assert run["job"]["error_code"] == "evidence_outside_package"
    assert run["candidate"]["gate_verdict"] == "blocked"
    assert run["candidate"]["gate_reason"] == "evidence_outside_package"
    assert run["candidate"]["citation_keys"] == ["fork:ff-ev:chapter:999"]


async def test_divergence_yields_needs_override(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"div_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-div")
    _seed_world_model(sync_url, owner_id=ids["owner_id"], novel_id=novel_id)
    pkg = await _compile(client, headers, novel_id, fork["id"])
    pkg_view = pkg["package"]
    cite = _first_evidence_key(pkg_view)
    divergence = _divergence_payload()
    divergence["affected_evidence"] = [cite]
    _setup(ids, "div")
    created = await _create_job(
        client, headers, novel_id, pkg_view["id"], job_key="div-key"
    )
    gateway.responses = [
        {
            "content": _candidate_json(citations=[cite], divergence=divergence),
            "usage": {"input_tokens": 5, "output_tokens": 2},
        }
    ]
    run = await _run_job(client, headers, novel_id, created["job"]["id"])
    assert run["job"]["status"] == "needs_override"
    assert run["job"]["error_code"] == "divergence_requires_override"
    candidate = run["candidate"]
    assert candidate["gate_verdict"] == "needs_override"
    assert candidate["approval_state"] == "needs_override"
    assert candidate["divergence"]["divergence_type"] == "character"
    assert candidate["canon_delta_hash"] is not None


async def test_cancel_prevents_provider_call(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"cancel_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-cancel")
    _seed_world_model(sync_url, owner_id=ids["owner_id"], novel_id=novel_id)
    pkg = await _compile(client, headers, novel_id, fork["id"])
    pkg_view = pkg["package"]
    _setup(ids, "cancel")
    created = await _create_job(
        client, headers, novel_id, pkg_view["id"], job_key="cancel-key"
    )
    job_id = created["job"]["id"]
    cancel_resp = await client.post(
        JOB_BASE.format(novel_id=novel_id) + f"/{job_id}/cancel", headers=headers
    )
    assert cancel_resp.status_code == 200, cancel_resp.text
    assert cancel_resp.json()["job"]["status"] == "cancelled"
    gateway.responses = [
        {
            "content": _candidate_json(citations=[_first_evidence_key(pkg_view)]),
            "usage": {},
        }
    ]
    run_resp = await client.post(
        JOB_BASE.format(novel_id=novel_id) + f"/{job_id}/run", headers=headers
    )
    assert run_resp.status_code == 409
    assert "job_not_runnable" in run_resp.json()["detail"]
    assert gateway.calls == []


async def test_cross_fork_package_is_identical_404(api_client):
    client, factory, sync_url = api_client
    ids_a = _seed_owner(sync_url, suffix=f"forka_{uuid.uuid4().hex[:8]}")
    ids_b = _seed_owner(sync_url, suffix=f"forkb_{uuid.uuid4().hex[:8]}")
    headers_a = {"Authorization": f"Bearer {ids_a['token']}"}
    headers_b = {"Authorization": f"Bearer {ids_b['token']}"}
    novel_a = ids_a["novel_id"]
    fork = await _create_fork(client, headers_a, novel_a, "ff-forka")
    _seed_world_model(sync_url, owner_id=ids_a["owner_id"], novel_id=novel_a)
    pkg = await _compile(client, headers_a, novel_a, fork["id"])
    pkg_view = pkg["package"]
    _setup(ids_a, "forka")
    # Owner B tries to generate from owner A's sealed package.
    resp = await client.post(
        JOB_BASE.format(novel_id=ids_b["novel_id"]),
        json={
            "context_package_id": pkg_view["id"],
            "intent": "continuation",
            "job_key": "foreign",
        },
        headers=headers_b,
    )
    assert resp.status_code == 404
    assert "package_not_found" in resp.json()["detail"]
    # No provider call happened.
    assert gateway.calls == []


async def test_intent_mismatch_is_conflict(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"intent_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-intent")
    _seed_world_model(sync_url, owner_id=ids["owner_id"], novel_id=novel_id)
    pkg = await _compile(client, headers, novel_id, fork["id"], intent="rewrite")
    pkg_view = pkg["package"]
    _setup(ids, "intent")
    resp = await client.post(
        JOB_BASE.format(novel_id=novel_id),
        json={
            "context_package_id": pkg_view["id"],
            "intent": "continuation",
            "job_key": "wrong-intent",
        },
        headers=headers,
    )
    assert resp.status_code == 409
    assert "intent_mismatch" in resp.json()["detail"]


async def test_list_and_detail_read_back_lineage(api_client):
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"list_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-list")
    _seed_world_model(sync_url, owner_id=ids["owner_id"], novel_id=novel_id)
    pkg = await _compile(client, headers, novel_id, fork["id"])
    pkg_view = pkg["package"]
    cite = _first_evidence_key(pkg_view)
    _setup(ids, "list")
    created = await _create_job(
        client, headers, novel_id, pkg_view["id"], job_key="list-key"
    )
    gateway.responses = [
        {
            "content": _candidate_json(citations=[cite]),
            "usage": {"input_tokens": 20, "output_tokens": 10},
        }
    ]
    await _run_job(client, headers, novel_id, created["job"]["id"])
    list_resp = await client.get(JOB_BASE.format(novel_id=novel_id), headers=headers)
    assert list_resp.status_code == 200
    items = list_resp.json()["items"]
    assert len(items) == 1
    assert items[0]["status"] == "succeeded"
    detail_resp = await client.get(
        JOB_BASE.format(novel_id=novel_id) + f"/{created['job']['id']}", headers=headers
    )
    detail = detail_resp.json()
    assert detail["candidate"]["gate_verdict"] == "candidate"
    assert detail["attempts"][0]["status"] == "succeeded"
    assert detail["job"]["retry_count"] == 1


async def test_fake_gateway_replay_is_identical(api_client):
    """Two identical fake responses produce identical candidate lineage."""
    client, factory, sync_url = api_client
    ids = _seed_owner(sync_url, suffix=f"replay_{uuid.uuid4().hex[:8]}")
    headers = {"Authorization": f"Bearer {ids['token']}"}
    novel_id = ids["novel_id"]
    fork = await _create_fork(client, headers, novel_id, "ff-replay")
    _seed_world_model(sync_url, owner_id=ids["owner_id"], novel_id=novel_id)
    pkg = await _compile(client, headers, novel_id, fork["id"])
    pkg_view = pkg["package"]
    cite = _first_evidence_key(pkg_view)
    response = {
        "content": _candidate_json(citations=[cite]),
        "usage": {"input_tokens": 7, "output_tokens": 3},
        "id": "req-replay",
    }
    _setup(ids, "replay")
    job_a = await _create_job(
        client, headers, novel_id, pkg_view["id"], job_key="replay-a"
    )
    job_b = await _create_job(
        client, headers, novel_id, pkg_view["id"], job_key="replay-b"
    )
    gateway.responses = [dict(response)]
    run_a = await _run_job(client, headers, novel_id, job_a["job"]["id"])
    gateway.responses = [dict(response)]
    run_b = await _run_job(client, headers, novel_id, job_b["job"]["id"])
    assert run_a["candidate"]["response_hash"] == run_b["candidate"]["response_hash"]
    assert run_a["candidate"]["draft_text"] == run_b["candidate"]["draft_text"]
    assert run_a["attempts"][0]["usage"] == run_b["attempts"][0]["usage"]
