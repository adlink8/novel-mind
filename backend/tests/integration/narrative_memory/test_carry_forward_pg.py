"""PostgreSQL carry-forward tests for Phase 16."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.chunk_build import ChunkHierarchyNode
from app.models.narrative_memory import (
    NarrativeMemoryClaim,
    NarrativeMemoryNode,
    NarrativeMemoryVersion,
)
from app.models.narrative_memory_builder import NarrativeMemoryBuildRun
from app.models.narrative_memory_rebuild import NarrativeMemoryRebuildItem
from app.models.novel import Chapter, Novel
from app.models.user import User
from app.services.chunking.pg_store import create_and_persist_hierarchy_build
from app.services.narrative_memory.audit import audit_assets
from app.services.narrative_memory.audit_pg import PostgresAuditSource
from app.services.narrative_memory.authority import CandidateAuthority
from app.services.narrative_memory.carry_forward import (
    CarryForwardError,
    carry_forward_from_plan,
    carry_has_provider_capability,
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
        username="carry-owner",
        email="carry-owner@example.com",
        hashed_password="x",
    )
    session.add(user)
    await session.flush()
    novel = Novel(owner_id=user.id, title="Carry Novel", status="ready")
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
        "chapter_ids": {c.chapter_number: c.id for c in chapters},
    }


@pytest.fixture
async def carry_env(empty_postgres: str, pg_async_url: str):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_async_engine(pg_async_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _plan_and_persist(session: AsyncSession, ctx: dict):
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
    await session.commit()
    return plan, row


@pytest.mark.asyncio
async def test_no_change_carry_preserves_semantic_checksums(carry_env) -> None:
    assert carry_has_provider_capability() is False
    async with carry_env() as session:
        ctx = await _seed(session, edit_chapter=None)

    async with carry_env() as session:
        plan, row = await _plan_and_persist(session, ctx)
        assert all(
            i.decision == RebuildDecision.CARRIED
            for i in plan.items
            if i.asset_kind.value
            in {"chapter_state", "story_arc", "global_story"}
        )
        result = await carry_forward_from_plan(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            plan_id=row.id,
            expected_plan_checksum=row.plan_checksum,
        )
        await session.commit()
        assert result.carried_node_keys
        assert result.skipped_dirty_keys == ()

        parent_nodes = {
            n.node_key: n
            for n in (
                await session.scalars(
                    select(NarrativeMemoryNode).where(
                        NarrativeMemoryNode.version_id == ctx["parent_version_id"]
                    )
                )
            ).all()
        }
        target_nodes = {
            n.node_key: n
            for n in (
                await session.scalars(
                    select(NarrativeMemoryNode).where(
                        NarrativeMemoryNode.version_id == ctx["target_version_id"]
                    )
                )
            ).all()
        }
        for key, pn in parent_nodes.items():
            assert key in target_nodes
            assert target_nodes[key].content_checksum == pn.content_checksum
            assert target_nodes[key].id != pn.id

        parent_claims = {
            c.claim_key: c
            for c in (
                await session.scalars(
                    select(NarrativeMemoryClaim).where(
                        NarrativeMemoryClaim.version_id == ctx["parent_version_id"]
                    )
                )
            ).all()
        }
        target_claims = {
            c.claim_key: c
            for c in (
                await session.scalars(
                    select(NarrativeMemoryClaim).where(
                        NarrativeMemoryClaim.version_id == ctx["target_version_id"]
                    )
                )
            ).all()
        }
        for key, pc in parent_claims.items():
            assert key in target_claims
            # Typed payload + uncertainty/confidence/visibility are semantic authority.
            # Full claim_checksum also folds package-local source_keys (not stored on
            # link rows), so target rebinds may change the composite hash.
            assert target_claims[key].typed_payload == pc.typed_payload
            assert target_claims[key].uncertainty == pc.uncertainty
            assert float(target_claims[key].confidence) == float(pc.confidence)
            assert (
                target_claims[key].visible_from_chapter == pc.visible_from_chapter
            )

        # Idempotent retry
        result2 = await carry_forward_from_plan(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            plan_id=row.id,
            expected_plan_checksum=row.plan_checksum,
        )
        await session.commit()
        assert set(result2.carried_node_keys) == set(result.carried_node_keys)
        n_count = await session.scalar(
            text(
                "SELECT count(*) FROM narrative_memory_nodes "
                "WHERE version_id = :v"
            ),
            {"v": ctx["target_version_id"]},
        )
        assert int(n_count) == len(parent_nodes)


@pytest.mark.asyncio
async def test_partial_carry_on_stable_edit(carry_env) -> None:
    async with carry_env() as session:
        ctx = await _seed(session, edit_chapter=2)

    async with carry_env() as session:
        plan, row = await _plan_and_persist(session, ctx)
        dirty_keys = {
            i.asset_key
            for i in plan.items
            if i.decision == RebuildDecision.DIRTY
            and i.asset_kind.value
            in {"chapter_state", "story_arc", "global_story"}
        }
        carried_keys = {
            i.asset_key
            for i in plan.items
            if i.decision == RebuildDecision.CARRIED
            and i.asset_kind.value
            in {"chapter_state", "story_arc", "global_story"}
        }
        assert dirty_keys  # edit must dirty at least leaf/parent/global

        result = await carry_forward_from_plan(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            plan_id=row.id,
            expected_plan_checksum=row.plan_checksum,
        )
        await session.commit()

        target_keys = {
            n.node_key
            for n in (
                await session.scalars(
                    select(NarrativeMemoryNode).where(
                        NarrativeMemoryNode.version_id == ctx["target_version_id"]
                    )
                )
            ).all()
        }
        # Only carried semantic assets are copied; dirty keys never appear.
        assert dirty_keys.isdisjoint(target_keys)
        assert carried_keys.issubset(target_keys)
        assert set(result.skipped_dirty_keys) == dirty_keys

        parent_by_key = {
            n.node_key: n
            for n in (
                await session.scalars(
                    select(NarrativeMemoryNode).where(
                        NarrativeMemoryNode.version_id == ctx["parent_version_id"]
                    )
                )
            ).all()
        }
        for key in carried_keys:
            tn = (
                await session.scalars(
                    select(NarrativeMemoryNode).where(
                        NarrativeMemoryNode.version_id == ctx["target_version_id"],
                        NarrativeMemoryNode.node_key == key,
                    )
                )
            ).first()
            assert tn is not None
            assert tn.content_checksum == parent_by_key[key].content_checksum


@pytest.mark.asyncio
async def test_stale_plan_checksum_rejected(carry_env) -> None:
    async with carry_env() as session:
        ctx = await _seed(session)

    async with carry_env() as session:
        _, row = await _plan_and_persist(session, ctx)
        with pytest.raises(CarryForwardError, match="stale plan"):
            await carry_forward_from_plan(
                session,
                owner_id=ctx["owner_id"],
                novel_id=ctx["novel_id"],
                plan_id=row.id,
                expected_plan_checksum="f" * 64,
            )


@pytest.mark.asyncio
async def test_cross_scope_carry_rejected(carry_env) -> None:
    async with carry_env() as session:
        ctx = await _seed(session)

    async with carry_env() as session:
        _, row = await _plan_and_persist(session, ctx)
        with pytest.raises(CarryForwardError):
            await carry_forward_from_plan(
                session,
                owner_id=ctx["owner_id"] + 999,
                novel_id=ctx["novel_id"],
                plan_id=row.id,
            )


@pytest.mark.asyncio
async def test_carry_creates_zero_provider_attempts(carry_env) -> None:
    async with carry_env() as session:
        ctx = await _seed(session)

    async with carry_env() as session:
        _, row = await _plan_and_persist(session, ctx)
        await carry_forward_from_plan(
            session,
            owner_id=ctx["owner_id"],
            novel_id=ctx["novel_id"],
            plan_id=row.id,
            expected_plan_checksum=row.plan_checksum,
        )
        await session.commit()
        attempts = await session.scalar(
            text(
                "SELECT count(*) FROM narrative_memory_build_model_call_attempts a "
                "JOIN narrative_memory_build_runs r ON r.id = a.run_id "
                "WHERE r.novel_id = :n AND r.version_id = :v"
            ),
            {"n": ctx["novel_id"], "v": ctx["target_version_id"]},
        )
        assert int(attempts or 0) == 0
        items = list(
            (
                await session.scalars(
                    select(NarrativeMemoryRebuildItem).where(
                        NarrativeMemoryRebuildItem.plan_id == row.id,
                        NarrativeMemoryRebuildItem.decision == "carried",
                    )
                )
            ).all()
        )
        assert items
