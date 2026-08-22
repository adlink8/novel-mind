"""Reading-domain default tool services for the agent-tools facade.

Extracted from the agent-tools facade (25.2-02 Domain Tool Contract / D-06 /
D-07): this module owns the default service entry for the read-only novel /
chapter / full-text search tools. Each ``_default_*`` seam reuses an existing
service entry (novel_service, production retrieval strategy) and never
re-implements owner / cutoff / budget logic — those enforcement points stay in
the facade's ``ToolFacade.execute``.
"""

from __future__ import annotations

from typing import Any

from app.services.novel_service import novel_service


async def _default_get_novel(db, novel_id: int):
    return await novel_service.get_novel(db, novel_id)


async def _default_get_chapter(db, chapter_id: int):
    return await novel_service.get_chapter(db, chapter_id)


async def _default_search_novel_text(
    db,
    *,
    owner_id: int,
    novel_id: int,
    query: str,
    mode: str,
    top_k: int,
) -> Any:
    from app.services.knowledge_units.search import production_retrieval_strategy

    strategy = production_retrieval_strategy()
    outcome = await strategy.resolve_novel(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        domain_profile="fiction",
        query=query,
        mode=mode,
        top_k=top_k,
    )
    return {
        "results": outcome.rows,
        "resolved_mode": outcome.resolved_mode,
        "fallback_reason": outcome.fallback_reason,
    }
