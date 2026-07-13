"""Immutable full/incremental candidate builds (07-05). Never moves active pointer."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.services.chunking.hierarchy import build_chapter_hierarchy
from app.services.chunking.incremental import plan_incremental_delta
from app.services.chunking.schemas import ChunkBuildRecord, HierarchyTree
from app.services.rag_fixture import stable_hash


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class InMemoryBuildStore:
    """Test/dev store for builds and active pointer (PostgreSQL-backed later)."""

    builds: dict[str, ChunkBuildRecord] = field(default_factory=dict)
    # novel_id -> active build_id
    active: dict[int, str] = field(default_factory=dict)
    # build_id -> hierarchy trees by chapter
    hierarchies: dict[str, list[HierarchyTree]] = field(default_factory=dict)
    # build_id -> evidence vector ids
    vector_ids: dict[str, set[str]] = field(default_factory=dict)
    index_writes: int = 0
    llm_calls: int = 0

    def get_active(self, novel_id: int) -> str | None:
        return self.active.get(novel_id)


def create_candidate_build(
    store: InMemoryBuildStore,
    *,
    novel_id: int,
    chapters: list[dict[str, Any]],
    source_snapshot_hash: str,
    chunker_name: str = "hierarchical-v1",
    chunker_version: str = "1.0.0",
    chunker_config: dict[str, Any] | None = None,
    parent_build_id: str | None = None,
    force_full: bool = False,
) -> ChunkBuildRecord:
    """Create a new immutable candidate build. Does NOT change active pointer."""
    cfg = chunker_config or {"min": 300, "max": 500}
    cfg_hash = stable_hash(cfg)
    parent = store.builds.get(parent_build_id) if parent_build_id else None
    if parent is None and store.get_active(novel_id):
        parent = store.builds.get(store.active[novel_id])

    # Previous chapter hashes for delta
    prev_chapter_hashes: dict[int, str] = {}
    prev_chunker = None
    if parent:
        prev_chunker = {
            "chunker_name": parent.chunker_name,
            "chunker_version": parent.chunker_version,
            "chunker_config_hash": parent.chunker_config_hash,
        }
        # recover from journal if present
        for entry in parent.journal:
            if entry.get("event") == "chapter_hash":
                prev_chapter_hashes[int(entry["chapter_id"])] = entry["content_hash"]

    current_hashes = {
        int(ch["chapter_id"]): stable_hash({"c": ch.get("content") or ""})
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
        force_full=force_full or parent is None,
    )

    build_id = f"cb_{uuid.uuid4().hex[:16]}"
    collection = f"candidate_{novel_id}_{build_id}"

    trees: list[HierarchyTree] = []
    journal: list[dict[str, Any]] = [
        {"event": "created", "at": _utcnow(), "delta": delta.__dict__},
    ]

    if delta.no_op:
        # carry-forward entire hierarchy from parent
        if parent and parent.build_id in store.hierarchies:
            trees = list(store.hierarchies[parent.build_id])
            store.vector_ids[build_id] = set(store.vector_ids.get(parent.build_id, set()))
        journal.append({"event": "noop_carry_forward", "at": _utcnow()})
    else:
        # rebuild changed chapters; carry-forward unchanged from parent
        parent_trees: dict[int, HierarchyTree] = {}
        if parent is not None:
            parent_trees = {
                t.chapter_id: t for t in store.hierarchies.get(parent.build_id, [])
            }
        changed = set(delta.changed_chapter_ids)
        for ch in chapters:
            cid = int(ch["chapter_id"])
            if cid in changed or delta.full:
                tree = build_chapter_hierarchy(
                    novel_id=novel_id,
                    chapter_id=cid,
                    chapter_number=int(ch.get("chapter_number") or 1),
                    content=ch.get("content") or "",
                    source_snapshot_hash=source_snapshot_hash,
                )
                trees.append(tree)
                store.index_writes += 1
            elif cid in parent_trees:
                trees.append(parent_trees[cid])
        # evidence projection ids
        vids: set[str] = set()
        for t in trees:
            for n in t.nodes:
                if n.level == "evidence":
                    vids.add(n.node_id)
        store.vector_ids[build_id] = vids

    for ch in chapters:
        journal.append(
            {
                "event": "chapter_hash",
                "chapter_id": int(ch["chapter_id"]),
                "content_hash": current_hashes[int(ch["chapter_id"])],
            }
        )

    manifest_checksum = stable_hash(
        {
            "build_id": build_id,
            "trees": [t.tree_checksum for t in trees],
            "snapshot": source_snapshot_hash,
            "cfg": cfg_hash,
        }
    )
    rec = ChunkBuildRecord(
        build_id=build_id,
        novel_id=novel_id,
        status="built",
        parent_build_id=parent.build_id if parent else None,
        source_snapshot_hash=source_snapshot_hash,
        manifest_checksum=manifest_checksum,
        chunker_name=chunker_name,
        chunker_version=chunker_version,
        chunker_config_hash=cfg_hash,
        collection_name=collection,
        is_candidate=True,
        immutable=True,
        changed_chapter_ids=list(delta.changed_chapter_ids),
        journal=journal,
    )
    store.builds[build_id] = rec
    store.hierarchies[build_id] = trees
    # CRITICAL: do not touch store.active
    return rec
