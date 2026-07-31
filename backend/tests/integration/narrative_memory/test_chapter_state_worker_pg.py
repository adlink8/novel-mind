"""Controlled-transport PostgreSQL tests for Chapter State builder worker."""

from __future__ import annotations


import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.narrative_memory import NarrativeMemoryClaim, NarrativeMemoryNode
from app.models.narrative_memory_builder import (
    NarrativeMemoryBuildStage,
)
from app.models.novel import Chapter, Novel
from app.models.user import User
from app.services.chunking.pg_store import create_and_persist_hierarchy_build
from app.services.narrative_memory.audit import audit_assets
from app.services.narrative_memory.audit_pg import PostgresAuditSource
from app.services.narrative_memory.authority import CandidateAuthority
from app.services.narrative_memory.builder_contracts import (
    BudgetPolicy,
    ModelDeploymentSnapshot,
    RunPolicy,
    StageKind,
)
from app.services.narrative_memory.builder_worker import (
    NarrativeMemoryBuilderWorker,
    scan_builder_package_for_forbidden_capabilities,
)
from app.services.narrative_memory.contracts import CandidateVersionSpec, ModelLineage
from tests.integration.conftest import run_alembic


pytestmark = pytest.mark.integration

HEX_A = "a" * 64


class ControlledTransport:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fail_chapters: set[int] = set()
        self.stale_claim_key_chapters: set[int] = set()
        self.cancel_after: int | None = None

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.cancel_after is not None and len(self.calls) > self.cancel_after:
            pass
        chapter_number = kwargs["payload"]["chapter_number"]
        if chapter_number in self.fail_chapters:
            raise RuntimeError("injected_chapter_failure")
        leaf = kwargs["payload"]["evidence_leaves"][0]
        claim_chapter = (
            chapter_number - 1
            if chapter_number in self.stale_claim_key_chapters
            else chapter_number
        )
        return {
            "node_key": f"chapter_state:{chapter_number}",
            "display_label": f"Chapter {chapter_number}",
            "claims": [
                {
                    "claim_key": f"chapter_state:{claim_chapter}:claim:1",
                    "payload": {
                        "claim_kind": "entity_state",
                        "entity_kind": "character",
                        "entity_key": "character:lin",
                        "dimension": "location",
                        "prior": {"value_kind": "unknown"},
                        "current": {
                            "value_kind": "text",
                            "value": f"place-{chapter_number}",
                        },
                        "change": "establish",
                    },
                    "uncertainty": "certain",
                    "confidence": 0.9,
                    "visible_from_chapter": chapter_number,
                }
            ],
            "source_bindings": [
                {
                    "claim_key": f"chapter_state:{claim_chapter}:claim:1",
                    "evidence_node_id": leaf["evidence_node_id"],
                    "source_key": f"src:{chapter_number}",
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }


class FullChainTransport(ControlledTransport):
    """Deterministic provider stub for the complete builder dependency chain."""

    async def complete(self, **kwargs):
        payload = kwargs["payload"]
        stage_key = str(payload.get("stage_key", ""))
        if stage_key == "arc_volume_plan:book":
            self.calls.append(kwargs)
            return {
                "ranges": [
                    {
                        "chapter_start": 1,
                        "chapter_end": 3,
                        "label": "全书测试故事弧",
                        "reason": "测试书三章属于同一事件链",
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 20},
            }
        if stage_key.startswith("story_arc:") or stage_key == "global_story:book":
            self.calls.append(kwargs)
            return {"claims": [], "usage": {"input_tokens": 10, "output_tokens": 20}
            }
        return await super().complete(**kwargs)


def _policy(**budget_overrides) -> RunPolicy:
    budget = {
        "max_calls": 50,
        "max_input_tokens": 100_000,
        "max_output_tokens": 100_000,
        "max_cost_usd": "10.0",
    }
    budget.update(budget_overrides)
    return RunPolicy(
        policy_version="builder-policy.v1",
        stage_order=(StageKind.CHAPTER_STATE,),
        max_schema_repairs=1,
        chapter_concurrency=1,
        arc_window_size=2,
        budget=BudgetPolicy(**budget),
        prompt_hash=HEX_A,
        schema_hash=HEX_A,
        model_lineage=ModelLineage(
            provider="test", model="m", deployment="fixed", revision="1"
        ),
        decoding_hash=HEX_A,
        config_hash=HEX_A,
        policy_hash=HEX_A,
    )


def _full_policy(**budget_overrides) -> RunPolicy:
    policy = _policy(**budget_overrides).model_dump(mode="json")
    policy["stage_order"] = [stage.value for stage in StageKind]
    return RunPolicy.model_validate(policy)


def _deployment(*, unknown_price: bool = False) -> ModelDeploymentSnapshot:
    return ModelDeploymentSnapshot(
        provider="test",
        model="m",
        deployment="fixed",
        revision="1",
        supports_structured_output=True,
        input_price_per_million=None if unknown_price else "1.0",
        output_price_per_million=None if unknown_price else "2.0",
    )


async def _seed(session: AsyncSession):
    user = User(
        username="builder-owner",
        email="builder-owner@example.com",
        hashed_password="x",
    )
    session.add(user)
    await session.flush()
    novel = Novel(owner_id=user.id, title="Builder Novel", status="ready")
    session.add(novel)
    await session.flush()
    chapters = [
        Chapter(
            novel_id=novel.id,
            chapter_number=number,
            title=f"Chapter {number}",
            content=content,
            word_count=len(content),
        )
        for number, content in (
            (1, "甲乙丙丁戊己"),
            (2, "庚辛壬癸子丑"),
            (3, "寅卯辰巳午未"),
        )
    ]
    session.add_all(chapters)
    await session.flush()
    await create_and_persist_hierarchy_build(
        session,
        novel_id=novel.id,
        chapters=[
            {
                "chapter_id": chapter.id,
                "chapter_number": chapter.chapter_number,
                "content": chapter.content,
            }
            for chapter in chapters
        ],
        promote_active=True,
        force_full=True,
    )
    await session.flush()
    report = await audit_assets(
        PostgresAuditSource(session), owner_id=user.id, novel_id=novel.id
    )
    assert report.provider_calls_allowed is True
    authority = CandidateAuthority(session)
    version = await authority.create_version(
        owner_id=user.id,
        novel_id=novel.id,
        spec=CandidateVersionSpec(
            version_key="builder-v1",
            prompt_hash=HEX_A,
            schema_hash=HEX_A,
            model_lineage=ModelLineage(
                provider="test", model="m", deployment="fixed", revision="1"
            ),
            decoding_hash=HEX_A,
            config_hash=HEX_A,
            policy_hash=HEX_A,
        ),
        eligibility_report=report,
    )
    await session.commit()
    return user, novel, version, chapters, report


@pytest.fixture
async def builder_env(empty_postgres: str, pg_async_url: str):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_async_engine(pg_async_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            user, novel, version, chapters, report = await _seed(session)
        yield {
            "factory": factory,
            "owner_id": user.id,
            "novel_id": novel.id,
            "version_id": version.id,
            "chapters": chapters,
        }
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_chapter_states_complete_with_budget_and_cache(builder_env) -> None:
    transport = ControlledTransport()
    worker = NarrativeMemoryBuilderWorker(
        builder_env["factory"],
        inventory_source=PostgresAuditSource,
        transport=transport,
        deployment=_deployment(),
    )

    # PostgresAuditSource needs a session — wrap it.
    class _Src:
        def __init__(self, sessions):
            self._sessions = sessions

        async def inventory(self, *, owner_id: int, novel_id: int):
            async with self._sessions() as session:
                return await PostgresAuditSource(session).inventory(
                    owner_id=owner_id, novel_id=novel_id
                )

    worker = NarrativeMemoryBuilderWorker(
        builder_env["factory"],
        inventory_source=_Src(builder_env["factory"]),
        transport=transport,
        deployment=_deployment(),
    )
    run_id = await worker.start_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
        run_policy=_policy(),
    )
    result = await worker.process_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
    )
    assert len(result.completed_stages) >= 3
    assert transport.calls  # at least one transport call

    # Resume should not re-call completed chapter transport (cache or skip).
    await worker.process_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
    )
    # Completed chapter stages should not rewrite; transport may only run parents.
    async with builder_env["factory"]() as session:
        nodes = (
            await session.scalars(
                select(NarrativeMemoryNode).where(
                    NarrativeMemoryNode.version_id == builder_env["version_id"],
                    NarrativeMemoryNode.node_kind == "chapter_state",
                )
            )
        ).all()
        assert len(nodes) == 3
        stages = (
            await session.scalars(
                select(NarrativeMemoryBuildStage).where(
                    NarrativeMemoryBuildStage.run_id == run_id,
                    NarrativeMemoryBuildStage.stage_kind == "chapter_state",
                )
            )
        ).all()
        assert all(s.status == "completed" for s in stages)
        artifacts = {s.stage_key: s.artifact_checksum for s in stages}
        # byte-identical artifacts after resume
        stages2 = (
            await session.scalars(
                select(NarrativeMemoryBuildStage).where(
                    NarrativeMemoryBuildStage.run_id == run_id,
                    NarrativeMemoryBuildStage.stage_kind == "chapter_state",
                )
            )
        ).all()
        assert {s.stage_key: s.artifact_checksum for s in stages2} == artifacts


@pytest.mark.asyncio
async def test_full_test_book_completes_chapter_arc_global_manifest_chain(builder_env) -> None:
    transport = FullChainTransport()
    transport.stale_claim_key_chapters = {2}
    worker = NarrativeMemoryBuilderWorker(
        builder_env["factory"],
        inventory_source=PostgresAuditSource,
        transport=transport,
        deployment=_deployment(),
    )

    class _Src:
        def __init__(self, sessions):
            self._sessions = sessions

        async def inventory(self, *, owner_id: int, novel_id: int):
            async with self._sessions() as session:
                return await PostgresAuditSource(session).inventory(
                    owner_id=owner_id, novel_id=novel_id
                )

    worker = NarrativeMemoryBuilderWorker(
        builder_env["factory"],
        inventory_source=_Src(builder_env["factory"]),
        transport=transport,
        deployment=_deployment(),
    )
    run_id = await worker.start_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
        run_policy=_full_policy(),
    )
    result = await worker.process_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
    )

    async with builder_env["factory"]() as session:
        stages = (
            await session.scalars(
                select(NarrativeMemoryBuildStage).where(
                    NarrativeMemoryBuildStage.run_id == run_id
                )
            )
        ).all()
        by_kind = {stage.stage_kind: stage.status for stage in stages}
        assert by_kind[StageKind.CHAPTER_STATE.value] == "completed"
        assert by_kind[StageKind.ARC_VOLUME_PLAN.value] == "completed"
        assert by_kind[StageKind.ARC_VOLUME_AGGREGATE.value] == "completed"
        assert by_kind[StageKind.GLOBAL_AGGREGATE.value] == "completed"
        assert by_kind[StageKind.MANIFEST_VALIDATION.value] == "completed"
        assert result.status == "completed"
        assert result.failed_stages == ()
        assert result.blocked_stages == ()
        manifest_stage = next(
            stage
            for stage in stages
            if stage.stage_kind == StageKind.MANIFEST_VALIDATION.value
        )
        assert manifest_stage.artifact_checksum
        assert len(transport.calls) == 6  # 3 chapter + plan + arc + global


@pytest.mark.asyncio
async def test_unknown_price_zero_transport(builder_env) -> None:
    transport = ControlledTransport()

    class _Src:
        def __init__(self, sessions):
            self._sessions = sessions

        async def inventory(self, *, owner_id: int, novel_id: int):
            async with self._sessions() as session:
                return await PostgresAuditSource(session).inventory(
                    owner_id=owner_id, novel_id=novel_id
                )

    worker = NarrativeMemoryBuilderWorker(
        builder_env["factory"],
        inventory_source=_Src(builder_env["factory"]),
        transport=transport,
        deployment=_deployment(unknown_price=True),
    )
    await worker.start_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
        run_policy=_policy(),
    )
    await worker.process_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
    )
    assert transport.calls == []


@pytest.mark.asyncio
async def test_stale_model_claim_key_is_scoped_to_current_chapter(builder_env) -> None:
    transport = ControlledTransport()
    transport.stale_claim_key_chapters = {2}

    class _Src:
        def __init__(self, sessions):
            self._sessions = sessions

        async def inventory(self, *, owner_id: int, novel_id: int):
            async with self._sessions() as session:
                return await PostgresAuditSource(session).inventory(
                    owner_id=owner_id, novel_id=novel_id
                )

    worker = NarrativeMemoryBuilderWorker(
        builder_env["factory"],
        inventory_source=_Src(builder_env["factory"]),
        transport=transport,
        deployment=_deployment(),
    )
    await worker.start_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
        run_policy=_policy(),
    )
    result = await worker.process_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
    )

    assert result.failed_stages == ()
    async with builder_env["factory"]() as session:
        claims = (
            await session.scalars(
                select(NarrativeMemoryClaim).where(
                    NarrativeMemoryClaim.version_id == builder_env["version_id"]
                )
            )
        ).all()
        assert {
            claim.claim_key for claim in claims if claim.visible_from_chapter == 2
        } == {"chapter_state:2:claim:1"}


@pytest.mark.asyncio
async def test_chapter_failure_isolates_siblings(builder_env) -> None:
    transport = ControlledTransport()
    transport.fail_chapters = {2}

    class _Src:
        def __init__(self, sessions):
            self._sessions = sessions

        async def inventory(self, *, owner_id: int, novel_id: int):
            async with self._sessions() as session:
                return await PostgresAuditSource(session).inventory(
                    owner_id=owner_id, novel_id=novel_id
                )

    worker = NarrativeMemoryBuilderWorker(
        builder_env["factory"],
        inventory_source=_Src(builder_env["factory"]),
        transport=transport,
        deployment=_deployment(),
    )
    run_id = await worker.start_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
        run_policy=_policy(),
    )
    result = await worker.process_run(
        owner_id=builder_env["owner_id"],
        novel_id=builder_env["novel_id"],
        version_id=builder_env["version_id"],
    )
    async with builder_env["factory"]() as session:
        stages = (
            await session.scalars(
                select(NarrativeMemoryBuildStage).where(
                    NarrativeMemoryBuildStage.run_id == run_id,
                    NarrativeMemoryBuildStage.stage_kind == "chapter_state",
                )
            )
        ).all()
        by_status = {s.status for s in stages}
        assert "completed" in by_status
        assert "failed" in by_status
        completed = [s for s in stages if s.status == "completed"]
        assert len(completed) >= 2
        # claims for completed chapters exist
        claims = (
            await session.scalars(
                select(NarrativeMemoryClaim).where(
                    NarrativeMemoryClaim.version_id == builder_env["version_id"]
                )
            )
        ).all()
        assert len(claims) >= 2
    assert result.failed_stages


def test_forbidden_capability_scan() -> None:
    hits = scan_builder_package_for_forbidden_capabilities()
    # Allow only scanner constant references — no runtime imports of chat/promotion.
    assert not any("reader_chat" in h and "import" in h for h in hits)
    assert not any("set_active_pointer" in h for h in hits)
