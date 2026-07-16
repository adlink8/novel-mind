"""Unit tests for global package construction."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.narrative_memory.builder_packages import (
    PackageBuildError,
    build_global_candidate,
)
from app.services.narrative_memory.contracts import NodeKind


pytestmark = pytest.mark.unit

HEX = "a" * 64


def test_global_package_from_parents() -> None:
    parent = SimpleNamespace(
        id=10,
        node_key="story_arc:1-2",
        node_kind="story_arc",
        chapter_start=1,
        chapter_end=2,
        schema_version="memory-node.v1",
        display_label=None,
    )
    claim = SimpleNamespace(
        id=1,
        claim_key="story_arc:1-2:claim:1",
        typed_payload={
            "claim_kind": "open_loop_delta",
            "loop_key": "loop:heir",
            "prior": "open",
            "current": "open",
            "change": "open",
        },
        uncertainty="uncertain",
        confidence=0.7,
        visible_from_chapter=1,
    )
    link = SimpleNamespace(
        claim_id=1,
        source_kind="hierarchy",
        hierarchy_build_id="build-1",
        evidence_node_id="leaf-1",
        chapter_id=1,
        chapter_number=1,
        source_start=0,
        source_end=2,
        content_hash=HEX,
        source_snapshot_hash=HEX,
        optional_source_ref=None,
    )
    package = build_global_candidate(
        chapter_start=1,
        chapter_end=2,
        parent_nodes=[parent],
        parent_claims=[claim],
        parent_links=[link],
    )
    assert package.nodes[0].node_kind == NodeKind.GLOBAL_STORY
    assert package.nodes[0].node_key == "global_story:book"
    assert package.edges[0].source_node_key == "global_story:book"


def test_global_requires_parents() -> None:
    with pytest.raises(PackageBuildError):
        build_global_candidate(
            chapter_start=1,
            chapter_end=1,
            parent_nodes=[],
            parent_claims=[],
            parent_links=[],
        )
