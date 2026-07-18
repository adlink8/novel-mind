"""One-shot probe for NM Vertex transport (operator debug)."""
from __future__ import annotations

import asyncio
import logging
import traceback

logging.disable(logging.WARNING)


async def main() -> None:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings
    from app.models.chunk_build import ChunkHierarchyNode
    from app.models.novel import Chapter
    from scripts.run_narrative_memory_build import _VertexNmTransport

    url = settings.database_url
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    eng = create_async_engine(url)
    sessions = async_sessionmaker(eng, expire_on_commit=False)
    t = _VertexNmTransport(sessions, model=settings.vertex_model)
    async with sessions() as s:
        ch = (
            await s.scalars(
                select(Chapter)
                .where(Chapter.novel_id == 91)
                .order_by(Chapter.chapter_number)
                .limit(1)
            )
        ).first()
        assert ch is not None
        leaves = (
            await s.scalars(
                select(ChunkHierarchyNode)
                .where(
                    ChunkHierarchyNode.build_id == "cb_9f9aee6bf1cb427b",
                    ChunkHierarchyNode.chapter_id == ch.id,
                    ChunkHierarchyNode.level == "evidence",
                )
                .order_by(ChunkHierarchyNode.order_index)
                .limit(3)
            )
        ).all()
        payload = {
            "chapter_id": ch.id,
            "chapter_number": ch.chapter_number,
            "hierarchy_build_id": "cb_9f9aee6bf1cb427b",
            "evidence_leaves": [
                {
                    "evidence_node_id": leaf.node_id,
                    "chapter_id": leaf.chapter_id,
                    "chapter_number": leaf.chapter_number,
                    "source_start": leaf.source_start,
                    "source_end": leaf.source_end,
                    "content_hash": leaf.content_hash,
                }
                for leaf in leaves
            ],
            "optional_signals": [],
        }
        print("chapter", ch.id, ch.chapter_number, "leaves", len(leaves))
    try:
        out = await t.complete(
            stage_key=f"chapter_state:{payload['chapter_id']}",
            payload=payload,
            deployment={"model": settings.vertex_model},
            repair=False,
        )
        print("OK keys", list(out.keys()))
        print("claims", len(out.get("claims") or []))
        print("label", out.get("display_label"))
        print("usage", out.get("usage"))
        print("sample claim", (out.get("claims") or [None])[0])
    except Exception:
        traceback.print_exc()
    await eng.dispose()


if __name__ == "__main__":
    asyncio.run(main())
