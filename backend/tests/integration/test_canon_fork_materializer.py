"""Phase 35-05 materializer + materialize API route integration tests.

Prove the deterministic Fork materializer boundary (D-35-03 / REQ-FORK-01 +
REQ-AGENT-03/04/07):
- Only the approved, validated CanonForkProposal + CanonDeltaArtifact reaches
  ``materialize_approved_fork``; the API materialize route delegates to that
  service and preserves Original Canon and active-pointer immutability.
- Wrong scope, stale base revision/hash, forged/expired approval, validator
  failure, cancellation, wrong owner/novel and schema drift produce stable
  blocked/cancelled outcomes with no fork materialization or Original-authority
  write.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, undefer
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models import Chapter, Novel, User
from app.models.agent_runtime import ApprovalRequest, ArtifactRevision
from app.models.canon_fork import CanonFork
from app.services.agent_runtime.approvals import confirm
from app.services.agent_runtime.finalize import finalize_skill_run
from app.services.agent_runtime.registry import canonical_input_hash
from app.services.agent_runtime.structured_output_integrity import (
    canonical_content_hash,
)
from app.services.agent_tools.facade import ToolFacade
from app.services.canon_fork.materializer import (
    ForkMaterializeError,
    materialize_approved_fork,
)
from app.services.canon_fork.snapshot import (
    ForkChapterRecord,
    compute_source_snapshot_hash,
)
from tests.integration.conftest import reset_public_schema, run_alembic

pytestmark = pytest.mark.integration

CHAPTER_TEXTS = {1: "chapter 1 body", 2: "chapter 2 body", 3: "chapter 3 body"}
DELTA_CONTENT = (
    "Aurora wakes before dawn and walks the southern wall, tracing the light "
    "that Arin described."
)
FORK_KEY = "fork-aurora"
DELTA_KEY = "delta-aurora-01"
HEX64 = "a" * 64


def _async_url(sync_url: str) -> str:
    if sync_url.startswith("postgresql+psycopg2://"):
        return sync_url.replace("postgresql+psycopg2://", "postgresql+asyncpg://", 1)
    return sync_url


def _seed(sync_url: str, *, suffix: str) -> dict[str, Any]:
    engine = create_engine(sync_url, poolclass=NullPool)
    with Session(engine) as session:
        user = User(
            username=f"fk_{suffix}",
            email=f"fk_{suffix}@example.com",
            hashed_password=hash_password("pass12345"),
        )
        session.add(user)
        session.flush()
        novel = Novel(
            title=f"FK Fork Materialize {suffix}",
            owner_id=user.id,
            status="ready",
            reading_progress={},
            chapter_count=len(CHAPTER_TEXTS),
            word_count=sum(len(text) for text in CHAPTER_TEXTS.values()),
        )
        session.add(novel)
        session.flush()
        records: list[ForkChapterRecord] = []
        for number, content in sorted(CHAPTER_TEXTS.items()):
            chapter = Chapter(
                novel_id=novel.id,
                chapter_number=number,
                title=f"C{number}",
                content=content,
                word_count=len(content),
            )
            session.add(chapter)
            session.flush()
            records.append(
                ForkChapterRecord(
                    chapter_id=chapter.id,
                    chapter_number=number,
                    content=content,
                )
            )
        snapshot_hash = compute_source_snapshot_hash(
            owner_id=user.id,
            novel_id=novel.id,
            chapters=tuple(records),
        )
        session.commit()
        data = {
            "owner_id": user.id,
            "novel_id": novel.id,
            "source_snapshot_hash": snapshot_hash,
        }
    engine.dispose()
    return data


def _fork_params(ids: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "branch": None,
        "fork": None,
        "fork_key": FORK_KEY,
        "requested_cutoff_chapter": 2,
        "full_book_requested": False,
        "expected_source_snapshot_hash": ids["source_snapshot_hash"],
        "delta_key": DELTA_KEY,
        "delta_content": DELTA_CONTENT,
        "delta_evidence_refs": ["chapter:1", "chapter:2"],
    }
    base.update(overrides)
    return base


def _build_envelope(
    ctx: dict[str, Any],
    *,
    proposal_status: str = "proposed",
    delta_status: str = "proposed",
    delta_base: str | None = None,
    delta_content_hash: str | None = None,
    delta_content: str = DELTA_CONTENT,
    delta_key: str = DELTA_KEY,
) -> dict[str, Any]:
    common: dict[str, Any] = {
        "owner_id": ctx["owner_id"],
        "novel_id": ctx["novel_id"],
        "branch": None,
        "producing_skill": "create-canon-fork",
        "producing_skill_version": "1.0.0",
        "skill_version_id": ctx["skill_version_id"],
        "model_lineage": {
            "provider": "fixture",
            "model": "stub-model",
            "revision": "stub-1",
        },
        "source_versions": {
            "novel": "v1",
            "source_snapshot_hash": ctx["source_snapshot_hash"],
        },
        "input_hash": ctx["input_hash"],
        "evidence_refs": ["chapter:1", "chapter:2"],
        "tool_runs": [
            {"tool_name": "get_novel", "calls": 1},
            {"tool_name": "create_canon_fork", "calls": 1},
        ],
        "status": "candidate",
        "parent_revision": None,
    }
    proposal: dict[str, Any] = {
        "schema_version": "canon-fork-proposal.v1",
        "artifact_kind": "canon_fork_proposal",
        "fork_key": ctx["fork_key"],
        "branch": None,
        "fork": None,
        "source_version_key": ctx["source_version_key"],
        "source_snapshot_id": ctx["source_snapshot_id"],
        "source_snapshot_hash": ctx["source_snapshot_hash"],
        "through_chapter": ctx["through_chapter"],
        "full_book_authorized": False,
        "cutoff_snapshot_hash": ctx["cutoff_snapshot_hash"],
        "scope_hash": ctx["scope_hash"],
        "manifest_hash": ctx["manifest_hash"],
        "citation_lineage": ctx["citation_lineage"],
        "authorization": ctx["authorization"],
        "proposal_status": proposal_status,
        "approval_request_id": None,
        "fork_id": None,
    }
    delta: dict[str, Any] = {
        "schema_version": "canon-delta.v1",
        "artifact_kind": "canon_delta",
        "delta_key": delta_key,
        "base_revision": delta_base or ctx["manifest_hash"],
        "content": delta_content,
        "content_hash": delta_content_hash
        or hashlib.sha256(delta_content.encode("utf-8")).hexdigest(),
        "evidence_refs": ["chapter:1", "chapter:2"],
        "delta_status": delta_status,
    }
    envelope: dict[str, Any] = {
        "type": "canon_fork_proposal",
        "schema_version": "canon-fork-proposal.v1",
        **common,
        "proposal": proposal,
        "delta": delta,
    }
    repaired_hash = canonical_content_hash(
        {k: v for k, v in envelope.items() if k != "normalization"}
    )
    envelope["normalization"] = {
        "raw_hash": repaired_hash,
        "repaired_hash": repaired_hash,
        "normalization_actions": [],
        "warnings": [],
    }
    return envelope


async def _propose_and_finalize(
    runtime_factory,
    *,
    ctx: dict[str, Any],
    run_id: int,
    delta_key: str = DELTA_KEY,
    envelope_builder: Any = None,
) -> tuple[int, int, int]:
    """Real facade action tool → candidate fork + pending approval → finalize artifact."""
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "create_canon_fork",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_fork_params(ctx, delta_key=delta_key, run_id=run_id),
        )
        await session.commit()
    fork_id = int(tool_view["fork_id"])
    approval_id = int(tool_view["approval_request_id"])

    async with runtime_factory() as session:
        fork = await session.get(CanonFork, fork_id)
        assert fork is not None
        ctx.update(
            {
                "fork_id": fork.id,
                "fork_key": fork.fork_key,
                "source_version_key": fork.source_version_key,
                "source_snapshot_id": fork.source_snapshot_id,
                "source_snapshot_hash": fork.source_snapshot_hash,
                "through_chapter": fork.through_chapter,
                "cutoff_snapshot_hash": fork.cutoff_snapshot_hash,
                "scope_hash": fork.scope_hash,
                "manifest_hash": fork.manifest_hash,
                "citation_lineage": list(fork.citation_lineage or []),
                "authorization": dict(fork.authorization or {}),
            }
        )

    if envelope_builder is None:
        envelope = _build_envelope(ctx, delta_key=delta_key)
    else:
        envelope = envelope_builder(ctx)
    outcome = await finalize_skill_run(
        runtime_factory,
        run_id=run_id,
        stop_reason="stop",
        envelope=envelope,
        model_lineage={
            "provider": "fixture",
            "model": "stub-model",
            "revision": "stub-1",
        },
        source_versions=dict(envelope.get("source_versions") or {}),
        usage={
            "calls": 2,
            "input_tokens": 400,
            "output_tokens": 200,
            "cost_usd": "0.0008",
        },
        frozen_manifest={"evidence_refs": ["chapter:1", "chapter:2"]},
    )
    assert outcome.status == "completed", outcome.status_reason
    assert outcome.artifact_revision_id is not None
    return fork_id, approval_id, int(outcome.artifact_revision_id)


async def _count_forks(factory, *, owner_id: int) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(CanonFork)
                .where(CanonFork.owner_id == owner_id)
            )
            or 0
        )


async def _count_approved(factory, *, owner_id: int) -> int:
    async with factory() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(CanonFork)
                .where(
                    CanonFork.owner_id == owner_id,
                    CanonFork.status == "approved",
                )
            )
            or 0
        )


# ────────────────────────── fixtures ──────────────────────────


@pytest.fixture(scope="module")
def migrated_postgres(pg_sync_url: str, require_postgres: None) -> str:
    reset_public_schema(pg_sync_url)
    run_alembic("upgrade", "head", database_url=pg_sync_url)
    return pg_sync_url


@pytest_asyncio.fixture
async def runtime_factory(migrated_postgres: str):
    engine = create_async_engine(
        _async_url(migrated_postgres), pool_pre_ping=True, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.fixture
async def api_client(migrated_postgres: str):
    aengine = create_async_engine(
        _async_url(migrated_postgres),
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


async def _set_up_run(
    factory, *, ctx: dict[str, Any], branch: str | None = None
) -> tuple[int, str]:
    run_input = {
        "novel_id": ctx["novel_id"],
        "branch": branch,
        "fork_key": FORK_KEY,
        "requested_cutoff_chapter": 2,
        "delta_key": DELTA_KEY,
        "delta_content": DELTA_CONTENT,
        "delta_evidence_refs": ["chapter:1", "chapter:2"],
        "requested_actions": ["create_canon_fork"],
    }
    input_hash = canonical_input_hash(run_input)
    async with factory() as session:
        from app.models.agent_runtime import SkillRun

        run = SkillRun(
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            skill_version_id=ctx["skill_version_id"],
            status="running",
            branch=branch,
            input=run_input,
            input_hash=input_hash,
            frozen_manifest={},
            budget_snapshot={"max_calls": 40},
            cancel_requested=False,
        )
        session.add(run)
        await session.commit()
        return run.id, input_hash


async def _register_skill(factory, *, ctx: dict[str, Any]) -> int:
    from app.schemas.agent_runtime import SkillVersionRegister
    from app.services.agent_runtime.registry import register_skill_version

    contract = SkillVersionRegister.model_validate(
        {
            "novel_id": ctx["novel_id"],
            "name": "create-canon-fork",
            "version": "1.0.0",
            "allowed_tools": [
                "get_novel",
                "get_chapter",
                "search_novel_text",
                "get_timeline",
                "get_relationships",
                "get_clues",
                "get_narrative_memory",
                "create_canon_fork",
            ],
            "read_permissions": ["canon", "canon_fork"],
            "write_permissions": [],
            "forbidden_spaces": [
                "canon:original",
                "canon_fork:write",
                "canon_fork:materialize",
                "approval_request",
                "fork_materializer",
            ],
            "budget": {
                "max_calls": 40,
                "max_input_tokens": 40000,
                "max_output_tokens": 12000,
                "max_cost_usd": "4.00",
            },
            "approval_required_for": ["create_canon_fork"],
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        }
    )
    async with factory() as session:
        _, version = await register_skill_version(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            contract=contract,
        )
        await session.commit()
        return version.id


async def _seed_full(runtime_factory, sync_url: str, *, suffix: str) -> dict[str, Any]:
    ctx = _seed(sync_url, suffix=suffix)
    svid = await _register_skill(runtime_factory, ctx=ctx)
    ctx["skill_version_id"] = svid
    run_id, input_hash = await _set_up_run(runtime_factory, ctx=ctx)
    ctx["run_id"] = run_id
    ctx["input_hash"] = input_hash
    return ctx


# ────────────────────────── 物化器服务测试 ──────────────────────────


async def test_materializer_happy_path_approves_fork(
    runtime_factory, migrated_postgres: str
):
    """Approval + 有效 envelope → materialize_approved_fork 把 fork 物化为 approved；
    active 恒 false、Original 章节未动。"""
    ctx = await _seed_full(
        runtime_factory, migrated_postgres, suffix=f"ok_{uuid.uuid4().hex[:6]}"
    )
    fork_id, approval_id, revision_id = await _propose_and_finalize(
        runtime_factory, ctx=ctx, run_id=ctx["run_id"]
    )
    async with runtime_factory() as session:
        await confirm(
            session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once"
        )
        await session.commit()
        outcome = await materialize_approved_fork(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            fork_id=fork_id,
            approval_request_id=approval_id,
            artifact_revision_id=revision_id,
        )
        await session.commit()
        fork = await session.get(CanonFork, fork_id)
        chapters = (
            await session.scalars(
                select(Chapter)
                .options(undefer(Chapter.content))
                .where(Chapter.novel_id == ctx["novel_id"])
                .order_by(Chapter.chapter_number)
            )
        ).all()
    assert outcome.fork.status == "approved"
    assert len(outcome.materialization_hash) == 64
    assert fork is not None and fork.status == "approved"
    assert fork.active is False
    assert [ch.content for ch in chapters] == list(CHAPTER_TEXTS.values())


async def test_materializer_idempotent_replay(runtime_factory, migrated_postgres: str):
    """已 approved fork 的重复 materialize → replayed=True，同一 materialization_hash。"""
    ctx = await _seed_full(
        runtime_factory, migrated_postgres, suffix=f"idem_{uuid.uuid4().hex[:6]}"
    )
    fork_id, approval_id, revision_id = await _propose_and_finalize(
        runtime_factory, ctx=ctx, run_id=ctx["run_id"]
    )
    async with runtime_factory() as session:
        await confirm(
            session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once"
        )
        await session.commit()
        first = await materialize_approved_fork(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            fork_id=fork_id,
            approval_request_id=approval_id,
            artifact_revision_id=revision_id,
        )
        await session.commit()
        second = await materialize_approved_fork(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            fork_id=fork_id,
            approval_request_id=approval_id,
            artifact_revision_id=revision_id,
        )
        await session.commit()
    assert first.replayed is False
    assert second.replayed is True
    assert first.materialization_hash == second.materialization_hash


async def test_materializer_wrong_owner_blocks(runtime_factory, migrated_postgres: str):
    """foreign owner 无法看到/物化 fork → fork_not_found / approval 越界 fail closed。"""
    ctx = await _seed_full(
        runtime_factory, migrated_postgres, suffix=f"own_{uuid.uuid4().hex[:6]}"
    )
    fork_id, approval_id, revision_id = await _propose_and_finalize(
        runtime_factory, ctx=ctx, run_id=ctx["run_id"]
    )
    foreign = _seed(migrated_postgres, suffix=f"fr_{uuid.uuid4().hex[:6]}")
    with pytest.raises(ForkMaterializeError) as exc:
        async with runtime_factory() as session:
            await materialize_approved_fork(
                session,
                owner_id=foreign["owner_id"],
                novel_id=foreign["novel_id"],
                fork_id=fork_id,
                approval_request_id=approval_id,
                artifact_revision_id=revision_id,
            )
    assert exc.value.code == "fork_not_found"


async def test_materializer_rejected_fork_blocks(
    runtime_factory, migrated_postgres: str
):
    """rejected fork（status=rejected）→ fork_not_materializable，零写入。"""
    ctx = await _seed_full(
        runtime_factory, migrated_postgres, suffix=f"rej_{uuid.uuid4().hex[:6]}"
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        tool_view = await ToolFacade().execute(
            "create_canon_fork",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_fork_params(ctx, delta_key="delta-rej"),
        )
        await session.commit()
    fork_id = int(tool_view["fork_id"])
    async with runtime_factory() as session:
        fork = await session.get(CanonFork, fork_id)
        fork.status = "rejected"  # 唯一可变投影；rejected 后不可物化
        await session.commit()
    ctx.update({"run_id": ctx["run_id"]})
    fork2, approval_id, revision_id = await _propose_and_finalize(
        runtime_factory, ctx=ctx, run_id=ctx["run_id"], delta_key="delta-rej"
    )
    assert fork2 == fork_id
    with pytest.raises(ForkMaterializeError) as exc:
        async with runtime_factory() as session:
            await materialize_approved_fork(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                fork_id=fork_id,
                approval_request_id=approval_id,
                artifact_revision_id=revision_id,
            )
    assert exc.value.code == "fork_not_materializable"


async def test_materializer_pending_approval_blocks(
    runtime_factory, migrated_postgres: str
):
    """Approval 未确认（pending）→ approval_not_approved，fork 保持 candidate。"""
    ctx = await _seed_full(
        runtime_factory, migrated_postgres, suffix=f"pend_{uuid.uuid4().hex[:6]}"
    )
    fork_id, approval_id, revision_id = await _propose_and_finalize(
        runtime_factory, ctx=ctx, run_id=ctx["run_id"], delta_key="delta-pend"
    )
    with pytest.raises(ForkMaterializeError) as exc:
        async with runtime_factory() as session:
            await materialize_approved_fork(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                fork_id=fork_id,
                approval_request_id=approval_id,
                artifact_revision_id=revision_id,
            )
    assert exc.value.code == "approval_not_approved"
    async with runtime_factory() as session:
        fork = await session.get(CanonFork, fork_id)
    assert fork is not None and fork.status == "candidate"


async def test_materializer_approval_action_mismatch_blocks(
    runtime_factory, migrated_postgres: str
):
    """Approval action 非 create_canon_fork（伪造物化动作）→ approval_action_mismatch。"""
    ctx = await _seed_full(
        runtime_factory, migrated_postgres, suffix=f"act_{uuid.uuid4().hex[:6]}"
    )
    fork_id, approval_id, revision_id = await _propose_and_finalize(
        runtime_factory, ctx=ctx, run_id=ctx["run_id"], delta_key="delta-act"
    )
    async with runtime_factory() as session:
        await confirm(
            session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once"
        )
        approval = await session.get(ApprovalRequest, approval_id)
        approval.action = "publish_illustration"  # 伪造 action
        await session.commit()
    with pytest.raises(ForkMaterializeError) as exc:
        async with runtime_factory() as session:
            await materialize_approved_fork(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                fork_id=fork_id,
                approval_request_id=approval_id,
                artifact_revision_id=revision_id,
            )
    assert exc.value.code == "approval_action_mismatch"


async def test_materializer_approval_fork_mismatch_blocks(
    runtime_factory, migrated_postgres: str
):
    """approval 绑定的是另一个 fork（wrong fork）→ approval_fork_mismatch。"""
    ctx = await _seed_full(
        runtime_factory, migrated_postgres, suffix=f"fkm_{uuid.uuid4().hex[:6]}"
    )
    async with runtime_factory() as session:
        novel = await session.get(Novel, ctx["novel_id"])
        facade = ToolFacade()
        first = await facade.execute(
            "create_canon_fork",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_fork_params(ctx, delta_key="delta-fkm"),
        )
        second = await facade.execute(
            "create_canon_fork",
            db=session,
            novel=novel,
            owner_id=ctx["owner_id"],
            params=_fork_params(ctx, fork_key="fork-other", delta_key="delta-fkm2"),
        )
        await session.commit()
    fork_a = int(first["fork_id"])
    approval_a = int(first["approval_request_id"])
    fork_b = int(second["fork_id"])
    assert fork_a != fork_b

    # 为 fork B 构建 envelope + finalize。
    async with runtime_factory() as session:
        fork_b_row = await session.get(CanonFork, fork_b)
        assert fork_b_row is not None
        ctx.update(
            {
                "fork_id": fork_b_row.id,
                "fork_key": fork_b_row.fork_key,
                "source_version_key": fork_b_row.source_version_key,
                "source_snapshot_id": fork_b_row.source_snapshot_id,
                "source_snapshot_hash": fork_b_row.source_snapshot_hash,
                "through_chapter": fork_b_row.through_chapter,
                "cutoff_snapshot_hash": fork_b_row.cutoff_snapshot_hash,
                "scope_hash": fork_b_row.scope_hash,
                "manifest_hash": fork_b_row.manifest_hash,
                "citation_lineage": list(fork_b_row.citation_lineage or []),
                "authorization": dict(fork_b_row.authorization or {}),
            }
        )
    envelope_b = _build_envelope(ctx)
    outcome_b = await finalize_skill_run(
        runtime_factory,
        run_id=ctx["run_id"],
        stop_reason="stop",
        envelope=envelope_b,
        model_lineage={
            "provider": "fixture",
            "model": "stub-model",
            "revision": "stub-1",
        },
        source_versions=dict(envelope_b.get("source_versions") or {}),
        usage={
            "calls": 2,
            "input_tokens": 400,
            "output_tokens": 200,
            "cost_usd": "0.0008",
        },
        frozen_manifest={"evidence_refs": ["chapter:1", "chapter:2"]},
    )
    assert outcome_b.status == "completed", outcome_b.status_reason

    # 确认 fork A 的 approval，但用它物化 fork B → wrong fork。
    async with runtime_factory() as session:
        await confirm(
            session, request_id=approval_a, owner_id=ctx["owner_id"], mode="once"
        )
        await session.commit()
    with pytest.raises(ForkMaterializeError) as exc:
        async with runtime_factory() as session:
            await materialize_approved_fork(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                fork_id=fork_b,
                approval_request_id=approval_a,  # wrong fork approval
                artifact_revision_id=outcome_b.artifact_revision_id,
            )
    assert exc.value.code == "approval_fork_mismatch"


async def test_materializer_tampered_payload_hash_blocks(
    runtime_factory, migrated_postgres: str
):
    """approval payload_hash 被篡改（伪造批准）→ approval_payload_mismatch，零物化。"""
    ctx = await _seed_full(
        runtime_factory, migrated_postgres, suffix=f"hash_{uuid.uuid4().hex[:6]}"
    )
    fork_id, approval_id, revision_id = await _propose_and_finalize(
        runtime_factory, ctx=ctx, run_id=ctx["run_id"], delta_key="delta-hash"
    )
    async with runtime_factory() as session:
        await confirm(
            session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once"
        )
        approval = await session.get(ApprovalRequest, approval_id)
        approval.payload_hash = "c" * 64
        await session.commit()
    with pytest.raises(ForkMaterializeError) as exc:
        async with runtime_factory() as session:
            await materialize_approved_fork(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                fork_id=fork_id,
                approval_request_id=approval_id,
                artifact_revision_id=revision_id,
            )
    assert exc.value.code == "approval_payload_mismatch"
    assert await _count_approved(runtime_factory, owner_id=ctx["owner_id"]) == 0


async def test_materializer_stale_snapshot_blocks(
    runtime_factory, migrated_postgres: str
):
    """proposal 后章节正文变化（source snapshot 不重放）→ stale_source_snapshot。"""
    ctx = await _seed_full(
        runtime_factory, migrated_postgres, suffix=f"snap_{uuid.uuid4().hex[:6]}"
    )
    fork_id, approval_id, revision_id = await _propose_and_finalize(
        runtime_factory, ctx=ctx, run_id=ctx["run_id"], delta_key="delta-snap"
    )
    async with runtime_factory() as session:
        chapter = await session.scalar(
            select(Chapter).where(
                Chapter.novel_id == ctx["novel_id"], Chapter.chapter_number == 1
            )
        )
        chapter.content = "chapter 1 body CHANGED"
        await session.commit()
        await confirm(
            session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once"
        )
        await session.commit()
    with pytest.raises(ForkMaterializeError) as exc:
        async with runtime_factory() as session:
            await materialize_approved_fork(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                fork_id=fork_id,
                approval_request_id=approval_id,
                artifact_revision_id=revision_id,
            )
    assert exc.value.code == "stale_source_snapshot"
    assert await _count_approved(runtime_factory, owner_id=ctx["owner_id"]) == 0


async def test_materializer_stale_base_revision_blocks(
    runtime_factory, migrated_postgres: str
):
    """delta.base_revision 与 fork manifest 不符（stale base）→ delta_base_mismatch。"""
    ctx = await _seed_full(
        runtime_factory, migrated_postgres, suffix=f"base_{uuid.uuid4().hex[:6]}"
    )

    def _stale_envelope(c: dict[str, Any]) -> dict[str, Any]:
        return _build_envelope(c, delta_base="b" * 64)

    fork_id, approval_id, revision_id = await _propose_and_finalize(
        runtime_factory,
        ctx=ctx,
        run_id=ctx["run_id"],
        delta_key="delta-base",
        envelope_builder=_stale_envelope,
    )
    async with runtime_factory() as session:
        await confirm(
            session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once"
        )
        await session.commit()
    with pytest.raises(ForkMaterializeError) as exc:
        async with runtime_factory() as session:
            await materialize_approved_fork(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                fork_id=fork_id,
                approval_request_id=approval_id,
                artifact_revision_id=revision_id,
            )
    assert exc.value.code == "delta_base_mismatch"
    assert await _count_approved(runtime_factory, owner_id=ctx["owner_id"]) == 0


async def test_materializer_schema_drift_blocks(
    runtime_factory, migrated_postgres: str
):
    """schema drift：artifact revision 内容不是合法 CanonForkProposal 信封 → 409。"""
    ctx = await _seed_full(
        runtime_factory, migrated_postgres, suffix=f"drift_{uuid.uuid4().hex[:6]}"
    )
    fork_id, approval_id, revision_id = await _propose_and_finalize(
        runtime_factory, ctx=ctx, run_id=ctx["run_id"], delta_key="delta-drift"
    )
    async with runtime_factory() as session:
        await confirm(
            session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once"
        )
        revision = await session.get(ArtifactRevision, revision_id)
        revision.content = {"type": "story_arc", "schema_version": "story-arc.v1"}
        await session.commit()
    with pytest.raises(ForkMaterializeError) as exc:
        async with runtime_factory() as session:
            await materialize_approved_fork(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                fork_id=fork_id,
                approval_request_id=approval_id,
                artifact_revision_id=revision_id,
            )
    assert exc.value.code == "schema_drift"
    assert await _count_approved(runtime_factory, owner_id=ctx["owner_id"]) == 0


# ────────────────────────── 物化 API 路由（委托边界） ──────────────────────────


async def test_materialize_api_route_delegates_and_preserves_original(
    api_client, migrated_postgres: str
):
    """POST /novels/{novel_id}/canon-fork/{fork_id}/materialize 委托 materializer：
    approved fork 返回 materialization_status=approved；active 恒 false；Original
    正文未动；未批准的 fork → 409 fail closed。"""
    client, factory, sync_url = api_client
    suffix = uuid.uuid4().hex[:6]
    ctx = _seed(sync_url, suffix=f"api_{suffix}")
    async with factory() as session:
        user = await session.get(User, ctx["owner_id"])
        token = create_access_token({"sub": str(user.id)})
    headers = {"Authorization": f"Bearer {token}"}

    # 注册技能 + 接受 run + action 工具创建候选 fork + approval。
    ctx["skill_version_id"] = await _register_skill(factory, ctx=ctx)
    run_id, input_hash = await _set_up_run(factory, ctx=ctx)
    ctx["run_id"] = run_id
    ctx["input_hash"] = input_hash
    fork_id, approval_id, revision_id = await _propose_and_finalize(
        factory, ctx=ctx, run_id=run_id, delta_key="delta-api"
    )

    # 未批准 → 409。
    resp = await client.post(
        f"/api/novels/{ctx['novel_id']}/canon-fork/{fork_id}/materialize",
        json={"approval_request_id": approval_id, "artifact_revision_id": revision_id},
        headers=headers,
    )
    assert resp.status_code == 409
    assert "approval_not_approved" in resp.json()["detail"]

    # 批准后 → 201/200 物化。
    async with factory() as session:
        await confirm(
            session, request_id=approval_id, owner_id=ctx["owner_id"], mode="once"
        )
        await session.commit()
    resp = await client.post(
        f"/api/novels/{ctx['novel_id']}/canon-fork/{fork_id}/materialize",
        json={"approval_request_id": approval_id, "artifact_revision_id": revision_id},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["materialization_status"] == "approved"
    assert body["fork"]["status"] == "approved"
    assert body["fork"]["active"] is False
    assert len(body["materialization_hash"]) == 64

    # Original 正文未动；active pointer 恒 false。
    async with factory() as session:
        chapters = (
            await session.scalars(
                select(Chapter)
                .options(undefer(Chapter.content))
                .where(Chapter.novel_id == ctx["novel_id"])
                .order_by(Chapter.chapter_number)
            )
        ).all()
        fork = await session.get(CanonFork, fork_id)
    assert [ch.content for ch in chapters] == list(CHAPTER_TEXTS.values())
    assert fork is not None and fork.active is False
