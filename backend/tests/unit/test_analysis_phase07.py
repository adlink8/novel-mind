"""Phase 07 structural analysis unit tests (no LLM / no DB hierarchy required)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novel import Chapter, Novel
from app.models.text_chunk import TextChunk
from app.models.user import User
from app.services.analysis_service import SUPPORTED_TYPES, build_structural_result, ensure_hierarchy

pytestmark = pytest.mark.unit


def test_supported_types_include_hierarchy_map():
    assert "hierarchy_map" in SUPPORTED_TYPES
    assert "plot_summary" in SUPPORTED_TYPES


def test_plot_summary_from_scenes():
    novel = Novel(title="测试书", owner_id=1)
    scenes = [
        {
            "scene_id": f"s{i}",
            "chapter_id": 1 + i // 3,
            "chapter_number": 1 + i // 3,
            "order_index": i,
            "char_count": 100 + i,
            "evidence_count": 2,
            "preview": f"场景预览{i} " * 5,
        }
        for i in range(12)
    ]
    data = build_structural_result(
        novel=novel,
        analysis_type="plot_summary",
        scenes=scenes,
        chapter_id=None,
        build_id="cb_test",
    )
    assert data["source"] == "phase07_hierarchy"
    assert data["scene_count"] == 12
    assert data["beats"]
    assert data["llm_enriched"] is False


def test_hierarchy_map_lists_scenes():
    novel = Novel(title="地图", owner_id=1)
    scenes = [
        {
            "scene_id": "s0",
            "chapter_id": 1,
            "chapter_number": 1,
            "order_index": 0,
            "char_count": 50,
            "evidence_count": 1,
            "preview": "开场",
        }
    ]
    data = build_structural_result(
        novel=novel,
        analysis_type="hierarchy_map",
        scenes=scenes,
        chapter_id=None,
        build_id="cb_x",
    )
    assert data["scenes"][0]["scene_id"] == "s0"


@pytest.mark.asyncio
async def test_ensure_hierarchy_raises_when_no_text_chunks(db_session: AsyncSession):
    """Fail-closed (Bug 4): never promote a committed hierarchy build with 0 text_chunks."""
    user = User(username="fc_user", email="fc@test.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()
    novel = Novel(owner_id=user.id, title="无 chunk 小说", status="ready")
    db_session.add(novel)
    await db_session.flush()
    db_session.add(
        Chapter(
            novel_id=novel.id,
            chapter_number=1,
            title="第一章",
            content="开场内容。" * 30,
            word_count=120,
        )
    )
    await db_session.commit()

    with pytest.raises(ValueError, match="text_chunks"):
        await ensure_hierarchy(db_session, novel, force=True)


@pytest.mark.asyncio
async def test_ensure_hierarchy_returns_existing_active_without_chunks(
    db_session: AsyncSession,
):
    """Existing active build fast-path stays permissive: no promote, no raise."""
    user = User(username="fc_user2", email="fc2@test.com", hashed_password="x")
    db_session.add(user)
    await db_session.flush()
    novel = Novel(owner_id=user.id, title="无 chunk 但有 active", status="ready")
    db_session.add(novel)
    await db_session.flush()

    from datetime import datetime, timezone

    from app.models.chunk_build import ChunkActivePointer, ChunkBuild

    db_session.add(
        ChunkBuild(
            build_id="cb_existing",
            novel_id=novel.id,
            status="committed",
            source_snapshot_hash="a" * 64,
            manifest_checksum="b" * 64,
            chunker_name="t",
            chunker_version="1",
            chunker_config_hash="c" * 64,
            collection_name="t",
            is_candidate=False,
            immutable=True,
        )
    )
    db_session.add(
        ChunkActivePointer(
            novel_id=novel.id,
            build_id="cb_existing",
            committed_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()

    build_id = await ensure_hierarchy(db_session, novel, force=False)
    assert build_id == "cb_existing"
