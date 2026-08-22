"""Unit tests for the frozen source manifest and drift detection.

Phase 28-02 (D-05): the frozen source manifest is DB-recomputable and any
drift fails closed. These pure-function tests cover deterministic checksums,
serialization round-trips, and chapter-level drift classification.
"""

from __future__ import annotations

import pytest

from app.services.narrative_memory.source_manifest import (
    ChapterSourceDigest,
    SourceManifest,
    detect_chapter_drift,
    frozen_manifest_from_progress,
    source_manifest_drift_reasons,
    store_frozen_manifest,
)

pytestmark = pytest.mark.unit


def _hex(char: str) -> str:
    return char * 64


def _manifest(**overrides) -> SourceManifest:
    base = {
        "novel_id": 1,
        "source_snapshot_hash": _hex("a"),
        "hierarchy_build_id": "build-1",
        "hierarchy_checksum": _hex("b"),
        "eligibility_report_checksum": _hex("c"),
        "digests": (
            ChapterSourceDigest(10, 1, _hex("d"), (_hex("e"),)),
            ChapterSourceDigest(20, 2, _hex("f"), ()),
        ),
    }
    base.update(overrides)
    from app.services.narrative_memory.source_manifest import _assemble_manifest

    return _assemble_manifest(**base)


def test_manifest_checksum_is_insertion_order_independent() -> None:
    a = _manifest()
    b = _manifest(
        digests=(
            ChapterSourceDigest(20, 2, _hex("f"), ()),
            ChapterSourceDigest(10, 1, _hex("d"), (_hex("e"),)),
        )
    )
    assert a.manifest_checksum == b.manifest_checksum
    assert len(a.manifest_checksum) == 64


def test_manifest_changes_when_any_digest_changes() -> None:
    original = _manifest().manifest_checksum
    changed_content = _manifest(
        digests=(
            ChapterSourceDigest(10, 1, _hex("9"), (_hex("e"),)),
            ChapterSourceDigest(20, 2, _hex("f"), ()),
        )
    )
    changed_evidence = _manifest(
        digests=(
            ChapterSourceDigest(10, 1, _hex("d"), (_hex("8"),)),
            ChapterSourceDigest(20, 2, _hex("f"), ()),
        )
    )
    changed_version = _manifest(source_snapshot_hash=_hex("9"))
    assert changed_content.manifest_checksum != original
    assert changed_evidence.manifest_checksum != original
    assert changed_version.manifest_checksum != original


def test_progress_round_trip_preserves_manifest() -> None:
    manifest = _manifest()
    progress = store_frozen_manifest({}, manifest)
    assert progress["source_manifest_checksum"] == manifest.manifest_checksum
    restored = frozen_manifest_from_progress(progress)
    assert restored == manifest
    assert restored is not None
    assert restored.chapters == manifest.chapters


def test_missing_progress_returns_none() -> None:
    assert frozen_manifest_from_progress(None) is None
    assert frozen_manifest_from_progress({}) is None


def test_identical_recomputation_has_no_drift() -> None:
    frozen = _manifest()
    recomputed = _manifest()
    assert detect_chapter_drift(frozen, recomputed) == {}
    assert source_manifest_drift_reasons(frozen, recomputed) == []


def test_drift_detection_classifies_chapter_and_reason() -> None:
    frozen = _manifest()
    recomputed = _manifest(
        digests=(
            ChapterSourceDigest(10, 1, _hex("9"), (_hex("e"),)),
            ChapterSourceDigest(20, 2, _hex("f"), ()),
        )
    )
    drift = detect_chapter_drift(frozen, recomputed)
    assert drift == {1: "chapter_content_drift"}
    reasons = source_manifest_drift_reasons(frozen, recomputed)
    assert "source_manifest_drift" in reasons
    assert "chapter:1:chapter_content_drift" in reasons


def test_added_chapter_is_drift() -> None:
    frozen = _manifest()
    recomputed = _manifest(
        digests=(
            ChapterSourceDigest(10, 1, _hex("d"), (_hex("e"),)),
            ChapterSourceDigest(20, 2, _hex("f"), ()),
            ChapterSourceDigest(30, 3, _hex("7"), ()),
        )
    )
    drift = detect_chapter_drift(frozen, recomputed)
    assert drift == {3: "chapter_added_after_freeze"}


def test_evidence_hash_change_is_drift() -> None:
    frozen = _manifest()
    recomputed = _manifest(
        digests=(
            ChapterSourceDigest(10, 1, _hex("d"), (_hex("8"),)),
            ChapterSourceDigest(20, 2, _hex("f"), ()),
        )
    )
    drift = detect_chapter_drift(frozen, recomputed)
    assert drift == {1: "chapter_evidence_drift"}
