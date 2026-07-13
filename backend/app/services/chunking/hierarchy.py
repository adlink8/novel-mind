"""Deterministic chapter → scene → evidence assembly (07-04)."""

from __future__ import annotations

from app.services.chunking.manifests import content_hash
from app.services.chunking.schemas import (
    CandidateSegment,
    CandidateSegmentation,
    HierarchyNode,
    HierarchyTree,
)
from app.services.chunking.segmentation import segment_chapter
from app.services.rag_fixture import stable_hash


def _node_id(level: str, chapter_id: int, index: int, c_hash: str) -> str:
    digest = stable_hash(
        {"level": level, "chapter_id": chapter_id, "index": index, "h": c_hash}
    )
    return f"hn_{level[0]}{digest[:22]}"


def assemble_hierarchy(
    *,
    novel_id: int,
    segmentation: CandidateSegmentation,
    scene_target_chars: int = 900,
) -> HierarchyTree:
    """Build chapter/scene/evidence tree from candidate segments (evidence leaves)."""
    chapter_id = segmentation.chapter_id
    chapter_number = segmentation.chapter_number
    segments = segmentation.segments
    if not segments and not segmentation.spans:
        # empty chapter
        ch_hash = content_hash("")
        ch_id = _node_id("chapter", chapter_id, 0, ch_hash)
        tree_checksum = stable_hash({"chapter_id": chapter_id, "nodes": []})
        return HierarchyTree(
            novel_id=novel_id,
            chapter_id=chapter_id,
            chapter_number=chapter_number,
            source_snapshot_hash=segmentation.source_snapshot_hash,
            nodes=[
                HierarchyNode(
                    node_id=ch_id,
                    level="chapter",
                    chapter_id=chapter_id,
                    chapter_number=chapter_number,
                    parent_id=None,
                    child_ids=[],
                    content="",
                    content_hash=ch_hash,
                    source_start=0,
                    source_end=0,
                    order_index=0,
                )
            ],
            chapter_node_id=ch_id,
            tree_checksum=tree_checksum,
        )

    evidence_nodes: list[HierarchyNode] = []
    for i, seg in enumerate(segments):
        eid = _node_id("evidence", chapter_id, i, seg.content_hash)
        evidence_nodes.append(
            HierarchyNode(
                node_id=eid,
                level="evidence",
                chapter_id=chapter_id,
                chapter_number=chapter_number,
                parent_id=None,  # filled when scene assigned
                child_ids=[],
                content=seg.content,
                content_hash=seg.content_hash,
                source_start=seg.source_start,
                source_end=seg.source_end,
                chunk_type="paragraph",
                decision_lineage=list(seg.decision_sources),
                order_index=i,
            )
        )

    # Group contiguous evidence into scenes by size budget
    scenes: list[HierarchyNode] = []
    bucket: list[HierarchyNode] = []
    bucket_chars = 0
    scene_idx = 0

    def flush_scene() -> None:
        nonlocal bucket, bucket_chars, scene_idx
        if not bucket:
            return
        text = "\n".join(n.content for n in bucket)
        c_hash = content_hash(text)
        sid = _node_id("scene", chapter_id, scene_idx, c_hash)
        child_ids = [n.node_id for n in bucket]
        for n in bucket:
            n.parent_id = sid
        scenes.append(
            HierarchyNode(
                node_id=sid,
                level="scene",
                chapter_id=chapter_id,
                chapter_number=chapter_number,
                parent_id=None,  # chapter
                child_ids=child_ids,
                content=text,
                content_hash=c_hash,
                source_start=bucket[0].source_start,
                source_end=bucket[-1].source_end,
                chunk_type="scene",
                order_index=scene_idx,
            )
        )
        scene_idx += 1
        bucket = []
        bucket_chars = 0

    for ev in evidence_nodes:
        if bucket and (bucket_chars + len(ev.content) > scene_target_chars):
            flush_scene()
        bucket.append(ev)
        bucket_chars += len(ev.content)
    flush_scene()

    ch_text = "\n".join(s.content for s in scenes) if scenes else ""
    ch_hash = content_hash(ch_text)
    ch_node_id = _node_id("chapter", chapter_id, 0, ch_hash)
    for sc in scenes:
        sc.parent_id = ch_node_id
    ch_node = HierarchyNode(
        node_id=ch_node_id,
        level="chapter",
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        parent_id=None,
        child_ids=[s.node_id for s in scenes],
        content=ch_text,
        content_hash=ch_hash,
        source_start=scenes[0].source_start if scenes else 0,
        source_end=scenes[-1].source_end if scenes else 0,
        chunk_type="chapter",
        order_index=0,
    )

    nodes = [ch_node, *scenes, *evidence_nodes]
    validate_hierarchy_invariants(nodes, chapter_id=chapter_id)

    tree_checksum = stable_hash(
        {
            "novel_id": novel_id,
            "chapter_id": chapter_id,
            "node_ids": [n.node_id for n in nodes],
            "parents": {n.node_id: n.parent_id for n in nodes},
        }
    )
    return HierarchyTree(
        novel_id=novel_id,
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        source_snapshot_hash=segmentation.source_snapshot_hash,
        nodes=nodes,
        chapter_node_id=ch_node_id,
        tree_checksum=tree_checksum,
    )


def validate_hierarchy_invariants(
    nodes: list[HierarchyNode], *, chapter_id: int
) -> None:
    by_id = {n.node_id: n for n in nodes}
    if len(by_id) != len(nodes):
        raise ValueError("duplicate node ids")

    chapters = [n for n in nodes if n.level == "chapter"]
    scenes = [n for n in nodes if n.level == "scene"]
    evidence = [n for n in nodes if n.level == "evidence"]
    if len(chapters) != 1:
        raise ValueError("exactly one chapter root required")
    root = chapters[0]
    if root.parent_id is not None:
        raise ValueError("chapter must have no parent")

    for sc in scenes:
        if sc.parent_id != root.node_id:
            raise ValueError("scene parent must be chapter")
        if sc.chapter_id != chapter_id:
            raise ValueError("cross-chapter scene")
        # reconstruct from children
        kids = [by_id[cid] for cid in sc.child_ids]
        rebuilt = "\n".join(k.content for k in kids)
        if content_hash(rebuilt) != sc.content_hash:
            raise ValueError("scene content not rebuildable from children")

    for ev in evidence:
        if ev.parent_id is None or ev.parent_id not in by_id:
            raise ValueError("evidence orphan")
        parent = by_id[ev.parent_id]
        if parent.level != "scene":
            raise ValueError("evidence parent must be scene")
        if ev.chapter_id != chapter_id:
            raise ValueError("cross-chapter evidence")

    # no cycles: walk parents
    for n in nodes:
        seen = set()
        cur = n.parent_id
        while cur is not None:
            if cur in seen:
                raise ValueError("cycle detected")
            seen.add(cur)
            cur = by_id[cur].parent_id if cur in by_id else None

    # coverage: evidence ordered non-overlapping by source
    ordered = sorted(evidence, key=lambda e: e.source_start)
    prev_end = -1
    for ev in ordered:
        if ev.source_start < prev_end:
            raise ValueError("evidence overlap")
        prev_end = max(prev_end, ev.source_end)


def build_chapter_hierarchy(
    *,
    novel_id: int,
    chapter_id: int,
    chapter_number: int,
    content: str,
    source_snapshot_hash: str | None = None,
) -> HierarchyTree:
    seg = segment_chapter(
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        content=content,
        source_snapshot_hash=source_snapshot_hash,
    )
    return assemble_hierarchy(novel_id=novel_id, segmentation=seg)


def expand_evidence_to_scene(
    tree: HierarchyTree, evidence_node_id: str, *, max_chars: int = 2000
) -> dict:
    """Evidence hit → limited scene expansion with citations."""
    by_id = {n.node_id: n for n in tree.nodes}
    ev = by_id.get(evidence_node_id)
    if ev is None or ev.level != "evidence":
        return {"mode": "raw_fallback", "reason": "evidence_not_found", "nodes": []}
    scene = by_id.get(ev.parent_id or "")
    if scene is None:
        return {
            "mode": "raw_fallback",
            "reason": "scene_missing",
            "nodes": [ev],
            "citation": {
                "source_start": ev.source_start,
                "source_end": ev.source_end,
            },
        }
    text = scene.content
    if len(text) > max_chars:
        # keep evidence-centered window
        text = ev.content[:max_chars]
        return {
            "mode": "evidence_truncated",
            "scene_id": scene.node_id,
            "text": text,
            "citation": {
                "source_start": ev.source_start,
                "source_end": ev.source_end,
            },
        }
    return {
        "mode": "scene_expand",
        "scene_id": scene.node_id,
        "text": text,
        "evidence_id": ev.node_id,
        "citation": {
            "source_start": ev.source_start,
            "source_end": ev.source_end,
        },
    }
