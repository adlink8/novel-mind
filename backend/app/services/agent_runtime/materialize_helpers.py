"""backfill 物化共用辅助：从 chat 同源权威解析 version_id / snapshot_hash。

world_model_knowledge 物化必须与下一轮问答检索使用同一快照口径——否则
world_projection_reader 会因 snapshot 不匹配抛 WorldProjectionUnavailableError，
闭环断裂。因此这里复用 reader_chat.retrieval 的 active analysis version +
build_source_snapshot。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.novel import Novel


async def resolve_active_analysis_version_id(
    session: AsyncSession, *, owner_id: int, novel_id: int
) -> int | None:
    """解析当前 active analysis version（与 chat 检索同源）。"""
    from app.services.reader_chat.retrieval import resolve_active_analysis_version

    return await resolve_active_analysis_version(
        session, owner_id=owner_id, novel_id=novel_id
    )


async def chat_snapshot_hash(
    session: AsyncSession, *, owner_id: int, novel_id: int, version_id: int
) -> str:
    """构造与 chat 路径同源的 source snapshot hash。"""
    from app.services.reader_chat.retrieval import build_source_snapshot

    novel = await session.scalar(
        select(Novel).where(
            Novel.id == novel_id,
            Novel.owner_id == owner_id,
        )
    )
    if novel is None:
        return ""
    snapshot = await build_source_snapshot(
        session,
        novel=novel,
        owner_id=owner_id,
        version_id=version_id,
        full_book_authorized=False,
    )
    return snapshot.snapshot_hash
