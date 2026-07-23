"""Unit tests for database-row narrative-memory manifests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.narrative_memory.manifests import (
    CandidateSnapshot,
    compute_manifest_from_snapshot,
    report_checksum,
    version_lineage_dict,
)


pytestmark = pytest.mark.unit


def _version(**overrides: object) -> SimpleNamespace:
    base = {
        "id": 10,
        "owner_id": 1,
        "novel_id": 2,
        "version_key": "memory-v1",
        "source_snapshot_hash": "a" * 64,
        "hierarchy_build_id": "build-1",
        "hierarchy_checksum": "b" * 64,
        "eligibility_policy_version": "asset-eligibility-policy.v1",
        "eligibility_report_checksum": "c" * 64,
        "prompt_hash": "d" * 64,
        "schema_hash": "e" * 64,
        "model_lineage": {
            "provider": "openai",
            "model": "gpt",
            "deployment": "fixed",
            "revision": "1",
        },
        "decoding_hash": "f" * 64,
        "config_hash": "1" * 64,
        "policy_hash": "2" * 64,
        "optional_source_lineage": [],
        "parent_version_id": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _node(**overrides: object) -> SimpleNamespace:
    base = {
        "id": 1,
        "node_key": "chapter:1",
        "node_kind": "chapter_state",
        "chapter_start": 1,
        "chapter_end": 1,
        "schema_version": "memory-node.v1",
        "content_checksum": "3" * 64,
        "model_lineage_checksum": "4" * 64,
        "display_label": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _claim(**overrides: object) -> SimpleNamespace:
    base = {
        "id": 1,
        "claim_key": "claim:1",
        "claim_kind": "entity_state",
        "node_id": 1,
        "schema_version": "memory-claim.v1",
        "typed_payload": {
            "claim_kind": "entity_state",
            "entity_kind": "character",
            "entity_key": "character:lin",
            "dimension": "location",
            "prior": {"value_kind": "unknown"},
            "current": {"value_kind": "text", "value": "north"},
            "change": "establish",
        },
        "uncertainty": "certain",
        "confidence": 0.9,
        "visible_from_chapter": 1,
        "claim_checksum": "5" * 64,
        "model_lineage_checksum": "4" * 64,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _edge(**overrides: object) -> SimpleNamespace:
    base = {
        "id": 1,
        "edge_type": "contains",
        "source_node_id": 2,
        "target_node_id": 1,
        "edge_checksum": "6" * 64,
        "model_lineage_checksum": "4" * 64,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _link(**overrides: object) -> SimpleNamespace:
    base = {
        "id": 1,
        "claim_id": 1,
        "source_kind": "hierarchy",
        "hierarchy_build_id": "build-1",
        "evidence_node_id": "ev-1",
        "chapter_id": 9,
        "chapter_number": 1,
        "source_start": 0,
        "source_end": 2,
        "content_hash": "7" * 64,
        "source_snapshot_hash": "a" * 64,
        "optional_source_ref": None,
        "link_checksum": "8" * 64,
        "model_lineage_checksum": "4" * 64,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_manifest_is_insertion_order_independent() -> None:
    version = _version()
    nodes_a = (
        _node(id=1, node_key="chapter:1"),
        _node(id=2, node_key="global", node_kind="global_story", chapter_end=1),
    )
    nodes_b = tuple(reversed(nodes_a))
    claims = (_claim(),)
    edges = (_edge(),)
    links = (_link(),)
    snap_a = CandidateSnapshot(version, nodes_a, claims, edges, links)
    snap_b = CandidateSnapshot(version, nodes_b, claims, edges, links)
    assert compute_manifest_from_snapshot(snap_a).manifest_checksum == (
        compute_manifest_from_snapshot(snap_b).manifest_checksum
    )


def test_manifest_changes_when_authoritative_field_changes() -> None:
    base = CandidateSnapshot(
        _version(),
        (_node(),),
        (_claim(),),
        (_edge(),),
        (_link(),),
    )
    original = compute_manifest_from_snapshot(base).manifest_checksum
    changed_version = CandidateSnapshot(
        _version(prompt_hash="9" * 64),
        base.nodes,
        base.claims,
        base.edges,
        base.source_links,
    )
    changed_claim = CandidateSnapshot(
        base.version,
        base.nodes,
        (_claim(claim_checksum="a" * 64),),
        base.edges,
        base.source_links,
    )
    changed_link = CandidateSnapshot(
        base.version,
        base.nodes,
        base.claims,
        base.edges,
        (_link(content_hash="b" * 64),),
    )
    assert compute_manifest_from_snapshot(changed_version).manifest_checksum != original
    assert compute_manifest_from_snapshot(changed_claim).manifest_checksum != original
    assert compute_manifest_from_snapshot(changed_link).manifest_checksum != original


def test_worker_checksum_fields_are_not_manifest_inputs() -> None:
    """Manifest payload is built only from sorted authority row dicts."""

    snapshot = CandidateSnapshot(
        _version(),
        (_node(),),
        (_claim(),),
        (_edge(),),
        (_link(),),
    )
    computation = compute_manifest_from_snapshot(snapshot)
    assert "worker" not in computation.payload
    assert "caller_checksum" not in computation.payload
    assert set(computation.component_hashes) == {
        "version",
        "nodes",
        "claims",
        "edges",
        "source_links",
    }
    lineage = version_lineage_dict(snapshot.version)
    assert lineage["hierarchy_build_id"] == "build-1"


def test_report_checksum_is_stable_for_sorted_reasons() -> None:
    first = report_checksum(
        owner_id=1,
        novel_id=2,
        version_id=3,
        manifest_checksum="a" * 64,
        verdict="blocked",
        reason_codes=("cycle_detected", "missing_claim_source"),
        observed_counts={"nodes": 1},
    )
    second = report_checksum(
        owner_id=1,
        novel_id=2,
        version_id=3,
        manifest_checksum="a" * 64,
        verdict="blocked",
        reason_codes=("cycle_detected", "missing_claim_source"),
        observed_counts={"nodes": 1},
    )
    assert first == second
    assert len(first) == 64
