"""Unit tests for arc/volume package construction."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.narrative_memory.builder_packages import (
    PackageBuildError,
    build_arc_volume_candidate,
)
from app.services.narrative_memory.contracts import NodeKind


pytestmark = pytest.mark.unit

HEX = "a" * 64


def _child(node_key: str, chapter: int, claim_id: int = 1):
    node = SimpleNamespace(
        id=chapter,
        node_key=node_key,
        chapter_start=chapter,
        chapter_end=chapter,
        schema_version="memory-node.v1",
        display_label=None,
        node_kind="chapter_state",
    )
    claim = SimpleNamespace(
        id=claim_id,
        claim_key=f"{node_key}:claim:1",
        typed_payload={
            "claim_kind": "entity_state",
            "entity_kind": "character",
            "entity_key": "character:lin",
            "dimension": "location",
            "prior": {"value_kind": "unknown"},
            "current": {"value_kind": "text", "value": "gate"},
            "change": "establish",
        },
        uncertainty="certain",
        confidence=0.9,
        visible_from_chapter=chapter,
    )
    link = SimpleNamespace(
        claim_id=claim_id,
        source_kind="hierarchy",
        hierarchy_build_id="build-1",
        evidence_node_id=f"leaf-{chapter}",
        chapter_id=100 + chapter,
        chapter_number=chapter,
        source_start=0,
        source_end=3,
        content_hash=HEX,
        source_snapshot_hash=HEX,
        optional_source_ref=None,
    )
    return node, claim, link


def test_arc_package_retains_direct_leaf_links() -> None:
    n1, c1, l1 = _child("chapter_state:1", 1, 1)
    n2, c2, l2 = _child("chapter_state:2", 2, 2)
    package = build_arc_volume_candidate(
        node_kind=NodeKind.STORY_ARC,
        node_key="story_arc:1-2",
        chapter_start=1,
        chapter_end=2,
        child_nodes=[n1, n2],
        child_claims=[c1, c2],
        child_links=[l1, l2],
        model_claims=[],
    )
    assert package.nodes[0].node_kind == NodeKind.STORY_ARC
    assert len(package.edges) == 2
    assert all(link.source_kind.value == "hierarchy" for link in package.source_links)
    assert all(link.evidence_node_id.startswith("leaf-") for link in package.source_links)


def test_arc_package_rejects_empty_children() -> None:
    with pytest.raises(PackageBuildError):
        build_arc_volume_candidate(
            node_kind=NodeKind.STORY_ARC,
            node_key="story_arc:1-1",
            chapter_start=1,
            chapter_end=1,
            child_nodes=[],
            child_claims=[],
            child_links=[],
            model_claims=[],
        )
