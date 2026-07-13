"""PostgreSQL-backed chunk build / hierarchy / active pointer store (Phase 07 wiring)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chunk_build import ChunkActivePointer, ChunkBuild, ChunkHierarchyNode
from app.models.text_chunk import TextChunk
from app.services.chunking.hierarchy import build_chapter_hierarchy
from app.services.chunking.incremental import plan_incremental_delta
from app.services.chunking.schemas import (
    ChunkBuildRecord,
    HierarchyNode,
    HierarchyTree,
)
from app.services.rag_fixture import stable_hash


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _record_from_row(row: ChunkBuild) -> ChunkBuildRecord:
    return ChunkBuildRecord(
        build_id=row.build_id,
        novel_id=row.novel_id,
        status=row.status,  # type: ignore[arg-type]
        parent_build_id=row.parent_build_id,
        source_snapshot_hash=row.source_snapshot_hash,
        manifest_checksum=row.manifest_checksum,
        chunker_name=row.chunker_name,
        chunker_version=row.chunker_version,
        chunker_config_hash=row.chunker_config_hash,
        collection_name=row.collection_name,
        is_candidate=row.is_candidate,
        immutable=row.immutable,
        changed_chapter_ids=list(row.changed_chapter_ids or []),
        journal=list(row.journal or []),
    )


def _node_from_row(row: ChunkHierarchyNode) -> HierarchyNode:
    return HierarchyNode(
        node_id=row.node_id,
        level=row.level,  # type: ignore[arg-type]
        chapter_id=row.chapter_id,
        chapter_number=row.chapter_number,
        parent_id=row.parent_id,
        child_ids=list(row.child_ids or []),
        content=row.content or "",
        content_hash=row.content_hash,
        source_start=row.source_start,
        source_end=row.source_end,
        chunk_type=row.chunk_type or "paragraph",
        decision_lineage=list(row.decision_lineage or []),
        order_index=row.order_index,
    )


async def get_active_build_id(session: AsyncSession, novel_id: int) -> str | None:
    row = (
        await session.execute(
            select(ChunkActivePointer).where(ChunkActivePointer.novel_id == novel_id)
        )
    ).scalar_one_or_none()
    return row.build_id if row else None


async def get_build(session: AsyncSession, build_id: str) -> ChunkBuildRecord | None:
    row = (
        await session.execute(select(ChunkBuild).where(ChunkBuild.build_id == build_id))
    ).scalar_one_or_none()
    return _record_from_row(row) if row else None


async def load_hierarchy_trees(
    session: AsyncSession, build_id: str
) -> list[HierarchyTree]:
    rows = (
        await session.execute(
            select(ChunkHierarchyNode)
            .where(ChunkHierarchyNode.build_id == build_id)
            .order_by(
                ChunkHierarchyNode.chapter_id,
                ChunkHierarchyNode.level,
                ChunkHierarchyNode.order_index,
            )
        )
    ).scalars().all()
    if not rows:
        return []

    by_chapter: dict[int, list[ChunkHierarchyNode]] = {}
    for r in rows:
        by_chapter.setdefault(r.chapter_id, []).append(r)

    trees: list[HierarchyTree] = []
    for chapter_id, chapter_rows in sorted(by_chapter.items()):
        nodes = [_node_from_row(r) for r in chapter_rows]
        chapter_node = next((n for n in nodes if n.level == "chapter"), None)
        if chapter_node is None:
            continue
        novel_id = chapter_rows[0].novel_id
        checksum = stable_hash(
            {
                "build_id": build_id,
                "chapter_id": chapter_id,
                "node_ids": [n.node_id for n in nodes],
            }
        )
        trees.append(
            HierarchyTree(
                novel_id=novel_id,
                chapter_id=chapter_id,
                chapter_number=chapter_node.chapter_number,
                source_snapshot_hash=None,
                nodes=nodes,
                chapter_node_id=chapter_node.node_id,
                tree_checksum=checksum,
            )
        )
    return trees


async def _persist_build_row(
    session: AsyncSession,
    rec: ChunkBuildRecord,
    *,
    vector_ids: list[str],
) -> ChunkBuild:
    existing = (
        await session.execute(
            select(ChunkBuild).where(ChunkBuild.build_id == rec.build_id)
        )
    ).scalar_one_or_none()
    if existing:
        existing.status = rec.status
        existing.is_candidate = rec.is_candidate
        existing.journal = list(rec.journal)
        existing.vector_ids = list(vector_ids)
        existing.changed_chapter_ids = list(rec.changed_chapter_ids)
        return existing

    row = ChunkBuild(
        build_id=rec.build_id,
        novel_id=rec.novel_id,
        status=rec.status,
        parent_build_id=rec.parent_build_id,
        source_snapshot_hash=rec.source_snapshot_hash,
        manifest_checksum=rec.manifest_checksum,
        chunker_name=rec.chunker_name,
        chunker_version=rec.chunker_version,
        chunker_config_hash=rec.chunker_config_hash,
        collection_name=rec.collection_name,
        is_candidate=rec.is_candidate,
        immutable=rec.immutable,
        changed_chapter_ids=list(rec.changed_chapter_ids),
        journal=list(rec.journal),
        vector_ids=list(vector_ids),
    )
    session.add(row)
    return row


async def _persist_hierarchy_nodes(
    session: AsyncSession,
    *,
    build_id: str,
    novel_id: int,
    trees: list[HierarchyTree],
) -> list[str]:
    """Replace nodes for build_id; return evidence node_ids."""
    await session.execute(
        delete(ChunkHierarchyNode).where(ChunkHierarchyNode.build_id == build_id)
    )
    evidence_ids: list[str] = []
    for tree in trees:
        for n in tree.nodes:
            session.add(
                ChunkHierarchyNode(
                    build_id=build_id,
                    novel_id=novel_id,
                    node_id=n.node_id,
                    level=n.level,
                    chapter_id=n.chapter_id,
                    chapter_number=n.chapter_number,
                    parent_id=n.parent_id,
                    child_ids=list(n.child_ids),
                    content=n.content,
                    content_hash=n.content_hash,
                    source_start=n.source_start,
                    source_end=n.source_end,
                    chunk_type=n.chunk_type,
                    decision_lineage=list(n.decision_lineage),
                    order_index=n.order_index,
                )
            )
            if n.level == "evidence":
                evidence_ids.append(n.node_id)
    return evidence_ids


async def set_active_pointer(
    session: AsyncSession, *, novel_id: int, build_id: str
) -> None:
    row = (
        await session.execute(
            select(ChunkActivePointer).where(ChunkActivePointer.novel_id == novel_id)
        )
    ).scalar_one_or_none()
    now = _utcnow()
    if row is None:
        session.add(
            ChunkActivePointer(
                novel_id=novel_id, build_id=build_id, committed_at=now
            )
        )
    else:
        row.build_id = build_id
        row.committed_at = now

    # Mark build non-candidate committed
    build = (
        await session.execute(select(ChunkBuild).where(ChunkBuild.build_id == build_id))
    ).scalar_one_or_none()
    if build:
        build.is_candidate = False
        build.status = "committed"
        journal = list(build.journal or [])
        journal.append(
            {
                "event": "committed",
                "at": now.isoformat(),
            }
        )
        build.journal = journal


async def create_and_persist_hierarchy_build(
    session: AsyncSession,
    *,
    novel_id: int,
    chapters: list[dict[str, Any]],
    source_snapshot_hash: str | None = None,
    chunker_name: str = "hierarchical-v1",
    chunker_version: str = "1.0.0",
    chunker_config: dict[str, Any] | None = None,
    promote_active: bool = True,
    force_full: bool = True,
) -> ChunkBuildRecord:
    """Build hierarchy trees, persist to PG, optionally set active pointer.

    Does not delete raw text_chunks. Evidence nodes are linked onto matching
    text_chunks when content matches; raw fallback remains.
    """
    cfg = chunker_config or {"min": 300, "max": 500}
    cfg_hash = stable_hash(cfg)
    snap = source_snapshot_hash or stable_hash(
        {
            "novel_id": novel_id,
            "chapters": [
                {
                    "id": c.get("chapter_id") or c.get("id"),
                    "h": stable_hash({"c": c.get("content") or ""}),
                }
                for c in chapters
            ],
        }
    )

    active_id = await get_active_build_id(session, novel_id)
    parent_rec = await get_build(session, active_id) if active_id else None

    prev_chapter_hashes: dict[int, str] = {}
    prev_chunker = None
    if parent_rec:
        prev_chunker = {
            "chunker_name": parent_rec.chunker_name,
            "chunker_version": parent_rec.chunker_version,
            "chunker_config_hash": parent_rec.chunker_config_hash,
        }
        for entry in parent_rec.journal:
            if entry.get("event") == "chapter_hash":
                prev_chapter_hashes[int(entry["chapter_id"])] = entry["content_hash"]

    current_hashes = {
        int(ch.get("chapter_id") or ch.get("id")): stable_hash(
            {"c": ch.get("content") or ""}
        )
        for ch in chapters
    }
    delta = plan_incremental_delta(
        prev_chapter_hashes=prev_chapter_hashes,
        current_chapter_hashes=current_hashes,
        prev_chunker_lineage=prev_chunker,
        current_chunker_lineage={
            "chunker_name": chunker_name,
            "chunker_version": chunker_version,
            "chunker_config_hash": cfg_hash,
        },
        force_full=force_full or parent_rec is None,
    )

    build_id = f"cb_{uuid.uuid4().hex[:16]}"
    collection = f"candidate_{novel_id}_{build_id}"
    trees: list[HierarchyTree] = []
    journal: list[dict[str, Any]] = [
        {"event": "created", "at": _utcnow().isoformat(), "delta": delta.__dict__},
    ]

    parent_trees: dict[int, HierarchyTree] = {}
    if parent_rec and not delta.full:
        for t in await load_hierarchy_trees(session, parent_rec.build_id):
            parent_trees[t.chapter_id] = t

    if delta.no_op and parent_rec:
        trees = list(parent_trees.values()) or await load_hierarchy_trees(
            session, parent_rec.build_id
        )
        journal.append({"event": "noop_carry_forward", "at": _utcnow().isoformat()})
    else:
        changed = set(delta.changed_chapter_ids)
        for ch in chapters:
            cid = int(ch.get("chapter_id") or ch.get("id"))
            cnum = int(ch.get("chapter_number") or 1)
            content = ch.get("content") or ""
            if cid in changed or delta.full:
                trees.append(
                    build_chapter_hierarchy(
                        novel_id=novel_id,
                        chapter_id=cid,
                        chapter_number=cnum,
                        content=content,
                        source_snapshot_hash=snap,
                    )
                )
            elif cid in parent_trees:
                trees.append(parent_trees[cid])

    for ch in chapters:
        cid = int(ch.get("chapter_id") or ch.get("id"))
        journal.append(
            {
                "event": "chapter_hash",
                "chapter_id": cid,
                "content_hash": current_hashes[cid],
            }
        )

    manifest_checksum = stable_hash(
        {
            "build_id": build_id,
            "trees": [t.tree_checksum for t in trees],
            "snapshot": snap,
            "cfg": cfg_hash,
        }
    )
    rec = ChunkBuildRecord(
        build_id=build_id,
        novel_id=novel_id,
        status="built",
        parent_build_id=parent_rec.build_id if parent_rec else None,
        source_snapshot_hash=snap,
        manifest_checksum=manifest_checksum,
        chunker_name=chunker_name,
        chunker_version=chunker_version,
        chunker_config_hash=cfg_hash,
        collection_name=collection,
        is_candidate=not promote_active,
        immutable=True,
        changed_chapter_ids=list(delta.changed_chapter_ids),
        journal=journal,
    )

    evidence_ids = await _persist_hierarchy_nodes(
        session, build_id=build_id, novel_id=novel_id, trees=trees
    )
    await _persist_build_row(session, rec, vector_ids=evidence_ids)

    # Link evidence nodes onto raw text_chunks by content equality (best effort)
    await _link_evidence_to_text_chunks(
        session, novel_id=novel_id, build_id=build_id, trees=trees
    )

    if promote_active:
        await set_active_pointer(session, novel_id=novel_id, build_id=build_id)
        rec = rec.model_copy(
            update={"status": "committed", "is_candidate": False}
        )

    await session.flush()
    return rec


async def _link_evidence_to_text_chunks(
    session: AsyncSession,
    *,
    novel_id: int,
    build_id: str,
    trees: list[HierarchyTree],
) -> None:
    """Attach hierarchy lineage columns on matching text_chunks (non-destructive)."""
    chunks = (
        await session.execute(
            select(TextChunk).where(TextChunk.novel_id == novel_id)
        )
    ).scalars().all()
    by_content: dict[str, list[TextChunk]] = {}
    for c in chunks:
        by_content.setdefault((c.content or "").strip(), []).append(c)

    node_rows = (
        await session.execute(
            select(ChunkHierarchyNode).where(
                ChunkHierarchyNode.build_id == build_id,
                ChunkHierarchyNode.level == "evidence",
            )
        )
    ).scalars().all()

    for nr in node_rows:
        key = (nr.content or "").strip()
        matches = by_content.get(key) or []
        # Prefer same chapter when possible
        match = None
        for m in matches:
            if m.chapter_id == nr.chapter_id:
                match = m
                break
        if match is None and matches:
            match = matches[0]
        if match is None:
            continue
        match.hierarchy_node_id = nr.node_id
        match.hierarchy_level = "evidence"
        match.hierarchy_parent_id = nr.parent_id
        match.hierarchy_build_id = build_id
        match.source_start = nr.source_start
        match.source_end = nr.source_end
        meta = dict(match.metadata_json or {})
        meta.update(
            {
                "hierarchy_node_id": nr.node_id,
                "hierarchy_level": "evidence",
                "hierarchy_parent_id": nr.parent_id,
                "hierarchy_build_id": build_id,
                "source_start": nr.source_start,
                "source_end": nr.source_end,
            }
        )
        match.metadata_json = meta
        nr.text_chunk_id = match.id


async def get_scene_for_evidence(
    session: AsyncSession,
    *,
    novel_id: int,
    evidence_node_id: str,
    build_id: str | None = None,
) -> dict[str, Any] | None:
    """Load evidence + parent scene for retrieval expansion."""
    bid = build_id or await get_active_build_id(session, novel_id)
    if not bid:
        return None
    ev = (
        await session.execute(
            select(ChunkHierarchyNode).where(
                ChunkHierarchyNode.build_id == bid,
                ChunkHierarchyNode.node_id == evidence_node_id,
                ChunkHierarchyNode.level == "evidence",
            )
        )
    ).scalar_one_or_none()
    if ev is None:
        return None
    scene = None
    if ev.parent_id:
        scene = (
            await session.execute(
                select(ChunkHierarchyNode).where(
                    ChunkHierarchyNode.build_id == bid,
                    ChunkHierarchyNode.node_id == ev.parent_id,
                    ChunkHierarchyNode.level == "scene",
                )
            )
        ).scalar_one_or_none()
    return {
        "evidence": _node_from_row(ev),
        "scene": _node_from_row(scene) if scene else None,
        "build_id": bid,
        "mode": "scene_expand" if scene else "evidence_only",
    }


async def expand_search_result_with_hierarchy(
    session: AsyncSession,
    *,
    novel_id: int,
    result: dict[str, Any],
    max_scene_chars: int = 2000,
) -> dict[str, Any]:
    """Enrich one hybrid-search hit with scene context when hierarchy is active."""
    out = dict(result)
    node_id = result.get("hierarchy_node_id")
    build_id = result.get("hierarchy_build_id")
    chunk_id = result.get("chunk_id")

    # Resolve from text_chunk row if needed
    if (not node_id or not build_id) and chunk_id:
        row = await session.get(TextChunk, int(chunk_id))
        if row and row.hierarchy_node_id:
            node_id = row.hierarchy_node_id
            build_id = row.hierarchy_build_id
            out["hierarchy_node_id"] = node_id
            out["hierarchy_build_id"] = build_id
            out["hierarchy_level"] = row.hierarchy_level
            out["source_start"] = row.source_start
            out["source_end"] = row.source_end

    if not node_id:
        out["hierarchy_mode"] = "raw_fallback"
        return out

    packed = await get_scene_for_evidence(
        session, novel_id=novel_id, evidence_node_id=node_id, build_id=build_id
    )
    if not packed:
        out["hierarchy_mode"] = "raw_fallback"
        return out

    scene = packed.get("scene")
    evidence = packed["evidence"]
    out["hierarchy_mode"] = packed["mode"]
    out["evidence_node_id"] = evidence.node_id
    out["citation"] = {
        "source_start": evidence.source_start,
        "source_end": evidence.source_end,
    }
    if scene is not None:
        text = scene.content or ""
        if len(text) > max_scene_chars:
            # keep evidence-centered snippet
            text = (evidence.content or "")[:max_scene_chars]
            out["hierarchy_mode"] = "evidence_truncated"
        out["scene_id"] = scene.node_id
        out["scene_content"] = text
        # Prefer scene snippet for display when expanded
        if out["hierarchy_mode"] == "scene_expand":
            out["content_snippet"] = text[:200]
    return out
