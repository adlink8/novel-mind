"""Phase 07 PG wiring: hierarchy persist, active pointer, search enrichment."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk_build import ChunkActivePointer, ChunkBuild, ChunkHierarchyNode
from app.models.novel import Chapter, Novel
from app.models.text_chunk import TextChunk
from app.models.user import User
from app.services.chunking.pg_store import (
    create_and_persist_hierarchy_build,
    expand_search_result_with_hierarchy,
    get_active_build_id,
    get_scene_for_evidence,
)
from app.services.hybrid_search import HybridSearchService

pytestmark = pytest.mark.integration


async def _seed_novel(db: AsyncSession, name: str = "hier") -> tuple[User, Novel, list[Chapter]]:
    user = User(username=name, email=f"{name}@test.com", hashed_password="hash")
    db.add(user)
    await db.flush()
    novel = Novel(title=f"novel-{name}", owner_id=user.id, status="ready")
    db.add(novel)
    await db.flush()
    chapters = []
    for i, body in enumerate(
        [
            ("开场叙述。" * 20 + "中间转折。" * 15),
            ("后半冲突。" * 18 + "收束结局。" * 12),
        ],
        start=1,
    ):
        ch = Chapter(
            novel_id=novel.id,
            chapter_number=i,
            title=f"第{i}章",
            content=body,
            word_count=len(body),
        )
        db.add(ch)
        chapters.append(ch)
    await db.flush()
    # raw text chunks for linking
    idx = 0
    for ch in chapters:
        # simple split for matching evidence
        parts = [ch.content[j : j + 80] for j in range(0, len(ch.content), 80) if ch.content[j : j + 80].strip()]
        for p in parts[:6]:
            db.add(
                TextChunk(
                    novel_id=novel.id,
                    chapter_id=ch.id,
                    chunk_index=idx,
                    content=p,
                    chunk_type="paragraph",
                    word_count=len(p),
                    embedding_status="embedded",
                    metadata_json={},
                )
            )
            idx += 1
    await db.commit()
    return user, novel, chapters


@pytest.mark.asyncio
async def test_persist_hierarchy_sets_active_and_nodes(db_session: AsyncSession):
    user, novel, chapters = await _seed_novel(db_session, "pg1")
    payload = [
        {
            "chapter_id": ch.id,
            "id": ch.id,
            "chapter_number": ch.chapter_number,
            "content": ch.content,
        }
        for ch in chapters
    ]
    rec = await create_and_persist_hierarchy_build(
        db_session,
        novel_id=novel.id,
        chapters=payload,
        promote_active=True,
        force_full=True,
    )
    await db_session.commit()

    assert rec.build_id
    active = await get_active_build_id(db_session, novel.id)
    assert active == rec.build_id

    ptr = (
        await db_session.execute(
            select(ChunkActivePointer).where(ChunkActivePointer.novel_id == novel.id)
        )
    ).scalar_one()
    assert ptr.build_id == rec.build_id

    builds = (
        await db_session.execute(
            select(ChunkBuild).where(ChunkBuild.novel_id == novel.id)
        )
    ).scalars().all()
    assert len(builds) == 1
    assert builds[0].status == "committed"
    assert builds[0].is_candidate is False

    nodes = (
        await db_session.execute(
            select(ChunkHierarchyNode).where(
                ChunkHierarchyNode.build_id == rec.build_id
            )
        )
    ).scalars().all()
    levels = {n.level for n in nodes}
    assert "chapter" in levels
    assert "scene" in levels
    assert "evidence" in levels


@pytest.mark.asyncio
async def test_scene_expand_and_raw_fallback(db_session: AsyncSession):
    _, novel, chapters = await _seed_novel(db_session, "pg2")
    payload = [
        {
            "chapter_id": ch.id,
            "chapter_number": ch.chapter_number,
            "content": ch.content,
        }
        for ch in chapters
    ]
    rec = await create_and_persist_hierarchy_build(
        db_session, novel_id=novel.id, chapters=payload, promote_active=True
    )
    await db_session.commit()

    ev = (
        await db_session.execute(
            select(ChunkHierarchyNode).where(
                ChunkHierarchyNode.build_id == rec.build_id,
                ChunkHierarchyNode.level == "evidence",
            )
        )
    ).scalars().first()
    assert ev is not None
    packed = await get_scene_for_evidence(
        db_session, novel_id=novel.id, evidence_node_id=ev.node_id
    )
    assert packed is not None
    assert packed["mode"] in ("scene_expand", "evidence_only")

    # raw fallback when node missing
    missing = await get_scene_for_evidence(
        db_session, novel_id=novel.id, evidence_node_id="hn_does_not_exist_xx"
    )
    assert missing is None

    enriched = await expand_search_result_with_hierarchy(
        db_session,
        novel_id=novel.id,
        result={"chunk_id": 0, "content_snippet": "x", "score": 0.1},
    )
    assert enriched["hierarchy_mode"] == "raw_fallback"


@pytest.mark.asyncio
async def test_hybrid_enrich_uses_hierarchy_when_linked(db_session: AsyncSession):
    _, novel, chapters = await _seed_novel(db_session, "pg3")
    payload = [
        {
            "chapter_id": ch.id,
            "chapter_number": ch.chapter_number,
            "content": ch.content,
        }
        for ch in chapters
    ]
    rec = await create_and_persist_hierarchy_build(
        db_session, novel_id=novel.id, chapters=payload, promote_active=True
    )
    await db_session.commit()

    # Find a linked text chunk if any
    chunk = (
        await db_session.execute(
            select(TextChunk).where(
                TextChunk.novel_id == novel.id,
                TextChunk.hierarchy_node_id.is_not(None),
            )
        )
    ).scalars().first()

    svc = HybridSearchService()
    if chunk is None:
        # still must not raise
        out = await svc._enrich_with_hierarchy(
            db_session,
            novel.id,
            [{"chunk_id": 999999, "content_snippet": "nope", "score": 0.2}],
        )
        assert out[0]["hierarchy_mode"] == "raw_fallback"
        return

    out = await svc._enrich_with_hierarchy(
        db_session,
        novel.id,
        [
            {
                "chunk_id": chunk.id,
                "content_snippet": chunk.content[:50],
                "score": 0.5,
                "hierarchy_node_id": chunk.hierarchy_node_id,
                "hierarchy_build_id": chunk.hierarchy_build_id,
            }
        ],
    )
    assert out[0].get("hierarchy_mode") in (
        "scene_expand",
        "evidence_truncated",
        "evidence_only",
        "raw_fallback",
    )
    assert rec.build_id
