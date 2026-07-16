"""Unit tests for Phase 16 change classification and dirty closure."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.narrative_memory.change_oracle import (
    classify_chapter_changes,
    classify_evidence_changes,
    compute_closure,
    expand_dirty_monotonically,
    oracle_has_provider_capability,
)
from app.services.narrative_memory.rebuild_contracts import (
    AssetKind,
    ChangeKind,
    EdgeKind,
    GraphEdge,
    GraphVertex,
    OraclePolicy,
    ReasonCode,
    RebuildDecision,
    stable_checksum,
)

import pytest

pytestmark = pytest.mark.unit


def _ch(cid: int, number: int, content: str = "x"):
    return SimpleNamespace(id=cid, chapter_number=number, content=content)


def test_classify_edit_insert_delete_reorder() -> None:
    parent = {
        1: _ch(1, 1, "old"),
        2: _ch(2, 2, "same"),
        3: _ch(3, 3, "gone"),
    }
    target = [
        _ch(1, 2, "new"),  # reorder + edit
        _ch(2, 1, "same"),  # reorder only (content hash by content string)
        _ch(4, 3, "inserted"),
    ]
    parent_hashes = {
        1: stable_checksum("old"),
        2: stable_checksum("same"),
        3: stable_checksum("gone"),
    }
    target_hashes = {
        1: stable_checksum("new"),
        2: stable_checksum("same"),
        4: stable_checksum("inserted"),
    }
    changes = classify_chapter_changes(
        parent_chapters=parent,
        target_chapters=target,
        parent_content_hashes=parent_hashes,
        target_content_hashes=target_hashes,
    )
    by_key = {c.asset_key: c for c in changes}
    assert by_key["source_chapter:3"].change_kind == ChangeKind.DELETE
    assert by_key["source_chapter:4"].change_kind == ChangeKind.INSERT
    assert ReasonCode.CHAPTER_REORDERED in by_key["source_chapter:1"].reasons
    assert ReasonCode.CHAPTER_EDITED in by_key["source_chapter:1"].reasons
    assert by_key["source_chapter:2"].change_kind == ChangeKind.REORDER


def test_classify_evidence_split_merge_remap() -> None:
    parent = {1: ["fpA", "fpB"]}
    # split: more fingerprints
    split = classify_evidence_changes(
        parent_fps_by_chapter=parent,
        target_fps_by_chapter={1: ["fpA", "fpB", "fpC"]},
    )
    assert split[0].change_kind == ChangeKind.EVIDENCE_SPLIT
    merge = classify_evidence_changes(
        parent_fps_by_chapter=parent,
        target_fps_by_chapter={1: ["fpA"]},
    )
    assert merge[0].change_kind == ChangeKind.EVIDENCE_MERGE
    remap = classify_evidence_changes(
        parent_fps_by_chapter=parent,
        target_fps_by_chapter={1: ["fpX", "fpY"]},
    )
    assert ReasonCode.MAPPING_UNPROVEN in remap[0].reasons


def test_simple_edit_dirties_chapter_parent_global_only() -> None:
    vertices = [
        GraphVertex(
            asset_key="source_chapter:10",
            asset_kind=AssetKind.SOURCE_CHAPTER,
            chapter_start=1,
            chapter_end=1,
        ),
        GraphVertex(
            asset_key="chapter_state:10",
            asset_kind=AssetKind.CHAPTER_STATE,
            chapter_start=1,
            chapter_end=1,
            stage_key="chapter_state:10",
        ),
        GraphVertex(
            asset_key="chapter_state:20",
            asset_kind=AssetKind.CHAPTER_STATE,
            chapter_start=2,
            chapter_end=2,
            stage_key="chapter_state:20",
        ),
        GraphVertex(
            asset_key="story_arc:1-2",
            asset_kind=AssetKind.STORY_ARC,
            chapter_start=1,
            chapter_end=2,
            stage_key="story_arc:1-2",
        ),
        GraphVertex(
            asset_key="global_story:book",
            asset_kind=AssetKind.GLOBAL_STORY,
            chapter_start=1,
            chapter_end=2,
            stage_key="global_story:book",
        ),
    ]
    edges = [
        GraphEdge(
            edge_kind=EdgeKind.SOURCE_TO_CHAPTER_STATE,
            source_key="source_chapter:10",
            target_key="chapter_state:10",
        ),
        GraphEdge(
            edge_kind=EdgeKind.CHAPTER_TO_PARENT,
            source_key="chapter_state:10",
            target_key="story_arc:1-2",
        ),
        GraphEdge(
            edge_kind=EdgeKind.CHAPTER_TO_PARENT,
            source_key="chapter_state:20",
            target_key="story_arc:1-2",
        ),
        GraphEdge(
            edge_kind=EdgeKind.PARENT_TO_GLOBAL,
            source_key="story_arc:1-2",
            target_key="global_story:book",
        ),
    ]
    from app.services.narrative_memory.rebuild_contracts import ChangeRecord

    changes = [
        ChangeRecord(
            asset_key="source_chapter:10",
            asset_kind=AssetKind.SOURCE_CHAPTER,
            change_kind=ChangeKind.EDIT,
            reasons=(ReasonCode.CHAPTER_EDITED,),
            chapter_start=1,
            chapter_end=1,
        )
    ]
    items = compute_closure(
        graph_vertices=vertices,
        graph_edges=edges,
        changes=changes,
        boundary_changed=False,
        policy_incompatible=False,
        optional_uncertain=False,
        cross_chapter_uncertain=False,
        oracle_policy=OraclePolicy(),
        chapter_numbers_sorted=(1, 2),
        earliest_uncertain_chapter=None,
        stable_suffix_stop=None,
    )
    by_key = {i.asset_key: i for i in items}
    assert by_key["chapter_state:10"].decision == RebuildDecision.DIRTY
    assert by_key["story_arc:1-2"].decision == RebuildDecision.DIRTY
    assert by_key["global_story:book"].decision == RebuildDecision.DIRTY
    # Unaffected sibling chapter stays carried when no suffix expansion
    assert by_key["chapter_state:20"].decision == RebuildDecision.CARRIED


def test_insert_expands_suffix_and_global() -> None:
    vertices = [
        GraphVertex(
            asset_key="source_chapter:1",
            asset_kind=AssetKind.SOURCE_CHAPTER,
            chapter_start=1,
            chapter_end=1,
        ),
        GraphVertex(
            asset_key="chapter_state:1",
            asset_kind=AssetKind.CHAPTER_STATE,
            chapter_start=1,
            chapter_end=1,
        ),
        GraphVertex(
            asset_key="chapter_state:2",
            asset_kind=AssetKind.CHAPTER_STATE,
            chapter_start=2,
            chapter_end=2,
        ),
        GraphVertex(
            asset_key="chapter_state:3",
            asset_kind=AssetKind.CHAPTER_STATE,
            chapter_start=3,
            chapter_end=3,
        ),
        GraphVertex(
            asset_key="global_story:book",
            asset_kind=AssetKind.GLOBAL_STORY,
            chapter_start=1,
            chapter_end=3,
        ),
    ]
    edges = [
        GraphEdge(
            edge_kind=EdgeKind.SOURCE_TO_CHAPTER_STATE,
            source_key="source_chapter:1",
            target_key="chapter_state:1",
        ),
        GraphEdge(
            edge_kind=EdgeKind.PARENT_TO_GLOBAL,
            source_key="chapter_state:1",
            target_key="global_story:book",
        ),
        GraphEdge(
            edge_kind=EdgeKind.PARENT_TO_GLOBAL,
            source_key="chapter_state:2",
            target_key="global_story:book",
        ),
        GraphEdge(
            edge_kind=EdgeKind.PARENT_TO_GLOBAL,
            source_key="chapter_state:3",
            target_key="global_story:book",
        ),
    ]
    from app.services.narrative_memory.rebuild_contracts import ChangeRecord

    changes = [
        ChangeRecord(
            asset_key="source_chapter:99",
            asset_kind=AssetKind.SOURCE_CHAPTER,
            change_kind=ChangeKind.INSERT,
            reasons=(ReasonCode.CHAPTER_INSERTED,),
            chapter_start=2,
            chapter_end=2,
        )
    ]
    items = compute_closure(
        graph_vertices=vertices,
        graph_edges=edges,
        changes=changes,
        boundary_changed=False,
        policy_incompatible=False,
        optional_uncertain=False,
        cross_chapter_uncertain=True,
        oracle_policy=OraclePolicy(),
        chapter_numbers_sorted=(1, 2, 3),
        earliest_uncertain_chapter=2,
        stable_suffix_stop=None,
    )
    by_key = {i.asset_key: i for i in items}
    assert by_key["chapter_state:2"].decision == RebuildDecision.DIRTY
    assert by_key["chapter_state:3"].decision == RebuildDecision.DIRTY
    assert by_key["global_story:book"].decision == RebuildDecision.DIRTY
    # Chapter 1 before earliest uncertain may stay carried
    assert by_key["chapter_state:1"].decision == RebuildDecision.CARRIED


def test_monotonic_uncertainty_expansion() -> None:
    base = frozenset({"chapter_state:1", "global_story:book"})
    expanded = expand_dirty_monotonically(base, ["chapter_state:2", "story_arc:1-2"])
    assert base.issubset(expanded)
    assert "chapter_state:2" in expanded


def test_policy_incompatible_dirties_all_semantic() -> None:
    vertices = [
        GraphVertex(
            asset_key="chapter_state:1",
            asset_kind=AssetKind.CHAPTER_STATE,
            chapter_start=1,
            chapter_end=1,
        ),
        GraphVertex(
            asset_key="global_story:book",
            asset_kind=AssetKind.GLOBAL_STORY,
            chapter_start=1,
            chapter_end=1,
        ),
    ]
    items = compute_closure(
        graph_vertices=vertices,
        graph_edges=[],
        changes=[],
        boundary_changed=False,
        policy_incompatible=True,
        optional_uncertain=False,
        cross_chapter_uncertain=False,
        oracle_policy=OraclePolicy(),
        chapter_numbers_sorted=(1,),
        earliest_uncertain_chapter=None,
        stable_suffix_stop=None,
    )
    assert all(i.decision == RebuildDecision.DIRTY for i in items)


def test_oracle_provider_free() -> None:
    assert oracle_has_provider_capability() is False


def test_no_change_carries_global() -> None:
    vertices = [
        GraphVertex(
            asset_key="chapter_state:1",
            asset_kind=AssetKind.CHAPTER_STATE,
            chapter_start=1,
            chapter_end=1,
        ),
        GraphVertex(
            asset_key="global_story:book",
            asset_kind=AssetKind.GLOBAL_STORY,
            chapter_start=1,
            chapter_end=1,
        ),
    ]
    from app.services.narrative_memory.rebuild_contracts import ChangeRecord

    changes = [
        ChangeRecord(
            asset_key="source_chapter:1",
            asset_kind=AssetKind.SOURCE_CHAPTER,
            change_kind=ChangeKind.NO_CHANGE,
            reasons=(ReasonCode.CLEAN_IDENTICAL,),
            chapter_start=1,
            chapter_end=1,
        )
    ]
    items = compute_closure(
        graph_vertices=vertices,
        graph_edges=[],
        changes=changes,
        boundary_changed=False,
        policy_incompatible=False,
        optional_uncertain=False,
        cross_chapter_uncertain=False,
        oracle_policy=OraclePolicy(),
        chapter_numbers_sorted=(1,),
        earliest_uncertain_chapter=None,
        stable_suffix_stop=None,
    )
    assert all(i.decision == RebuildDecision.CARRIED for i in items)
