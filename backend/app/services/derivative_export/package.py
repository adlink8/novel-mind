"""Derivative export provenance package (Phase 39-02, D-39-03/T-39-02).

D-39-03: the deliverable is not just Markdown/EPUB bytes — it is a **bounded
archive package** that carries asset provenance, the citation package (leaf
hashes), owner isolation evidence, the frozen export manifest and a package
manifest whose hash covers every content entry. The same DB state always
produces the same package bytes and the same package hash.

Design (T-39-02-01 / T-39-02-02 / T-39-02-SC):

- artifact IDs are **generated, never guessed**: ``package_id`` and every zip
  entry name derive only from the frozen snapshot hash, content hashes and
  fixed index values — a client-supplied path/id never reaches the archive.
- the builder **re-validates owner and derivative namespace** before sealing:
  a cross-owner revision, an Original/future space, a stale/mutated citation, a
  rejected or missing asset or an unsafe asset id can never produce a package
  (fail closed with an explicit ``ExportSnapshotError`` code).
- entry names are allowlisted (``manifest.json`` / ``provenance.json`` /
  ``assets/{content_hash}{ext}`` / ``package-manifest.json``) and every name is
  re-checked against ``/`` ``\\`` ``..`` ``\\x00`` before it is written
  (zip-slip/path traversal fail closed).
- entry count and total/entry bytes are bounded (T-39-02-02) and the archive is
  byte-deterministic (fixed zip timestamps) so the same snapshot always yields
  the same package and the same package hash.
- ``package-manifest.json`` carries the entry list (name + kind + per-entry
  content hash + size) and a ``package_hash`` that covers **every content
  entry plus the metadata canonical hash** (replayable; the hash field itself
  is excluded from the input). The package-manifest bytes are bound by the
  ``X-Package-Manifest-Hash`` response header, which is the server-side root of
  trust that detects tampering of the index itself.
- standard library only (``zipfile`` + ``hashlib``) — T-39-02-SC.

Nothing here writes to the database, to the filesystem or to any active pointer;
the package is produced purely in memory from the frozen snapshot.
"""

from __future__ import annotations

import hashlib
import json
from io import BytesIO
from typing import Any, Callable, Mapping
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.derivative_visual_asset import DERIVATIVE_ASSET_NAMESPACE
from app.services.derivative_export.manifest import (
    DERIVATIVE_EXPORT_ARTIFACT_KIND,
    DERIVATIVE_EXPORT_SCHEMA_VERSION,
    DERIVATIVE_EXPORT_SPACE,
    DERIVATIVE_EXPORT_VERSION,
    DerivativeExportAsset,
    DerivativeExportManifest,
    canonical_export_hash,
    seal_derivative_export_manifest,
)
from app.services.derivative_export.markdown import asset_filename
from app.services.derivative_export.snapshot import (
    ExportSnapshot,
    ExportSnapshotError,
)
from app.services.derivative_generation.published_revision import (
    canonical_citation_hash,
)

# T-39-02-02 bounded sizes (fail closed on a degenerate/oversized package).
MAX_PACKAGE_TOTAL_BYTES = 100 * 1024 * 1024  # 100 MiB total archive
MAX_PACKAGE_ENTRY_BYTES = 50 * 1024 * 1024  # 50 MiB per entry
MAX_PACKAGE_ENTRIES = 10_000  # bounded entry count (assets + fixed records)

DERIVATIVE_EXPORT_PACKAGE_SCHEMA_VERSION = "derivative-export-package.v1"
DERIVATIVE_EXPORT_PACKAGE_KIND = "derivative_export_package"
DERIVATIVE_EXPORT_PROVENANCE_SCHEMA_VERSION = "derivative-export-provenance.v1"

# Allowlisted top-level entry names (the only names that may exist in the zip).
_PACKAGE_MANIFEST_ENTRY = "package-manifest.json"
_MANIFEST_ENTRY = "manifest.json"
_PROVENANCE_ENTRY = "provenance.json"
_ASSET_PREFIX = "assets/"
_ALLOWED_ENTRY_PREFIXES = ("assets/",)

_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

# Path tokens that must never appear in any entry name (defense in depth).
_PATH_TOKENS = ("/", "\\", "..", "\x00")


class _StrictPackageModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DerivativeExportPackageEntry(_StrictPackageModel):
    """One zip entry described by the package manifest (hash-addressed)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=32)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)


class DerivativeExportPackageManifest(_StrictPackageModel):
    """Frozen package index: entries + metadata, sealed with ``package_hash``.

    ``package_hash`` replays the canonical hash over the **metadata canonical
    hash plus every content entry** (name/kind/content_hash/size_bytes); the
    ``package_hash`` field itself is excluded from the input. The index bytes
    are bound by the ``X-Package-Manifest-Hash`` response header.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = DERIVATIVE_EXPORT_PACKAGE_SCHEMA_VERSION
    artifact_kind: str = DERIVATIVE_EXPORT_PACKAGE_KIND
    package_id: str = Field(min_length=1, max_length=200)
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    project_id: int = Field(gt=0)
    fork_id: int = Field(gt=0)
    space: str = DERIVATIVE_EXPORT_SPACE
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    exporter_version: str = DERIVATIVE_EXPORT_VERSION
    manifest_schema_version: str = DERIVATIVE_EXPORT_SCHEMA_VERSION
    metadata_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    entries: tuple[DerivativeExportPackageEntry, ...]
    package_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def derivative_export_package_hash(
    package_manifest: DerivativeExportPackageManifest | Mapping[str, Any],
) -> str:
    """Replay the package hash from a package manifest contract or its JSON.

    The hash covers the metadata canonical hash plus every content entry
    (entry list + per-entry content hash); ``package_hash`` is excluded.
    """
    if isinstance(package_manifest, DerivativeExportPackageManifest):
        payload = package_manifest.model_dump(mode="json", exclude={"package_hash"})
    else:
        payload = dict(package_manifest)
        payload.pop("package_hash", None)
    return canonical_export_hash(payload)


def _package_metadata(snapshot: ExportSnapshot) -> dict[str, Any]:
    """Deterministic package metadata (project/fork/revision/exporter/schema)."""
    return {
        "owner_id": snapshot.owner_id,
        "novel_id": snapshot.novel_id,
        "project_id": snapshot.project_id,
        "project_key": snapshot.project_key,
        "project_name": snapshot.project_name,
        "fork_id": snapshot.fork_id,
        "fork_key": snapshot.fork_key,
        "space": snapshot.space,
        "source_snapshot": snapshot.source_snapshot,
        "project_manifest_hash": snapshot.project_manifest_hash,
        "cutoff_snapshot_hash": snapshot.cutoff_snapshot_hash,
        "scope_hash": snapshot.scope_hash,
        "text_version_hash": snapshot.text_version_hash,
        "exporter_version": DERIVATIVE_EXPORT_VERSION,
        "schema_versions": {
            "manifest": DERIVATIVE_EXPORT_SCHEMA_VERSION,
            "package": DERIVATIVE_EXPORT_PACKAGE_SCHEMA_VERSION,
            "provenance": DERIVATIVE_EXPORT_PROVENANCE_SCHEMA_VERSION,
        },
        "counts": {
            "chapters": len(snapshot.chapters),
            "revisions": len(snapshot.revisions),
            "assets": len(snapshot.assets),
            "citations": len(snapshot.citations),
            "missing_assets": len(snapshot.missing_assets),
        },
    }


def _package_metadata_hash(snapshot: ExportSnapshot) -> str:
    return canonical_export_hash(_package_metadata(snapshot))


def _package_id(snapshot: ExportSnapshot) -> str:
    """Generated, non-guessable artifact id (snapshot-hash bound)."""
    return f"derivative-export:{snapshot.project_id}:{snapshot.snapshot_hash}"


# ---------------------------------------------------------------------------
# Pure package-input validators (fail-closed, DB-free, testable)
# ---------------------------------------------------------------------------


def validate_package_inputs(
    snapshot: ExportSnapshot, manifest: DerivativeExportManifest
) -> list[str]:
    """Re-validate owner/namespace/citation/asset/manifest parity for a package.

    Any returned code means the package must **not** be generated. Codes:

    - ``namespace_denied`` / ``asset_namespace_denied`` — Original/future space
      or a non-derivative asset namespace reached the package;
    - ``revision_{owner,project,fork}_mismatch`` / ``manifest_*_mismatch`` —
      owner isolation evidence failed (IDOR-style cross-scope row);
    - ``citation_{hash,source_snapshot,revision,chapter}*`` — stale/future
      citation leaf;
    - ``asset_{path_denied,not_approved,hash_not_member}`` — zip-slip /
      rejected asset / unbound asset hash;
    - ``missing_asset_blocks_package`` — an explicit missing binary prevents a
      complete provenance package.
    """
    errors: list[str] = []
    if snapshot.space != DERIVATIVE_EXPORT_SPACE:
        errors.append("namespace_denied")
    if type(snapshot.owner_id) is not int or snapshot.owner_id <= 0:
        errors.append("invalid_scope")

    # D-39-03 manifest parity: the frozen manifest must be the snapshot's.
    if manifest.manifest_hash != snapshot.snapshot_hash:
        errors.append("manifest_hash_mismatch")
    if manifest.owner_id != snapshot.owner_id:
        errors.append("manifest_owner_mismatch")
    if manifest.novel_id != snapshot.novel_id:
        errors.append("manifest_novel_mismatch")
    if manifest.project_id != snapshot.project_id:
        errors.append("manifest_project_mismatch")
    if manifest.fork_id != snapshot.fork_id:
        errors.append("manifest_fork_mismatch")

    # Owner isolation: every revision is scoped to the same owner/project/fork.
    for revision in snapshot.revisions:
        if revision.owner_id != snapshot.owner_id:
            errors.append("revision_owner_mismatch")
        if revision.project_id != snapshot.project_id:
            errors.append("revision_project_mismatch")
        if revision.fork_id != snapshot.fork_id:
            errors.append("revision_fork_mismatch")

    # Asset provenance: derivative namespace only + zip-safe asset ids.
    for asset in snapshot.assets:
        if asset.namespace != DERIVATIVE_ASSET_NAMESPACE:
            errors.append("asset_namespace_denied")
        if asset.approval != "approved" or asset.review_state != "approved":
            errors.append("asset_not_approved")
        if not isinstance(asset.asset_id, str) or not asset.asset_id:
            errors.append("asset_id_missing")
        elif any(token in asset.asset_id for token in _PATH_TOKENS):
            errors.append("asset_path_denied")

    # Citation package: every leaf hash replays and binds to this snapshot.
    revision_ids = {r.revision_id for r in snapshot.revisions}
    chapter_numbers = {c.chapter_number for c in snapshot.chapters}
    for citation in snapshot.citations:
        if canonical_citation_hash([citation.citation_key]) != citation.citation_hash:
            errors.append("citation_hash_mismatch")
        if citation.source_snapshot != snapshot.source_snapshot:
            errors.append("citation_source_snapshot_mismatch")
        if citation.revision_id not in revision_ids:
            errors.append("citation_revision_unknown")
        if citation.chapter_number not in chapter_numbers:
            errors.append("citation_chapter_unknown")

    # Revision asset hashes must be members of the published package asset set.
    available_hashes = {asset.content_hash for asset in snapshot.assets}
    for revision in snapshot.revisions:
        unbound = sorted(
            hash_ for hash_ in revision.asset_hashes if hash_ not in available_hashes
        )
        if unbound:
            errors.append("asset_hash_not_member")

    # D-39-03: a complete provenance package cannot contain a missing binary.
    if snapshot.missing_assets:
        errors.append("missing_asset_blocks_package")
    return errors


# ---------------------------------------------------------------------------
# Deterministic payloads
# ---------------------------------------------------------------------------


def _revision_provenance(snapshot: ExportSnapshot) -> list[dict[str, Any]]:
    return [
        {
            "revision_id": r.revision_id,
            "version_id": r.version_id,
            "chapter_id": r.chapter_id,
            "chapter_number": r.chapter_number,
            "status": r.status,
            "source_snapshot": r.source_snapshot,
            "manifest_hash": r.manifest_hash,
            "citation_hash": r.citation_hash,
            "asset_hashes": list(r.asset_hashes),
        }
        for r in snapshot.revisions
    ]


def _asset_provenance(snapshot: ExportSnapshot) -> list[dict[str, Any]]:
    return [
        {
            "asset_id": asset.asset_id,
            "asset_key": asset.asset_key,
            "content_hash": asset.content_hash,
            "mime_type": asset.mime_type,
            "size_bytes": asset.size_bytes,
            "namespace": asset.namespace,
            "scene_spec_hash": asset.scene_spec_hash,
            "chapter_number": asset.chapter_number,
            "visual_version": asset.visual_version.model_dump(mode="json"),
            "source_snapshot": asset.source_snapshot.model_dump(mode="json"),
            "approval": asset.approval,
            "review_state": asset.review_state,
            "source_refs": [ref for ref in asset.source_refs],
            "identity_lineage": [row for row in asset.identity_lineage],
            "generator_lineage": dict(asset.generator_lineage),
            "divergence_manifest_hash": asset.divergence_manifest_hash,
        }
        for asset in snapshot.assets
    ]


def _citation_provenance(snapshot: ExportSnapshot) -> list[dict[str, Any]]:
    return [
        {
            "citation_key": c.citation_key,
            "citation_hash": c.citation_hash,
            "source_snapshot": c.source_snapshot,
            "revision_id": c.revision_id,
            "chapter_number": c.chapter_number,
        }
        for c in snapshot.citations
    ]


def provenance_payload(
    snapshot: ExportSnapshot, manifest: DerivativeExportManifest
) -> dict[str, Any]:
    """Owner isolation evidence + asset/citation provenance envelope (D-39-03)."""
    return {
        "schema_version": DERIVATIVE_EXPORT_PROVENANCE_SCHEMA_VERSION,
        "artifact_kind": "derivative_export_provenance",
        "exporter_version": DERIVATIVE_EXPORT_VERSION,
        "manifest_schema_version": DERIVATIVE_EXPORT_SCHEMA_VERSION,
        "space": DERIVATIVE_EXPORT_SPACE,
        "snapshot_hash": snapshot.snapshot_hash,
        "manifest_hash": manifest.manifest_hash,
        "project": {
            "owner_id": snapshot.owner_id,
            "novel_id": snapshot.novel_id,
            "project_id": snapshot.project_id,
            "project_key": snapshot.project_key,
            "project_name": snapshot.project_name,
        },
        "fork": {"fork_id": snapshot.fork_id, "fork_key": snapshot.fork_key},
        "revision_lineage": {
            "source_snapshot": snapshot.source_snapshot,
            "project_manifest_hash": snapshot.project_manifest_hash,
            "cutoff_snapshot_hash": snapshot.cutoff_snapshot_hash,
            "scope_hash": snapshot.scope_hash,
            "text_version_hash": snapshot.text_version_hash,
        },
        "revisions": _revision_provenance(snapshot),
        "assets": _asset_provenance(snapshot),
        "citations": _citation_provenance(snapshot),
        "missing_assets": [
            {
                "asset_id": m.asset_id,
                "content_hash": m.content_hash,
                "mime_type": m.mime_type,
                "chapter_number": m.chapter_number,
                "reason_code": m.reason_code,
            }
            for m in snapshot.missing_assets
        ],
        "owner_isolation": {
            "space_allowed": snapshot.space == DERIVATIVE_EXPORT_SPACE,
            "revisions_owner_scoped": all(
                r.owner_id == snapshot.owner_id for r in snapshot.revisions
            ),
            "assets_derivative_namespace": all(
                a.namespace == DERIVATIVE_ASSET_NAMESPACE for a in snapshot.assets
            ),
            "citations_source_snapshot_bound": all(
                c.source_snapshot == snapshot.source_snapshot
                for c in snapshot.citations
            ),
        },
    }


# ---------------------------------------------------------------------------
# Archive writer
# ---------------------------------------------------------------------------


def _zip_entry(name: str, content: bytes, *, stored: bool = False) -> tuple[ZipInfo, bytes]:
    info = ZipInfo(name, date_time=_FIXED_ZIP_TIME)
    info.compress_type = ZIP_STORED if stored else ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info, content


def _assert_safe_entry_name(name: str) -> None:
    """T-39-02-02: entry names are generated, but re-check every token anyway."""
    if not isinstance(name, str) or not name:
        raise ExportSnapshotError("entry_name_invalid", "empty archive entry name")
    if len(name) > 200:
        raise ExportSnapshotError("entry_name_invalid", "archive entry name too long")
    if any(token in name for token in _PATH_TOKENS) and not name.startswith(
        _ASSET_PREFIX
    ):
        raise ExportSnapshotError(
            "entry_name_denied",
            f"archive entry name carries a forbidden path token: {name!r}",
        )
    if name.startswith(_ASSET_PREFIX):
        stem = name[len(_ASSET_PREFIX) :]
        if not stem or any(token in stem for token in ("\\", "..", "\x00")):
            raise ExportSnapshotError(
                "entry_name_denied",
                f"asset archive entry name is unsafe: {name!r}",
            )


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _manifest_json_bytes(manifest: DerivativeExportManifest) -> bytes:
    return _json_bytes(manifest.model_dump(mode="json"))


def _package_manifest_json_bytes(pkg: DerivativeExportPackageManifest) -> bytes:
    return _json_bytes(pkg.model_dump(mode="json"))


def _seal_package_manifest(
    snapshot: ExportSnapshot,
    entry_records: tuple[DerivativeExportPackageEntry, ...],
) -> DerivativeExportPackageManifest:
    pkg = DerivativeExportPackageManifest(
        package_id=_package_id(snapshot),
        owner_id=snapshot.owner_id,
        novel_id=snapshot.novel_id,
        project_id=snapshot.project_id,
        fork_id=snapshot.fork_id,
        space=snapshot.space,
        snapshot_hash=snapshot.snapshot_hash,
        metadata_hash=_package_metadata_hash(snapshot),
        entries=entry_records,
        package_hash="0" * 64,
    )
    return pkg.model_copy(update={"package_hash": derivative_export_package_hash(pkg)})


# ---------------------------------------------------------------------------
# Public builder
# ---------------------------------------------------------------------------


def build_derivative_export_package(
    snapshot: ExportSnapshot,
    asset_reader: Callable[[DerivativeExportAsset], bytes | None],
) -> tuple[bytes, DerivativeExportPackageManifest]:
    """Seal a deterministic, bounded provenance package from the frozen snapshot.

    Raises ``ExportSnapshotError`` fail-closed for any cross-owner /
    Original-space / stale-citation / rejected or missing asset / unsafe path
    input; on success returns ``(archive_bytes, package_manifest)`` where the
    package manifest hash covers every content entry plus the metadata hash.
    """
    manifest = seal_derivative_export_manifest(snapshot)
    errors = validate_package_inputs(snapshot, manifest)
    if errors:
        raise ExportSnapshotError(
            errors[0],
            "derivative export package blocked: " + "; ".join(sorted(set(errors))),
        )

    # Read approved bytes once; a missing/hash-drifted binary fails closed here
    # (a complete provenance package never contains a silent placeholder).
    asset_payloads: dict[str, bytes] = {}
    for asset in snapshot.assets:
        payload = asset_reader(asset)
        if payload is None or hashlib.sha256(payload).hexdigest() != asset.content_hash:
            raise ExportSnapshotError(
                "asset_bytes_missing",
                f"asset {asset.asset_id} bytes do not replay its content hash; "
                "a provenance package cannot be produced",
            )
        if len(payload) > MAX_PACKAGE_ENTRY_BYTES:
            raise ExportSnapshotError(
                "entry_too_large",
                f"asset {asset.asset_id} exceeds the {MAX_PACKAGE_ENTRY_BYTES} "
                "byte package entry limit",
            )
        asset_payloads[asset.asset_id] = payload

    # Content entries (deterministic order). Names are generated, never guessed.
    content_entries: list[tuple[str, str, bytes]] = [
        (_MANIFEST_ENTRY, "manifest", _manifest_json_bytes(manifest)),
        (_PROVENANCE_ENTRY, "provenance", _json_bytes(provenance_payload(snapshot, manifest))),
    ]
    for asset in snapshot.assets:
        content_entries.append(
            (f"{_ASSET_PREFIX}{asset_filename(asset)}", "asset", asset_payloads[asset.asset_id])
        )

    if len(content_entries) + 1 > MAX_PACKAGE_ENTRIES:
        raise ExportSnapshotError(
            "package_too_many_entries",
            f"package exceeds the {MAX_PACKAGE_ENTRIES} entry limit",
        )

    entry_records = tuple(
        DerivativeExportPackageEntry(
            name=name,
            kind=kind,
            content_hash=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )
        for name, kind, content in content_entries
    )
    pkg = _seal_package_manifest(snapshot, entry_records)
    pkg_bytes = _package_manifest_json_bytes(pkg)
    if len(pkg_bytes) > MAX_PACKAGE_ENTRY_BYTES:
        raise ExportSnapshotError(
            "entry_too_large",
            "package-manifest exceeds the package entry byte limit",
        )

    # Byte-deterministic archive (fixed timestamps, allowlisted names).
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        for name, _kind, content in content_entries:
            _assert_safe_entry_name(name)
            archive.writestr(*_zip_entry(name, content))
        _assert_safe_entry_name(_PACKAGE_MANIFEST_ENTRY)
        archive.writestr(*_zip_entry(_PACKAGE_MANIFEST_ENTRY, pkg_bytes))

    payload = output.getvalue()
    if len(payload) > MAX_PACKAGE_TOTAL_BYTES:
        raise ExportSnapshotError(
            "package_too_large",
            f"package archive exceeds the {MAX_PACKAGE_TOTAL_BYTES} byte limit",
        )
    return payload, pkg


__all__ = [
    "DERIVATIVE_EXPORT_PACKAGE_KIND",
    "DERIVATIVE_EXPORT_PACKAGE_SCHEMA_VERSION",
    "DERIVATIVE_EXPORT_PROVENANCE_SCHEMA_VERSION",
    "MAX_PACKAGE_ENTRIES",
    "MAX_PACKAGE_ENTRY_BYTES",
    "MAX_PACKAGE_TOTAL_BYTES",
    "DerivativeExportPackageEntry",
    "DerivativeExportPackageManifest",
    "build_derivative_export_package",
    "derivative_export_package_hash",
    "provenance_payload",
    "validate_package_inputs",
]
