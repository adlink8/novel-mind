from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.chunk_build import ChunkActivePointer, ChunkHierarchyNode
from app.models.novel import Chapter, Novel
from app.models.user import User
from app.services.chunking.pg_store import create_and_persist_hierarchy_build
from app.services.narrative_memory.audit import audit_assets
from app.services.narrative_memory.audit_contracts import (
    AssetKind,
    EligibilityStatus,
    ReasonCode,
)
from app.services.narrative_memory.audit_pg import PostgresAuditSource


pytestmark = pytest.mark.integration


async def _seed_valid_hierarchy(
    db_session, contents: tuple[str, str] = ("甲乙丙丁戊己", "庚辛壬癸子丑")
):
    user = User(username="audit-owner", email="audit@example.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()
    novel = Novel(owner_id=user.id, title="Audit Novel", status="ready")
    db_session.add(novel)
    await db_session.flush()
    chapters = [
        Chapter(novel_id=novel.id, chapter_number=1, title="一", content=contents[0], word_count=len(contents[0])),
        Chapter(novel_id=novel.id, chapter_number=2, title="二", content=contents[1], word_count=len(contents[1])),
    ]
    db_session.add_all(chapters)
    await db_session.flush()
    await create_and_persist_hierarchy_build(
        db_session,
        novel_id=novel.id,
        chapters=[
            {"chapter_id": ch.id, "chapter_number": ch.chapter_number, "content": ch.content}
            for ch in chapters
        ],
        promote_active=True,
        force_full=True,
    )
    await db_session.flush()
    return user, novel, chapters


@pytest.mark.asyncio
async def test_valid_hierarchy_is_exact_and_optional_sources_are_unavailable(audit_pg_session):
    user, novel, _ = await _seed_valid_hierarchy(audit_pg_session)

    report = await audit_assets(
        PostgresAuditSource(audit_pg_session), owner_id=user.id, novel_id=novel.id
    )
    by_kind = {asset.kind: asset for asset in report.assets}

    assert by_kind[AssetKind.HIERARCHY].status == EligibilityStatus.REUSABLE_EXACT, by_kind[AssetKind.HIERARCHY].reason_codes
    assert report.provider_calls_allowed is True
    for kind in (AssetKind.TIMELINE, AssetKind.RELATIONSHIP, AssetKind.CLUE):
        assert by_kind[kind].status == EligibilityStatus.OPTIONAL_UNAVAILABLE


@pytest.mark.asyncio
async def test_deferred_chapter_content_is_loaded_without_async_lazy_io(audit_pg_session):
    user, novel, _ = await _seed_valid_hierarchy(audit_pg_session)
    owner_id, novel_id = user.id, novel.id
    audit_pg_session.expire_all()

    report = await audit_assets(
        PostgresAuditSource(audit_pg_session), owner_id=owner_id, novel_id=novel_id
    )

    assert report.provider_calls_allowed is True


@pytest.mark.asyncio
async def test_content_hash_mismatch_reports_only_affected_chapter(audit_pg_session):
    user, novel, chapters = await _seed_valid_hierarchy(audit_pg_session)
    evidence = await audit_pg_session.scalar(
        select(ChunkHierarchyNode).where(
            ChunkHierarchyNode.novel_id == novel.id,
            ChunkHierarchyNode.chapter_id == chapters[1].id,
            ChunkHierarchyNode.level == "evidence",
        )
    )
    evidence.content_hash = "0" * 64
    await audit_pg_session.flush()

    report = await audit_assets(
        PostgresAuditSource(audit_pg_session), owner_id=user.id, novel_id=novel.id
    )
    hierarchy = next(a for a in report.assets if a.kind == AssetKind.HIERARCHY)

    assert hierarchy.status == EligibilityStatus.REBUILD_REQUIRED
    assert ReasonCode.CONTENT_HASH_MISMATCH in hierarchy.reason_codes
    assert [(r.start_chapter, r.end_chapter) for r in hierarchy.rebuild_ranges] == [(2, 2)]
    assert report.provider_calls_allowed is False


@pytest.mark.asyncio
async def test_normalized_multi_span_content_is_not_claimed_as_exact(audit_pg_session):
    user, novel, _ = await _seed_valid_hierarchy(
        audit_pg_session, ("甲乙丙。丁戊己。", "庚辛壬。癸子丑。")
    )

    report = await audit_assets(
        PostgresAuditSource(audit_pg_session), owner_id=user.id, novel_id=novel.id
    )
    hierarchy = next(a for a in report.assets if a.kind == AssetKind.HIERARCHY)

    assert hierarchy.status == EligibilityStatus.REBUILD_REQUIRED
    assert ReasonCode.CONTENT_HASH_MISMATCH in hierarchy.reason_codes
    assert report.provider_calls_allowed is False


@pytest.mark.asyncio
async def test_missing_pointer_blocks_without_repair(audit_pg_session):
    user, novel, _ = await _seed_valid_hierarchy(audit_pg_session)
    pointer = await audit_pg_session.scalar(
        select(ChunkActivePointer).where(ChunkActivePointer.novel_id == novel.id)
    )
    await audit_pg_session.delete(pointer)
    await audit_pg_session.flush()

    report = await audit_assets(
        PostgresAuditSource(audit_pg_session), owner_id=user.id, novel_id=novel.id
    )
    hierarchy = next(a for a in report.assets if a.kind == AssetKind.HIERARCHY)
    assert hierarchy.status == EligibilityStatus.BLOCKED
    assert ReasonCode.ACTIVE_VERSION_MISSING in hierarchy.reason_codes


@pytest.mark.asyncio
async def test_cross_owner_request_discloses_no_build_identity(audit_pg_session):
    _, novel, _ = await _seed_valid_hierarchy(audit_pg_session)
    other = User(username="audit-other", email="other@example.com", hashed_password="x")
    audit_pg_session.add(other)
    await audit_pg_session.flush()

    inventories = await PostgresAuditSource(audit_pg_session).inventory(
        owner_id=other.id, novel_id=novel.id
    )
    hierarchy = next(item for item in inventories if item.kind == AssetKind.HIERARCHY)
    assert hierarchy.available is False
    assert hierarchy.version_id is None
    assert hierarchy.source_snapshot_hash is None
    assert hierarchy.manifest_hash is None
    assert ReasonCode.SOURCE_MISSING in hierarchy.reason_codes


def test_rebuild_ranges_are_coalesced() -> None:
    assert [
        (item.start_chapter, item.end_chapter)
        for item in PostgresAuditSource._coalesce_ranges({1, 2, 3, 5, 7, 8})
    ] == [(1, 3), (5, 5), (7, 8)]
