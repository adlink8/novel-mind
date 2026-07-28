"""Explicit-version persistence behind verified Phase 12 eligibility."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models.chunk_build import ChunkBuild, ChunkHierarchyNode
from app.models.narrative_memory import (
    NarrativeMemoryClaim,
    NarrativeMemoryEdge,
    NarrativeMemoryNode,
    NarrativeMemorySourceLink,
    NarrativeMemoryVersion,
)
from app.models.novel import Chapter, Novel
from app.models.user import User
from app.services.chunking.pg_store import create_and_persist_hierarchy_build
from app.services.narrative_memory.audit import audit_assets
from app.services.narrative_memory.audit_contracts import (
    AssetKind,
    EligibilityReport,
    EligibilityStatus,
)
from app.services.narrative_memory.audit_pg import PostgresAuditSource
from app.services.narrative_memory.authority import (
    CandidateAuthority,
    CandidateConflictError,
    EligibilityRejectedError,
    ScopeMismatchError,
)
from app.services.narrative_memory.contracts import (
    CandidatePackage,
    CandidateVersionSpec,
    ModelLineage,
)


pytestmark = [pytest.mark.integration]


async def _seed_exact_report(session):
    user = User(
        username="contract-authority",
        email="contract-authority@example.com",
        hashed_password="x",
    )
    session.add(user)
    await session.flush()
    novel = Novel(owner_id=user.id, title="Contract Novel", status="ready")
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
        for number, content in ((1, "甲乙丙丁戊己"), (2, "庚辛壬癸子丑"))
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
    return user, novel, report


def _version_spec(version_key: str = "memory-v1") -> CandidateVersionSpec:
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


async def _candidate_package(
    session, version: NarrativeMemoryVersion
) -> CandidatePackage:
    evidence = await session.scalar(
        select(ChunkHierarchyNode)
        .where(
            ChunkHierarchyNode.build_id == version.hierarchy_build_id,
            ChunkHierarchyNode.level == "evidence",
            ChunkHierarchyNode.chapter_number == 1,
        )
        .order_by(ChunkHierarchyNode.order_index, ChunkHierarchyNode.id)
    )
    assert evidence is not None
    raw = {
        "nodes": [
            {
                "node_kind": "global_story",
                "node_key": "global",
                "chapter_start": 1,
                "chapter_end": 2,
                "schema_version": "memory-node.v1",
            },
            {
                "node_kind": "story_arc",
                "node_key": "arc:1",
                "chapter_start": 1,
                "chapter_end": 2,
                "schema_version": "memory-node.v1",
            },
            {
                "node_kind": "chapter_state",
                "node_key": "chapter:1",
                "chapter_start": 1,
                "chapter_end": 1,
                "schema_version": "memory-node.v1",
            },
        ],
        "claims": [
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
        ],
        "edges": [
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
        ],
        "source_links": [
            {
                "source_key": "source:1",
                "claim_key": "claim:1",
                "source_kind": "hierarchy",
                "hierarchy_build_id": version.hierarchy_build_id,
                "evidence_node_id": evidence.node_id,
                "chapter_id": evidence.chapter_id,
                "chapter_number": evidence.chapter_number,
                "source_start": evidence.source_start,
                "source_end": evidence.source_end,
                "content_hash": evidence.content_hash,
                "source_snapshot_hash": version.source_snapshot_hash,
            }
        ],
    }
    return CandidatePackage.model_validate_json(json.dumps(raw, ensure_ascii=False))


@pytest.mark.asyncio
async def test_exact_report_creates_explicit_version_and_persists_strict_package(
    audit_pg_session,
):
    user, novel, report = await _seed_exact_report(audit_pg_session)
    authority = CandidateAuthority(audit_pg_session)

    version = await authority.create_version(
        owner_id=user.id,
        novel_id=novel.id,
        spec=_version_spec(),
        eligibility_report=report,
    )
    package = await _candidate_package(audit_pg_session, version)
    persisted = await authority.persist_package(
        owner_id=user.id,
        novel_id=novel.id,
        version_id=version.id,
        package=package,
    )
    await audit_pg_session.flush()

    hierarchy = next(
        asset for asset in report.assets if asset.kind == AssetKind.HIERARCHY
    )
    build = await audit_pg_session.scalar(
        select(ChunkBuild).where(ChunkBuild.build_id == hierarchy.version_id)
    )
    assert build is not None
    assert version.hierarchy_build_id == hierarchy.version_id
    assert version.source_snapshot_hash == build.source_snapshot_hash
    assert version.hierarchy_checksum == build.manifest_checksum
    assert version.eligibility_policy_version == report.policy_version
    assert persisted.version_id == version.id
    assert len(persisted.node_ids) == 3
    assert len(persisted.claim_ids) == 1
    assert (
        await audit_pg_session.scalar(
            select(func.count()).select_from(NarrativeMemoryNode)
        )
        == 3
    )
    assert (
        await audit_pg_session.scalar(
            select(func.count()).select_from(NarrativeMemoryClaim)
        )
        == 1
    )
    assert (
        await audit_pg_session.scalar(
            select(func.count()).select_from(NarrativeMemoryEdge)
        )
        == 2
    )
    assert (
        await audit_pg_session.scalar(
            select(func.count()).select_from(NarrativeMemorySourceLink)
        )
        == 1
    )


@pytest.mark.asyncio
async def test_exact_retry_is_idempotent_but_conflicting_retry_fails(audit_pg_session):
    user, novel, report = await _seed_exact_report(audit_pg_session)
    authority = CandidateAuthority(audit_pg_session)
    version = await authority.create_version(
        owner_id=user.id,
        novel_id=novel.id,
        spec=_version_spec(),
        eligibility_report=report,
    )
    package = await _candidate_package(audit_pg_session, version)
    first = await authority.persist_package(
        owner_id=user.id,
        novel_id=novel.id,
        version_id=version.id,
        package=package,
    )
    second_version = await authority.create_version(
        owner_id=user.id,
        novel_id=novel.id,
        spec=_version_spec(),
        eligibility_report=report,
    )
    second = await authority.persist_package(
        owner_id=user.id,
        novel_id=novel.id,
        version_id=version.id,
        package=package,
    )

    assert second_version.id == version.id
    assert second == first

    with pytest.raises(CandidateConflictError):
        await authority.create_version(
            owner_id=user.id,
            novel_id=novel.id,
            spec=_version_spec().model_copy(update={"prompt_hash": "f" * 64}),
            eligibility_report=report,
        )


@pytest.mark.asyncio
async def test_non_exact_or_wrong_scope_report_creates_no_candidate(audit_pg_session):
    user, novel, report = await _seed_exact_report(audit_pg_session)
    authority = CandidateAuthority(audit_pg_session)
    raw = report.model_dump(mode="json")
    for asset in raw["assets"]:
        if asset["kind"] == AssetKind.HIERARCHY.value:
            asset["status"] = EligibilityStatus.REBUILD_REQUIRED.value
            asset["reason_codes"] = ["manifest_mismatch"]
    raw["provider_calls_allowed"] = False
    rebuild_report = EligibilityReport.model_validate(raw)

    with pytest.raises(EligibilityRejectedError):
        await authority.create_version(
            owner_id=user.id,
            novel_id=novel.id,
            spec=_version_spec(),
            eligibility_report=rebuild_report,
        )
    with pytest.raises(ScopeMismatchError):
        await authority.create_version(
            owner_id=user.id + 100,
            novel_id=novel.id,
            spec=_version_spec(),
            eligibility_report=report,
        )

    assert (
        await audit_pg_session.scalar(
            select(func.count()).select_from(NarrativeMemoryVersion)
        )
        == 0
    )


def test_authority_has_no_provider_worker_pointer_or_consumer_capability() -> None:
    path = (
        Path(__file__).resolve().parents[3]
        / "app"
        / "services"
        / "narrative_memory"
        / "authority.py"
    )
    source = path.read_text(encoding="utf-8").lower()
    forbidden = (
        "model_gateway",
        "provider",
        "worker",
        "dispatch",
        "activepointer",
        "active_pointer",
        "promotion",
        "rollback",
        "chroma",
        "reader_chat",
        "resolve_current",
        "resolve_active",
    )

    assert all(token not in source for token in forbidden)
