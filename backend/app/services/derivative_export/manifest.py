"""Frozen derivative export manifest contracts and canonical hashing (Phase 39-01).

D-39-01 / D-39-02 / REQ-FORK-05 / REQ-CRE-07: a derivative export is only ever
materialized from **one immutable export snapshot**; Markdown and EPUB3 consume
the same frozen DTO and the manifest freezes project / revision / version /
snapshot / asset / citation / lineage so the same DB state always exports the
same bytes and the same hash.

This module owns:

- the strict, frozen ``extra="forbid"`` manifest contracts
  (``DerivativeExportChapter`` / ``DerivativeExportRevision`` /
  ``DerivativeExportAsset`` / ``DerivativeExportCitation`` /
  ``MissingDerivativeAssetRecord`` / ``DerivativeExportManifest``);
- ``canonical_export_hash`` — byte-replayable SHA-256 over stable sorted JSON;
- ``derivative_export_manifest_hash`` — replay the manifest hash from a
  manifest contract or its JSON payload (excluding the hash field);
- ``seal_derivative_export_manifest`` — derive the frozen manifest from an
  ``ExportSnapshot`` with the same single canonical hash the snapshot carries.

Everything here is pure and database-free; the snapshot builder and the two
deterministic serializers are the only consumers.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

# Derivative-only export contract versions (no third-party EPUB dependency).
DERIVATIVE_EXPORT_SCHEMA_VERSION = "derivative-export-manifest.v1"
DERIVATIVE_EXPORT_ARTIFACT_KIND = "derivative_export_manifest"
DERIVATIVE_EXPORT_VERSION = "1.0.0"
# D-36-03 / D-39-02: the only space a derivative export may ever represent.
DERIVATIVE_EXPORT_SPACE = "fanfiction_canon"

HEX64_RE_SET = frozenset("0123456789abcdef")


def canonical_export_hash(payload: dict[str, Any]) -> str:
    """SHA-256 over stable, sorted JSON (canonical ordering convention).

    ``default=str`` keeps datetime/enum values deterministic; separators are
    pinned so two equivalent payloads always hash byte-identically.
    """
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _is_hex64(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in HEX64_RE_SET for ch in value)
    )


class _StrictExportModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DerivativeExportChapter(_StrictExportModel):
    """One frozen derivative chapter (content + version token + checksum).

    ``chapter_number`` is the human-facing 1-based position (DB ``position`` is
    0-based); assets are matched by ``chapter_number``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    chapter_id: int = Field(gt=0)
    position: int = Field(ge=0)
    chapter_number: int = Field(ge=1)
    title: str = Field(max_length=200)
    content: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    markdown_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    version_id: int = Field(gt=0)
    revision_id: int | None = Field(default=None, gt=0)


class DerivativeExportRevision(_StrictExportModel):
    """One published derivative revision frozen into the export.

    Mirrors the immutable ``PublishedDerivativeRevision`` consumer contract
    (37-04) field-for-field so a future phase cannot silently widen the
    surface; ``manifest_hash`` here is the project lineage manifest hash.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_id: int = Field(gt=0)
    project_id: int = Field(gt=0)
    fork_id: int = Field(gt=0)
    revision_id: int = Field(gt=0)
    version_id: int = Field(gt=0)
    chapter_id: int = Field(gt=0)
    chapter_number: int = Field(ge=1)
    status: str = Field(min_length=1, max_length=64)
    source_snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    citation_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    asset_hashes: tuple[str, ...] = Field(default_factory=tuple)
    approval: dict[str, Any] = Field(default_factory=dict)
    review: dict[str, Any] = Field(default_factory=dict)


class DerivativeVisualVersionExport(_StrictExportModel):
    """Frozen visual-version lineage of a published derivative asset."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version_id: int = Field(gt=0)
    version_key: str = Field(min_length=1, max_length=160)
    version_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DerivativeSourceSnapshotExport(_StrictExportModel):
    """Frozen source-snapshot lineage (no Original future content may leak)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_snapshot_id: str = Field(min_length=1, max_length=160)
    source_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cutoff_chapter: int = Field(ge=1)


class DerivativeExportAsset(_StrictExportModel):
    """One published derivative asset frozen into the export.

    The full 38-03/04 provenance envelope: generated ``asset_id``, replayed
    content hash, visual-version / source-snapshot lineage, approval/review
    chain, identity/source/generator lineage and the divergence manifest hash.
    No raw storage path and no Original row is ever present.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str = Field(min_length=1, max_length=200)
    asset_key: str = Field(min_length=1, max_length=180)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: str = Field(min_length=1, max_length=100)
    size_bytes: int = Field(gt=0)
    namespace: str = Field(min_length=1, max_length=64)
    scene_spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    chapter_number: int = Field(ge=1)
    visual_version: DerivativeVisualVersionExport
    source_snapshot: DerivativeSourceSnapshotExport
    approval: str = Field(min_length=1, max_length=32)
    review_state: str = Field(min_length=1, max_length=32)
    review: dict[str, Any] = Field(default_factory=dict)
    source_refs: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    identity_lineage: tuple[dict[str, Any], ...] = Field(default_factory=tuple)
    generator_lineage: dict[str, Any] = Field(default_factory=dict)
    divergence_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DerivativeExportCitation(_StrictExportModel):
    """One citation leaf of the derivative export citation package.

    ``citation_hash`` is the leaf replay (``canonical_citation_hash([key])``);
    the revision record carries the aggregate citation hash for the same
    evidence set so both are auditable in the package.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_key: str = Field(min_length=1, max_length=4000)
    citation_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")
    revision_id: int = Field(gt=0)
    chapter_number: int = Field(ge=1)


class MissingDerivativeAssetRecord(_StrictExportModel):
    """Explicit missing-asset record; a missing binary is never a silent drop."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    asset_id: str = Field(min_length=1, max_length=200)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: str = Field(min_length=1, max_length=100)
    chapter_number: int = Field(ge=1)
    reason_code: str
    detail: str


class DerivativeExportManifest(_StrictExportModel):
    """Frozen derivative export manifest (single source for Markdown/EPUB3).

    Ordering is deterministic (chapters by position, revisions by chapter,
    assets by chapter/asset id, citations by revision/chapter) so the same DB
    state always freezes to the same manifest hash.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = DERIVATIVE_EXPORT_SCHEMA_VERSION
    artifact_kind: str = DERIVATIVE_EXPORT_ARTIFACT_KIND
    export_version: str = DERIVATIVE_EXPORT_VERSION
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    project_id: int = Field(gt=0)
    project_key: str = Field(min_length=1, max_length=128)
    project_name: str = Field(min_length=1, max_length=120)
    fork_id: int = Field(gt=0)
    fork_key: str = Field(min_length=1, max_length=128)
    space: str = DERIVATIVE_EXPORT_SPACE
    # Project fork lineage (D-39-01: revision/version/snapshot alignment).
    source_snapshot: str = Field(pattern=r"^[0-9a-f]{64}$")
    project_manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    cutoff_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scope_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    text_version_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    chapters: tuple[DerivativeExportChapter, ...]
    revisions: tuple[DerivativeExportRevision, ...] = Field(default_factory=tuple)
    assets: tuple[DerivativeExportAsset, ...] = Field(default_factory=tuple)
    citations: tuple[DerivativeExportCitation, ...] = Field(default_factory=tuple)
    missing_assets: tuple[MissingDerivativeAssetRecord, ...] = Field(
        default_factory=tuple
    )
    # The byte-replayable export hash (identical to the snapshot hash).
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def derivative_export_manifest_hash(
    manifest: DerivativeExportManifest | Mapping[str, Any],
) -> str:
    """Replay the frozen manifest hash from a manifest contract or its JSON."""
    if isinstance(manifest, DerivativeExportManifest):
        payload = manifest.model_dump(mode="json", exclude={"manifest_hash"})
    else:
        payload = dict(manifest)
        payload.pop("manifest_hash", None)
    return canonical_export_hash(payload)


def seal_derivative_export_manifest(snapshot: Any) -> DerivativeExportManifest:
    """Derive the frozen manifest from an ``ExportSnapshot``.

    The manifest shares the snapshot's single canonical hash: the export
    ``manifest_hash`` equals ``snapshot.snapshot_hash`` so prepare/download and
    every serializer agree on one immutable version (D-39-01).
    """
    data = snapshot.model_dump(mode="json", exclude={"snapshot_hash"})
    payload = {**data, "manifest_hash": snapshot.snapshot_hash}
    manifest = DerivativeExportManifest(**payload)
    if derivative_export_manifest_hash(manifest) != snapshot.snapshot_hash:
        raise ValueError(
            "derivative export manifest hash does not replay the snapshot hash"
        )
    return manifest


__all__ = [
    "DERIVATIVE_EXPORT_ARTIFACT_KIND",
    "DERIVATIVE_EXPORT_SCHEMA_VERSION",
    "DERIVATIVE_EXPORT_SPACE",
    "DERIVATIVE_EXPORT_VERSION",
    "DerivativeExportAsset",
    "DerivativeExportChapter",
    "DerivativeExportCitation",
    "DerivativeExportManifest",
    "DerivativeExportRevision",
    "DerivativeSourceSnapshotExport",
    "DerivativeVisualVersionExport",
    "MissingDerivativeAssetRecord",
    "canonical_export_hash",
    "derivative_export_manifest_hash",
    "seal_derivative_export_manifest",
]
