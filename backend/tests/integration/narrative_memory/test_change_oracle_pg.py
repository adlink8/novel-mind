"""PostgreSQL change-oracle integration for Phase 16."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.chunk_build import ChunkHierarchyNode
from app.models.narrative_memory import NarrativeMemoryNode, NarrativeMemoryVersion
from app.models.narrative_memory_builder import NarrativeMemoryBuildRun
from app.models.narrative_memory_rebuild import NarrativeMemoryRebuildItem
from app.models.novel import Chapter, Novel
from app.models.user import User
from app.services.chunking.pg_store import create_and_persist_hierarchy_build
from app.services.narrative_memory.audit import audit_assets
from app.services.narrative_memory.audit_pg import PostgresAuditSource
from app.services.narrative_memory.authority import CandidateAuthority
from app.services.narrative_memory.change_oracle import (
    compute_rebuild_plan,
    oracle_has_provider_capability,
    persist_rebuild_plan,
)
from app.services.narrative_memory.contracts import (
    CandidatePackage,
    CandidateVersionSpec,
    ModelLineage,
)
from app.services.narrative_memory.dependency_graph import (
    build_dependency_graph,
    load_parent_authority,
    load_target_hierarchy,
)
from app.services.narrative_memory.manifests import seal_and_report
from app.services.narrative_memory.rebuild_contracts import RebuildDecision, stable_checksum
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

    # Direct leaf for global
    leaf1 = by_chapter[1]
    claims.append(
        {
            "claim_key": "claim:global",
            "node_key": "global_story:book",
            "payload": {
                "claim_kind": "world_state_delta",
                "subject_key": "world:capital",
                "dimension": "political_order",
                "prior": {"value_kind": "unknown"},
                "current": {"value_kind": "text", "value": "stable"},
                "change": "establish",
            },
            "uncertainty": "certain",
            "confidence": 0.7,
            "visible_from_chapter": 1,
            "source_keys": ["source:global"],
        }
    )
    source_links.append(
        {
            "source_key": "source:global",
            "claim_key": "claim:global",
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
    claims.append(
        {
            "claim_key": "claim:arc",
            "node_key": "story_arc:1-3",
            "payload": {
                "claim_kind": "event_fact",
                "event_kind": "discovery",
                "actor_keys": ["character:lin"],
                "object_keys": [],
                "chapter_start": 1,
                "chapter_end": len(chapters),
                "outcome": {"value_kind": "text", "value": "arc-summary"},
            },
            "uncertainty": "likely",
            "confidence": 0.8,
            "visible_from_chapter": 1,
            "source_keys": ["source:arc"],
        }
    )
    source_links.append(
        {
            "source_key": "source:arc",
            "claim_key": "claim:arc",
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

    raw = {
        "nodes": nodes,
        "claims": claims,
        "edges": edges,
        "source_links": source_links,
    }
    return CandidatePackage.model_validate_json(json.dumps(raw, ensure_ascii=False))


async def _seed(session: AsyncSession, *, edit_chapter: int | None = None) -> dict:
    user = User(
        username="oracle-owner",
        email="oracle-owner@example.com",
        hashed_password="x",
    )
    session.add(user)
    await session.flush()
    novel = Novel(owner_id=user.id, title="Oracle Novel", status="ready")
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
    package = await _package_for(session, parent, chapters)
    await authority.persist_package(
        owner_id=user.id,
        novel_id=novel.id,
        version_id=parent.id,
        package=package,
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
        hierarchy_build_id = target.hierarchy_build_id
        eligibility = target.eligibility_report_checksum
    else:
        target = await authority.create_version(
            owner_id=user.id,
            novel_id=novel.id,
            spec=_spec("target-v1", parent_version_id=parent.id),
            eligibility_report=report,
        )
        hierarchy_build_id = target.hierarchy_build_id
        eligibility = target.eligibility_report_checksum

    await session.commit()
    return {
        "owner_id": user.id,
        "novel_id": novel.id,
        "parent_version_id": parent.id,
        "target_version_id": target.id,
        "hierarchy_build_id": hierarchy_build_id,
        "eligibility_checksum": eligibility,
    }


@pytest.fixture
async def oracle_env(empty_postgres: str, pg_async_url: str):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_async_engine(pg_async_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_no_change_plan_all_carried(oracle_env) -> None:
    async with oracle_env() as session:
        ctx = await _seed(session, edit_chapter=None)

    async with oracle_env() as session:
        plan = await compute_rebuild_plan(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            parent_version_id=ctx["parent_version_id"],
            target_version_id=ctx["target_version_id"],
            target_hierarchy_build_id=ctx["hierarchy_build_id"],
            eligibility_report_checksum=ctx["eligibility_checksum"],
        )
        chapter_items = [i for i in plan.items if i.asset_kind.value == "chapter_state"]
        assert chapter_items
        assert all(i.decision == RebuildDecision.CARRIED for i in chapter_items)
        global_items = [i for i in plan.items if i.asset_kind.value == "global_story"]
        assert global_items
        assert all(i.decision == RebuildDecision.CARRIED for i in global_items)

        row = await persist_rebuild_plan(session, plan)
        await session.commit()
        assert row.plan_checksum == plan.plan_checksum()
        items = (
            await session.scalars(
                select(NarrativeMemoryRebuildItem).where(
                    NarrativeMemoryRebuildItem.plan_id == row.id
                )
            )
        ).all()
        assert items
        row2 = await persist_rebuild_plan(session, plan)
        assert row2.id == row.id


@pytest.mark.asyncio
async def test_graph_lossless_and_provider_free(oracle_env) -> None:
    async with oracle_env() as session:
        ctx = await _seed(session)

    async with oracle_env() as session:
        parent = await load_parent_authority(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            parent_version_id=ctx["parent_version_id"],
        )
        target = await load_target_hierarchy(
            session,
            novel_id=ctx["novel_id"],
            hierarchy_build_id=ctx["hierarchy_build_id"],
        )
        g1 = build_dependency_graph(parent, target=target)
        g2 = build_dependency_graph(parent, target=target)
        assert g1.graph_checksum == g2.graph_checksum
        assert len(parent.nodes) >= 3
        assert oracle_has_provider_capability() is False
        count = await session.scalar(
            text(
                "SELECT count(*) FROM narrative_memory_build_model_call_attempts a "
                "JOIN narrative_memory_build_runs r ON r.id = a.run_id "
                "WHERE r.novel_id = :n"
            ),
            {"n": ctx["novel_id"]},
        )
        assert int(count or 0) == 0


@pytest.mark.asyncio
async def test_edit_dirties_local_closure(oracle_env) -> None:
    async with oracle_env() as session:
        ctx = await _seed(session, edit_chapter=2)

    async with oracle_env() as session:
        plan = await compute_rebuild_plan(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            parent_version_id=ctx["parent_version_id"],
            target_version_id=ctx["target_version_id"],
            target_hierarchy_build_id=ctx["hierarchy_build_id"],
            eligibility_report_checksum=ctx["eligibility_checksum"],
        )
        dirty = [i for i in plan.items if i.decision == RebuildDecision.DIRTY]
        assert dirty
        dirty_kinds = {i.asset_kind.value for i in dirty}
        assert "global_story" in dirty_kinds or "chapter_state" in dirty_kinds
        await persist_rebuild_plan(session, plan)
        await session.commit()
        parent_nodes = (
            await session.scalars(
                select(NarrativeMemoryNode).where(
                    NarrativeMemoryNode.version_id == ctx["parent_version_id"]
                )
            )
        ).all()
        assert parent_nodes


@pytest.mark.asyncio
async def test_cross_scope_rejected(oracle_env) -> None:
    async with oracle_env() as session:
        ctx = await _seed(session)

    async with oracle_env() as session:
        with pytest.raises(Exception):
            await compute_rebuild_plan(
                session,
                owner_id=ctx["owner_id"] + 999,
                novel_id=ctx["novel_id"],
                parent_version_id=ctx["parent_version_id"],
                target_version_id=ctx["target_version_id"],
                target_hierarchy_build_id=ctx["hierarchy_build_id"],
                eligibility_report_checksum=ctx["eligibility_checksum"],
            )
