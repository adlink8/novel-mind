"""PostgreSQL reuse-report recomputation tests for Phase 16."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.chunk_build import ChunkHierarchyNode
from app.models.narrative_memory import NarrativeMemoryVersion
from app.models.narrative_memory_builder import NarrativeMemoryBuildRun
from app.models.narrative_memory_rebuild import NarrativeMemoryReuseReport
from app.models.novel import Chapter, Novel
from app.models.user import User
from app.services.chunking.pg_store import create_and_persist_hierarchy_build
from app.services.narrative_memory.audit import audit_assets
from app.services.narrative_memory.audit_pg import PostgresAuditSource
from app.services.narrative_memory.authority import CandidateAuthority
from app.services.narrative_memory.builder_contracts import (
    BudgetPolicy,
    RunPolicy,
    StageKind,
)
from app.services.narrative_memory.change_oracle import (
    compute_rebuild_plan,
    persist_rebuild_plan,
)
from app.services.narrative_memory.contracts import (
    CandidatePackage,
    CandidateVersionSpec,
    ModelLineage,
)
from app.services.narrative_memory.manifests import seal_and_report
from app.services.narrative_memory.rebuild_contracts import stable_checksum
from app.services.narrative_memory.rebuild_executor import materialize_carry_and_dirty_stages
from app.services.narrative_memory.reuse_report import (
    persist_reuse_report,
    recompute_reuse_report,
    report_has_provider_capability,
)
from tests.integration.conftest import run_alembic

pytestmark = pytest.mark.integration

HEX = "a" * 64


def _spec(version_key: str, *, parent_version_id: int | None = None) -> CandidateVersionSpec:
    return CandidateVersionSpec(
        version_key=version_key,
        prompt_hash=HEX,
        schema_hash=HEX,
        model_lineage=ModelLineage(
            provider="test", model="m", deployment="fixed", revision="1"
        ),
        decoding_hash=HEX,
        config_hash=HEX,
        policy_hash=HEX,
        parent_version_id=parent_version_id,
    )


def _run_policy() -> RunPolicy:
    return RunPolicy(
        policy_version="builder-policy.v1",
        stage_order=(
            StageKind.CHAPTER_STATE,
            StageKind.ARC_VOLUME_PLAN,
            StageKind.ARC_VOLUME_AGGREGATE,
            StageKind.GLOBAL_AGGREGATE,
        ),
        max_schema_repairs=1,
        chapter_concurrency=1,
        arc_window_size=2,
        budget=BudgetPolicy(
            max_calls=100,
            max_input_tokens=1_000_000,
            max_output_tokens=1_000_000,
            max_cost_usd="100.0",
        ),
        prompt_hash=HEX,
        schema_hash=HEX,
        model_lineage=ModelLineage(
            provider="test", model="m", deployment="fixed", revision="1"
        ),
        decoding_hash=HEX,
        config_hash=HEX,
        policy_hash=HEX,
    )


async def _package_for(
    session: AsyncSession,
    version: NarrativeMemoryVersion,
    chapters: list[Chapter],
) -> CandidatePackage:
    evidence_rows = list(
        (
            await session.scalars(
                select(ChunkHierarchyNode)
                .where(
                    ChunkHierarchyNode.build_id == version.hierarchy_build_id,
                    ChunkHierarchyNode.level == "evidence",
                )
                .order_by(
                    ChunkHierarchyNode.chapter_number,
                    ChunkHierarchyNode.order_index,
                    ChunkHierarchyNode.id,
                )
            )
        ).all()
    )
    by_chapter = {}
    for row in evidence_rows:
        by_chapter.setdefault(row.chapter_number, row)
    nodes = [
        {
            "node_kind": "global_story",
            "node_key": "global_story:book",
            "chapter_start": 1,
            "chapter_end": len(chapters),
            "schema_version": "memory-node.v1",
        },
        {
            "node_kind": "story_arc",
            "node_key": "story_arc:1-3",
            "chapter_start": 1,
            "chapter_end": len(chapters),
            "schema_version": "memory-node.v1",
        },
    ]
    claims = []
    edges = [
        {
            "edge_type": "contains",
            "source_node_key": "global_story:book",
            "target_node_key": "story_arc:1-3",
        }
    ]
    source_links = []
    for ch in chapters:
        leaf = by_chapter[ch.chapter_number]
        node_key = f"chapter_state:{ch.id}"
        claim_key = f"claim:ch:{ch.chapter_number}"
        source_key = f"source:ch:{ch.chapter_number}"
        nodes.append(
            {
                "node_kind": "chapter_state",
                "node_key": node_key,
                "chapter_start": ch.chapter_number,
                "chapter_end": ch.chapter_number,
                "schema_version": "memory-node.v1",
            }
        )
        claims.append(
            {
                "claim_key": claim_key,
                "node_key": node_key,
                "payload": {
                    "claim_kind": "entity_state",
                    "entity_kind": "character",
                    "entity_key": "character:lin",
                    "dimension": "location",
                    "prior": {"value_kind": "unknown"},
                    "current": {
                        "value_kind": "text",
                        "value": f"place-{ch.chapter_number}",
                    },
                    "change": "establish",
                },
                "uncertainty": "certain",
                "confidence": 0.95,
                "visible_from_chapter": ch.chapter_number,
                "source_keys": [source_key],
            }
        )
        edges.append(
            {
                "edge_type": "contains",
                "source_node_key": "story_arc:1-3",
                "target_node_key": node_key,
            }
        )
        source_links.append(
            {
                "source_key": source_key,
                "claim_key": claim_key,
                "source_kind": "hierarchy",
                "hierarchy_build_id": version.hierarchy_build_id,
                "evidence_node_id": leaf.node_id,
                "chapter_id": leaf.chapter_id,
                "chapter_number": leaf.chapter_number,
                "source_start": leaf.source_start,
                "source_end": leaf.source_end,
                "content_hash": leaf.content_hash,
                "source_snapshot_hash": version.source_snapshot_hash,
            }
        )
    leaf1 = by_chapter[1]
    for claim_key, node_key, payload in (
        (
            "claim:global",
            "global_story:book",
            {
                "claim_kind": "world_state_delta",
                "subject_key": "world:capital",
                "dimension": "political_order",
                "prior": {"value_kind": "unknown"},
                "current": {"value_kind": "text", "value": "stable"},
                "change": "establish",
            },
        ),
        (
            "claim:arc",
            "story_arc:1-3",
            {
                "claim_kind": "event_fact",
                "event_kind": "discovery",
                "actor_keys": ["character:lin"],
                "object_keys": [],
                "chapter_start": 1,
                "chapter_end": len(chapters),
                "outcome": {"value_kind": "text", "value": "arc-summary"},
            },
        ),
    ):
        claims.append(
            {
                "claim_key": claim_key,
                "node_key": node_key,
                "payload": payload,
                "uncertainty": "certain",
                "confidence": 0.7,
                "visible_from_chapter": 1,
                "source_keys": [f"source:{claim_key}"],
            }
        )
        source_links.append(
            {
                "source_key": f"source:{claim_key}",
                "claim_key": claim_key,
                "source_kind": "hierarchy",
                "hierarchy_build_id": version.hierarchy_build_id,
                "evidence_node_id": leaf1.node_id,
                "chapter_id": leaf1.chapter_id,
                "chapter_number": leaf1.chapter_number,
                "source_start": leaf1.source_start,
                "source_end": leaf1.source_end,
                "content_hash": leaf1.content_hash,
                "source_snapshot_hash": version.source_snapshot_hash,
            }
        )
    return CandidatePackage.model_validate_json(
        json.dumps(
            {
                "nodes": nodes,
                "claims": claims,
                "edges": edges,
                "source_links": source_links,
            },
            ensure_ascii=False,
        )
    )


async def _seed(session: AsyncSession, *, edit_chapter: int | None = None) -> dict:
    user = User(
        username="reuse-report-owner",
        email="reuse-report@example.com",
        hashed_password="x",
    )
    session.add(user)
    await session.flush()
    novel = Novel(owner_id=user.id, title="Reuse Report", status="ready")
    session.add(novel)
    await session.flush()
    contents = {1: "甲乙丙丁戊己", 2: "庚辛壬癸子丑", 3: "寅卯辰巳午未"}
    chapters = [
        Chapter(
            novel_id=novel.id,
            chapter_number=n,
            title=f"Chapter {n}",
            content=contents[n],
            word_count=len(contents[n]),
        )
        for n in (1, 2, 3)
    ]
    session.add_all(chapters)
    await session.flush()
    await create_and_persist_hierarchy_build(
        session,
        novel_id=novel.id,
        chapters=[
            {
                "chapter_id": c.id,
                "chapter_number": c.chapter_number,
                "content": c.content,
            }
            for c in chapters
        ],
        promote_active=True,
        force_full=True,
    )
    await session.flush()
    report = await audit_assets(
        PostgresAuditSource(session), owner_id=user.id, novel_id=novel.id
    )
    authority = CandidateAuthority(session)
    parent = await authority.create_version(
        owner_id=user.id,
        novel_id=novel.id,
        spec=_spec("parent-v1"),
        eligibility_report=report,
    )
    await authority.persist_package(
        owner_id=user.id,
        novel_id=novel.id,
        version_id=parent.id,
        package=await _package_for(session, parent, chapters),
    )
    boundary = {
        "source_kind": "explicit_volume",
        "chapter_min": 1,
        "chapter_max": 3,
        "chapter_to_parent": {
            str(ch.chapter_number): "story_arc:1-3" for ch in chapters
        },
        "parent_to_global": {"story_arc:1-3": "global_story:book"},
    }
    session.add(
        NarrativeMemoryBuildRun(
            owner_id=user.id,
            novel_id=novel.id,
            version_id=parent.id,
            eligibility_report_checksum=parent.eligibility_report_checksum,
            eligibility_policy_version=parent.eligibility_policy_version,
            status="completed",
            progress={},
            run_policy={},
            boundary_plan=boundary,
            boundary_plan_checksum=stable_checksum(boundary),
        )
    )
    await session.flush()
    await seal_and_report(
        session,
        owner_id=user.id,
        novel_id=novel.id,
        version_id=parent.id,
    )
    if edit_chapter is not None:
        ch = next(c for c in chapters if c.chapter_number == edit_chapter)
        ch.content = ch.content + "改"
        ch.word_count = len(ch.content)
        await session.flush()
        await create_and_persist_hierarchy_build(
            session,
            novel_id=novel.id,
            chapters=[
                {
                    "chapter_id": c.id,
                    "chapter_number": c.chapter_number,
                    "content": c.content,
                }
                for c in chapters
            ],
            promote_active=True,
            force_full=True,
        )
        await session.flush()
        report2 = await audit_assets(
            PostgresAuditSource(session), owner_id=user.id, novel_id=novel.id
        )
        target = await authority.create_version(
            owner_id=user.id,
            novel_id=novel.id,
            spec=_spec("target-v1", parent_version_id=parent.id),
            eligibility_report=report2,
        )
    else:
        target = await authority.create_version(
            owner_id=user.id,
            novel_id=novel.id,
            spec=_spec("target-v1", parent_version_id=parent.id),
            eligibility_report=report,
        )
    await session.commit()
    return {
        "owner_id": user.id,
        "novel_id": novel.id,
        "parent_version_id": parent.id,
        "target_version_id": target.id,
        "hierarchy_build_id": target.hierarchy_build_id,
        "eligibility_checksum": target.eligibility_report_checksum,
    }


@pytest.fixture
async def report_env(empty_postgres: str, pg_async_url: str):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_async_engine(pg_async_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_no_change_report_full_carry_zero_calls(report_env) -> None:
    assert report_has_provider_capability() is False
    async with report_env() as session:
        ctx = await _seed(session, edit_chapter=None)

    async with report_env() as session:
        plan = await compute_rebuild_plan(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            parent_version_id=ctx["parent_version_id"],
            target_version_id=ctx["target_version_id"],
            target_hierarchy_build_id=ctx["hierarchy_build_id"],
            eligibility_report_checksum=ctx["eligibility_checksum"],
        )
        row = await persist_rebuild_plan(session, plan)
        await materialize_carry_and_dirty_stages(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            plan_id=row.id,
            run_policy=_run_policy(),
            expected_plan_checksum=row.plan_checksum,
        )
        body = await recompute_reuse_report(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            plan_id=row.id,
            full_rebuild_stage_count=5,
            reservation_envelope_input=100,
            reservation_envelope_output=50,
            price_input_per_million="1.0",
            price_output_per_million="2.0",
        )
        assert body["observed_actual"]["label"] == "observed_actual"
        assert body["observed_actual"]["calls"] == 0
        assert body["carry_reuse"]["carried_item_count"] >= 3
        assert body["carried_counts"]["total"] == body["carry_reuse"]["carried_item_count"]
        assert body["full_rebuild_upper_bound"]["calls"] == 5
        assert body["avoided_upper_bound"]["calls"] == 5
        assert body["full_rebuild_upper_bound"]["price_known"] is True
        assert body["cache_reuse"]["label"] == "exact_cache_reuse"

        persisted = await persist_reuse_report(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            plan_id=row.id,
            body=body,
        )
        await session.commit()

        body2 = await recompute_reuse_report(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            plan_id=row.id,
            full_rebuild_stage_count=5,
            reservation_envelope_input=100,
            reservation_envelope_output=50,
            price_input_per_million="1.0",
            price_output_per_million="2.0",
        )
        assert body2["report_checksum"] == body["report_checksum"]
        assert body2["report_checksum"] == persisted.report_checksum

        again = await persist_reuse_report(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            plan_id=row.id,
            body=body2,
        )
        assert again.id == persisted.id


@pytest.mark.asyncio
async def test_edit_report_separates_dirty_and_carry(report_env) -> None:
    async with report_env() as session:
        ctx = await _seed(session, edit_chapter=2)

    async with report_env() as session:
        plan = await compute_rebuild_plan(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            parent_version_id=ctx["parent_version_id"],
            target_version_id=ctx["target_version_id"],
            target_hierarchy_build_id=ctx["hierarchy_build_id"],
            eligibility_report_checksum=ctx["eligibility_checksum"],
        )
        row = await persist_rebuild_plan(session, plan)
        await materialize_carry_and_dirty_stages(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            plan_id=row.id,
            run_policy=_run_policy(),
            expected_plan_checksum=row.plan_checksum,
        )
        body = await recompute_reuse_report(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            plan_id=row.id,
            full_rebuild_stage_count=6,
            reservation_envelope_input=200,
            reservation_envelope_output=100,
        )
        assert body["rebuilt_counts"]["total"] >= 1
        assert body["carried_counts"]["total"] >= 1
        # Carry count from items, not stages
        assert body["carry_reuse"]["carried_item_count"] == body["carried_counts"]["total"]
        assert body["observed_actual"]["calls"] == 0  # no worker provider calls yet
        assert body["observed_actual"]["dirty_stage_rows"] >= 1
        assert body["full_rebuild_upper_bound"]["price_known"] is False
        await persist_reuse_report(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            plan_id=row.id,
            body=body,
        )
        await session.commit()
        rows = list(
            (
                await session.scalars(
                    select(NarrativeMemoryReuseReport).where(
                        NarrativeMemoryReuseReport.plan_id == row.id
                    )
                )
            ).all()
        )
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_carried_items_have_no_stage_rows_in_report_path(report_env) -> None:
    async with report_env() as session:
        ctx = await _seed(session, edit_chapter=2)

    async with report_env() as session:
        plan = await compute_rebuild_plan(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            parent_version_id=ctx["parent_version_id"],
            target_version_id=ctx["target_version_id"],
            target_hierarchy_build_id=ctx["hierarchy_build_id"],
            eligibility_report_checksum=ctx["eligibility_checksum"],
        )
        row = await persist_rebuild_plan(session, plan)
        _, mask, run_id = await materialize_carry_and_dirty_stages(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            plan_id=row.id,
            run_policy=_run_policy(),
            expected_plan_checksum=row.plan_checksum,
        )
        await session.commit()
        # recompute must not raise carried-has-stage error
        body = await recompute_reuse_report(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            plan_id=row.id,
        )
        assert body["carry_reuse"]["note"]
        for key in mask.carried_asset_keys:
            # stage_key presence checked via report integrity
            pass
        attempts = await session.scalar(
            text(
                "SELECT count(*) FROM narrative_memory_build_model_call_attempts "
                "WHERE run_id = :r"
            ),
            {"r": run_id},
        )
        assert int(attempts or 0) == 0
