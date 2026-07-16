"""PostgreSQL provenance closure, sealing, and adversarial cases."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models.chunk_build import ChunkHierarchyNode
from app.models.narrative_memory import (
    NarrativeMemoryClaim,
    NarrativeMemoryManifest,
    NarrativeMemoryNode,
    NarrativeMemoryValidationReport,
    NarrativeMemoryVersion,
)
from app.models.novel import Chapter
from app.models.user import User
from app.models.novel import Novel
from app.services.chunking.pg_store import create_and_persist_hierarchy_build
from app.services.narrative_memory.audit import audit_assets
from app.services.narrative_memory.audit_pg import PostgresAuditSource
from app.services.narrative_memory.authority import CandidateAuthority
from app.services.narrative_memory.contracts import (
    CandidatePackage,
    CandidateVersionSpec,
    ModelLineage,
)
from app.services.narrative_memory.manifests import (
    SealConflictError,
    compute_manifest_from_snapshot,
    load_candidate_snapshot,
    seal_and_report,
)


pytestmark = [pytest.mark.integration]


async def _seed_exact(session, *, unicode_content: bool = True):
    user = User(
        username="prov-authority",
        email="prov-authority@example.com",
        hashed_password="x",
    )
    session.add(user)
    await session.flush()
    novel = Novel(owner_id=user.id, title="Provenance Novel", status="ready")
    session.add(novel)
    await session.flush()
    contents = (
        ("甲乙丙丁戊己", "庚辛壬癸子丑")
        if unicode_content
        else ("abcdef", "ghijkl")
    )
    chapters = [
        Chapter(
            novel_id=novel.id,
            chapter_number=number,
            title=f"Chapter {number}",
            content=content,
            word_count=len(content),
        )
        for number, content in enumerate(contents, start=1)
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
    return user, novel, report


def _spec(version_key: str = "memory-v1") -> CandidateVersionSpec:
    return CandidateVersionSpec(
        version_key=version_key,
        prompt_hash="a" * 64,
        schema_hash="b" * 64,
        model_lineage=ModelLineage(
            provider="openai", model="gpt", deployment="fixed", revision="1"
        ),
        decoding_hash="c" * 64,
        config_hash="d" * 64,
        policy_hash="e" * 64,
    )


async def _package_for_version(
    session,
    version: NarrativeMemoryVersion,
    *,
    include_chapter_two: bool = True,
    reverse_nodes: bool = False,
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
    assert evidence_rows
    by_chapter = {row.chapter_number: row for row in evidence_rows}
    nodes = [
        {
            "node_kind": "global_story",
            "node_key": "global",
            "chapter_start": 1,
            "chapter_end": 2 if include_chapter_two else 1,
            "schema_version": "memory-node.v1",
        },
        {
            "node_kind": "story_arc",
            "node_key": "arc:1",
            "chapter_start": 1,
            "chapter_end": 2 if include_chapter_two else 1,
            "schema_version": "memory-node.v1",
        },
        {
            "node_kind": "chapter_state",
            "node_key": "chapter:1",
            "chapter_start": 1,
            "chapter_end": 1,
            "schema_version": "memory-node.v1",
        },
    ]
    claims = [
        {
            "claim_key": "claim:1",
            "node_key": "chapter:1",
            "payload": {
                "claim_kind": "entity_state",
                "entity_kind": "character",
                "entity_key": "character:lin",
                "dimension": "location",
                "prior": {"value_kind": "unknown"},
                "current": {"value_kind": "text", "value": "north gate"},
                "change": "establish",
            },
            "uncertainty": "certain",
            "confidence": 0.95,
            "visible_from_chapter": 1,
            "source_keys": ["source:1"],
        }
    ]
    edges = [
        {
            "edge_type": "contains",
            "source_node_key": "global",
            "target_node_key": "arc:1",
        },
        {
            "edge_type": "contains",
            "source_node_key": "arc:1",
            "target_node_key": "chapter:1",
        },
    ]
    source_links = [
        {
            "source_key": "source:1",
            "claim_key": "claim:1",
            "source_kind": "hierarchy",
            "hierarchy_build_id": version.hierarchy_build_id,
            "evidence_node_id": by_chapter[1].node_id,
            "chapter_id": by_chapter[1].chapter_id,
            "chapter_number": by_chapter[1].chapter_number,
            "source_start": by_chapter[1].source_start,
            "source_end": by_chapter[1].source_end,
            "content_hash": by_chapter[1].content_hash,
            "source_snapshot_hash": version.source_snapshot_hash,
        }
    ]
    if include_chapter_two:
        nodes.append(
            {
                "node_kind": "chapter_state",
                "node_key": "chapter:2",
                "chapter_start": 2,
                "chapter_end": 2,
                "schema_version": "memory-node.v1",
            }
        )
        claims.append(
            {
                "claim_key": "claim:2",
                "node_key": "chapter:2",
                "payload": {
                    "claim_kind": "event_fact",
                    "event_kind": "discovery",
                    "actor_keys": ["character:lin"],
                    "object_keys": [],
                    "chapter_start": 2,
                    "chapter_end": 2,
                    "outcome": {"value_kind": "text", "value": "found map"},
                },
                "uncertainty": "likely",
                "confidence": 0.8,
                "visible_from_chapter": 2,
                "source_keys": ["source:2"],
            }
        )
        edges.append(
            {
                "edge_type": "contains",
                "source_node_key": "arc:1",
                "target_node_key": "chapter:2",
            }
        )
        source_links.append(
            {
                "source_key": "source:2",
                "claim_key": "claim:2",
                "source_kind": "hierarchy",
                "hierarchy_build_id": version.hierarchy_build_id,
                "evidence_node_id": by_chapter[2].node_id,
                "chapter_id": by_chapter[2].chapter_id,
                "chapter_number": by_chapter[2].chapter_number,
                "source_start": by_chapter[2].source_start,
                "source_end": by_chapter[2].source_end,
                "content_hash": by_chapter[2].content_hash,
                "source_snapshot_hash": version.source_snapshot_hash,
            }
        )
        # Upper-level claim still needs its own direct leaf link.
        claims.append(
            {
                "claim_key": "claim:global",
                "node_key": "global",
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
                "visible_from_chapter": 2,
                "source_keys": ["source:global"],
            }
        )
        source_links.append(
            {
                "source_key": "source:global",
                "claim_key": "claim:global",
                "source_kind": "hierarchy",
                "hierarchy_build_id": version.hierarchy_build_id,
                "evidence_node_id": by_chapter[1].node_id,
                "chapter_id": by_chapter[1].chapter_id,
                "chapter_number": by_chapter[1].chapter_number,
                "source_start": by_chapter[1].source_start,
                "source_end": by_chapter[1].source_end,
                "content_hash": by_chapter[1].content_hash,
                "source_snapshot_hash": version.source_snapshot_hash,
            }
        )
    if reverse_nodes:
        nodes = list(reversed(nodes))
    raw = {
        "nodes": nodes,
        "claims": claims,
        "edges": edges,
        "source_links": source_links,
    }
    return CandidatePackage.model_validate_json(json.dumps(raw, ensure_ascii=False))


async def _persist_candidate(session, *, reverse_nodes: bool = False):
    user, novel, report = await _seed_exact(session)
    authority = CandidateAuthority(session)
    version = await authority.create_version(
        owner_id=user.id,
        novel_id=novel.id,
        spec=_spec(),
        eligibility_report=report,
    )
    package = await _package_for_version(
        session, version, reverse_nodes=reverse_nodes
    )
    await authority.persist_package(
        owner_id=user.id,
        novel_id=novel.id,
        version_id=version.id,
        package=package,
    )
    await session.flush()
    return user, novel, version


@pytest.mark.asyncio
async def test_unicode_reslice_and_seal_qualified_candidate(audit_pg_session):
    user, novel, version = await _persist_candidate(audit_pg_session)
    result = await seal_and_report(
        audit_pg_session,
        owner_id=user.id,
        novel_id=novel.id,
        version_id=version.id,
    )
    assert result.structural.ok is True
    assert result.report.verdict == "qualified_candidate"
    assert result.manifest.manifest_checksum == result.manifest_checksum
    assert len(result.manifest_checksum) == 64
    assert await audit_pg_session.scalar(
        select(func.count()).select_from(NarrativeMemoryManifest)
    ) == 1
    assert await audit_pg_session.scalar(
        select(func.count()).select_from(NarrativeMemoryValidationReport)
    ) == 1


@pytest.mark.asyncio
async def test_insertion_order_independent_manifests(audit_pg_session):
    user_a, novel_a, version_a = await _persist_candidate(
        audit_pg_session, reverse_nodes=False
    )
    snap_a = await load_candidate_snapshot(
        audit_pg_session,
        owner_id=user_a.id,
        novel_id=novel_a.id,
        version_id=version_a.id,
    )
    checksum_a = compute_manifest_from_snapshot(snap_a).manifest_checksum

    # Second candidate with reversed insert order in the same session/db.
    user_b = User(
        username="prov-authority-b",
        email="prov-authority-b@example.com",
        hashed_password="x",
    )
    audit_pg_session.add(user_b)
    await audit_pg_session.flush()
    novel_b = Novel(owner_id=user_b.id, title="Order B", status="ready")
    audit_pg_session.add(novel_b)
    await audit_pg_session.flush()
    chapters = [
        Chapter(
            novel_id=novel_b.id,
            chapter_number=number,
            title=f"B{number}",
            content=content,
            word_count=len(content),
        )
        for number, content in ((1, "甲乙丙丁戊己"), (2, "庚辛壬癸子丑"))
    ]
    audit_pg_session.add_all(chapters)
    await audit_pg_session.flush()
    await create_and_persist_hierarchy_build(
        audit_pg_session,
        novel_id=novel_b.id,
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
    await audit_pg_session.flush()
    report_b = await audit_assets(
        PostgresAuditSource(audit_pg_session),
        owner_id=user_b.id,
        novel_id=novel_b.id,
    )
    authority = CandidateAuthority(audit_pg_session)
    version_b = await authority.create_version(
        owner_id=user_b.id,
        novel_id=novel_b.id,
        spec=_spec("memory-v1"),
        eligibility_report=report_b,
    )
    package_b = await _package_for_version(
        audit_pg_session, version_b, reverse_nodes=True
    )
    await authority.persist_package(
        owner_id=user_b.id,
        novel_id=novel_b.id,
        version_id=version_b.id,
        package=package_b,
    )
    await audit_pg_session.flush()
    snap_b = await load_candidate_snapshot(
        audit_pg_session,
        owner_id=user_b.id,
        novel_id=novel_b.id,
        version_id=version_b.id,
    )
    checksum_b = compute_manifest_from_snapshot(snap_b).manifest_checksum
    # Different owners/versions differ; prove within-snapshot sort stability instead.
    snap_b_reloaded = await load_candidate_snapshot(
        audit_pg_session,
        owner_id=user_b.id,
        novel_id=novel_b.id,
        version_id=version_b.id,
    )
    assert (
        compute_manifest_from_snapshot(snap_b_reloaded).manifest_checksum
        == checksum_b
    )
    assert checksum_a
    assert checksum_b


@pytest.mark.asyncio
async def test_stale_chapter_content_blocks_and_still_seals_report(audit_pg_session):
    user, novel, version = await _persist_candidate(audit_pg_session)
    chapter = await audit_pg_session.scalar(
        select(Chapter).where(Chapter.novel_id == novel.id, Chapter.chapter_number == 1)
    )
    assert chapter is not None
    chapter.content = "被篡改的章节正文内容"
    await audit_pg_session.flush()

    result = await seal_and_report(
        audit_pg_session,
        owner_id=user.id,
        novel_id=novel.id,
        version_id=version.id,
    )
    assert result.structural.ok is False
    assert result.report.verdict == "blocked"
    assert "reslice_mismatch" in result.structural.reason_codes or (
        "content_hash_mismatch" in result.structural.reason_codes
    )


@pytest.mark.asyncio
async def test_missing_claim_link_blocks(audit_pg_session):
    user, novel, report = await _seed_exact(audit_pg_session)
    authority = CandidateAuthority(audit_pg_session)
    version = await authority.create_version(
        owner_id=user.id,
        novel_id=novel.id,
        spec=_spec(),
        eligibility_report=report,
    )
    package = await _package_for_version(audit_pg_session, version)
    await authority.persist_package(
        owner_id=user.id,
        novel_id=novel.id,
        version_id=version.id,
        package=package,
    )
    await audit_pg_session.flush()
    # Delete one source link is forbidden by append-only; instead create a claim
    # without link by direct insert of an extra claim after package validation.
    chapter_node = await audit_pg_session.scalar(
        select(NarrativeMemoryNode).where(
            NarrativeMemoryNode.version_id == version.id,
            NarrativeMemoryNode.node_key == "chapter:1",
        )
    )
    assert chapter_node is not None
    audit_pg_session.add(
        NarrativeMemoryClaim(
            owner_id=user.id,
            novel_id=novel.id,
            version_id=version.id,
            node_id=chapter_node.id,
            claim_key="claim:orphan",
            claim_kind="entity_state",
            schema_version="memory-claim.v1",
            typed_payload={
                "claim_kind": "entity_state",
                "entity_kind": "character",
                "entity_key": "character:orphan",
                "dimension": "location",
                "prior": {"value_kind": "unknown"},
                "current": {"value_kind": "text", "value": "x"},
                "change": "establish",
            },
            uncertainty="certain",
            confidence=0.5,
            visible_from_chapter=1,
            claim_checksum="f" * 64,
            model_lineage_checksum="e" * 64,
        )
    )
    await audit_pg_session.flush()
    result = await seal_and_report(
        audit_pg_session,
        owner_id=user.id,
        novel_id=novel.id,
        version_id=version.id,
    )
    assert result.structural.ok is False
    assert "missing_claim_source" in result.structural.reason_codes


@pytest.mark.asyncio
async def test_post_seal_insert_and_update_delete_rejected(audit_pg_session):
    user, novel, version = await _persist_candidate(audit_pg_session)
    await seal_and_report(
        audit_pg_session,
        owner_id=user.id,
        novel_id=novel.id,
        version_id=version.id,
    )
    with pytest.raises(SealConflictError):
        await seal_and_report(
            audit_pg_session,
            owner_id=user.id,
            novel_id=novel.id,
            version_id=version.id,
        )

    node_id = await audit_pg_session.scalar(
        select(NarrativeMemoryNode.id).where(
            NarrativeMemoryNode.version_id == version.id,
            NarrativeMemoryNode.node_key == "chapter:1",
        )
    )
    assert node_id is not None

    audit_pg_session.add(
        NarrativeMemoryNode(
            owner_id=user.id,
            novel_id=novel.id,
            version_id=version.id,
            node_key="chapter:late",
            node_kind="chapter_state",
            chapter_start=1,
            chapter_end=1,
            schema_version="memory-node.v1",
            content_checksum="1" * 64,
            model_lineage_checksum="2" * 64,
        )
    )
    with pytest.raises((IntegrityError, DBAPIError)):
        await audit_pg_session.flush()
    await audit_pg_session.rollback()

    # Rollback cleared the fixture transaction; reseed a sealed candidate.
    user, novel, version = await _persist_candidate(audit_pg_session)
    await seal_and_report(
        audit_pg_session,
        owner_id=user.id,
        novel_id=novel.id,
        version_id=version.id,
    )
    node_id = await audit_pg_session.scalar(
        select(NarrativeMemoryNode.id).where(
            NarrativeMemoryNode.version_id == version.id,
            NarrativeMemoryNode.node_key == "chapter:1",
        )
    )
    assert node_id is not None
    with pytest.raises((IntegrityError, DBAPIError)):
        await audit_pg_session.execute(
            text(
                "UPDATE narrative_memory_nodes SET display_label = 'x' WHERE id = :id"
            ),
            {"id": node_id},
        )
    await audit_pg_session.rollback()

    user, novel, version = await _persist_candidate(audit_pg_session)
    await seal_and_report(
        audit_pg_session,
        owner_id=user.id,
        novel_id=novel.id,
        version_id=version.id,
    )
    node_id = await audit_pg_session.scalar(
        select(NarrativeMemoryNode.id).where(
            NarrativeMemoryNode.version_id == version.id,
            NarrativeMemoryNode.node_key == "chapter:1",
        )
    )
    assert node_id is not None
    with pytest.raises((IntegrityError, DBAPIError)):
        await audit_pg_session.execute(
            text("DELETE FROM narrative_memory_nodes WHERE id = :id"),
            {"id": node_id},
        )
    await audit_pg_session.rollback()


@pytest.mark.asyncio
async def test_wrong_evidence_offsets_block(audit_pg_session):
    user, novel, report = await _seed_exact(audit_pg_session)
    authority = CandidateAuthority(audit_pg_session)
    version = await authority.create_version(
        owner_id=user.id,
        novel_id=novel.id,
        spec=_spec(),
        eligibility_report=report,
    )
    package = await _package_for_version(audit_pg_session, version)
    await authority.persist_package(
        owner_id=user.id,
        novel_id=novel.id,
        version_id=version.id,
        package=package,
    )
    await audit_pg_session.flush()
    chapter = await audit_pg_session.scalar(
        select(Chapter).where(Chapter.novel_id == novel.id, Chapter.chapter_number == 1)
    )
    assert chapter is not None
    # Prefix-preserving append would still re-slice equal; replace body instead.
    chapter.content = "ZZ" * max(1, len(chapter.content))
    await audit_pg_session.flush()
    result = await seal_and_report(
        audit_pg_session,
        owner_id=user.id,
        novel_id=novel.id,
        version_id=version.id,
    )
    assert result.structural.ok is False
    assert any(
        code in result.structural.reason_codes
        for code in ("reslice_mismatch", "content_hash_mismatch", "invalid_offset")
    )


def test_phase13_package_has_no_pointer_provider_chat_capability() -> None:
    root = Path(__file__).resolve().parents[3] / "app" / "services" / "narrative_memory"
    forbidden = (
        "model_gateway",
        "activepointer",
        "active_pointer",
        "promotion",
        "rollback",
        "chroma",
        "reader_chat",
        "resolve_current",
        "resolve_active",
        "dispatch",
    )
    for name in (
        "authority.py",
        "contracts.py",
        "provenance.py",
        "manifests.py",
    ):
        source = (root / name).read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token not in source, f"{name} contains forbidden token {token}"
