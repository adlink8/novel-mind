"""Analysis-domain default tool services for the agent-tools facade.

Extracted from the agent-tools facade (25.2-02 Domain Tool Contract / D-06 /
D-07): this module owns the default service entry for the read-only timeline /
relationships / clues / narrative-memory tools. Each seam delegates to the
existing domain query service (build_version_view, relationship graph query,
build_clue_envelope, structure query) with the facade-supplied scope
parameters; no owner / cutoff / full-book authorization is re-implemented here.
"""

from __future__ import annotations

from typing import Any

from app.models import Novel
from app.schemas.relationship import RelationshipVersionSource
from app.schemas.timeline import TimelineOrdering, TimelineVersionSource
from app.services.agent_tools.errors import InvalidInputError
from app.services.clues.query import build_clue_envelope
from app.services.narrative_memory.structure_query import (
    list_versions,
    load_structure_tree,
)
from app.services.relationships.query import relationship_graph_query_service
from app.services.timeline.query import build_version_view


async def _default_get_timeline(
    db,
    *,
    novel: Novel,
    owner_id: int,
    source: TimelineVersionSource,
    ordering: TimelineOrdering,
    person: str | None,
    include_causal: bool,
    request_full_book: bool,
    chapter_start: int | None,
    chapter_end: int | None,
):
    return await build_version_view(
        db,
        novel=novel,
        owner_id=owner_id,
        source=source,
        ordering=ordering,
        person=person,
        include_causal=include_causal,
        request_full_book=request_full_book,
        chapter_start=chapter_start,
        chapter_end=chapter_end,
    )


async def _default_get_relationships(
    db,
    *,
    novel: Novel,
    owner_id: int,
    source: RelationshipVersionSource,
    version_id: int | None,
    through_chapter: int | None,
    request_full_book: bool,
    character_id: int | None,
    relation_type: str | None,
    include_provisional: bool,
):
    return await relationship_graph_query_service.build_graph(
        db,
        novel=novel,
        owner_id=owner_id,
        source=source,
        version_id=version_id,
        through_chapter=through_chapter,
        request_full_book=request_full_book,
        character_id=character_id,
        relation_type=relation_type,
        include_provisional=include_provisional,
    )


async def _default_get_clues(
    db,
    *,
    novel: Novel,
    owner_id: int,
    request_full_book: bool,
    character_id: int | None,
    status_filter: str | None,
) -> dict[str, Any]:
    return await build_clue_envelope(
        db,
        novel=novel,
        owner_id=owner_id,
        request_full_book=request_full_book,
        character_id=character_id,
        status_filter=status_filter,
    )


async def _default_get_narrative_memory(
    db,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int | None,
    view: str,
    through_chapter: int | None,
) -> Any:
    if view == "versions":
        return await list_versions(db, owner_id=owner_id, novel_id=novel_id)
    if view == "tree":
        if version_id is None:
            raise InvalidInputError("narrative_memory tree 视图需要 version_id")
        return await load_structure_tree(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            through_chapter=through_chapter,
        )
    raise InvalidInputError(f"不支持的 narrative_memory 视图: {view!r}")
