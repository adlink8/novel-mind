"""Deterministic derivative export preparation freeze (Phase 39-05, D-39-01/D-39-02).

Ownership (39-05 PLAN): this module owns the deterministic ``prepare_export``
operation and the ``preparation_hash`` contract for the Agent consumption slice
of Phase 39. It reads **only** approved/published derivative revisions, assets,
citations and export policy through the existing owner/novel/project-scoped
``ExportSnapshotService`` and freezes the candidate ``ExportPreparationArtifact``
lineage (artifact/revision refs, owner/novel/branch/fork, source snapshot, base
revision, content hashes, evidence refs, validator report, preparation_hash).

Rules:

- ``prepare_export`` is read-only and deterministic: the same DB state always
  freezes the same snapshot, the same sealed manifest and the same
  ``preparation_hash``; it never writes any row.
- The freeze consumes only Fanfiction Canon rows (D-39-02); a cross-owner /
  Original / future-scope / archived project fails closed with an explicit
  ``ExportSnapshotError``.
- ``export_preparation_hash`` is the byte-replayable hash the
  ``approve_export`` ApprovalRequest and the deterministic materializer bind:
  forged/stale hash, wrong scope and a mutated manifest always fail closed.
- The pure gates here are DB-free so unit tests can prove parity / membership /
  hash-replay / reproducibility without a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.derivative_export.manifest import (
    DERIVATIVE_EXPORT_VERSION,
    DerivativeExportManifest,
    canonical_export_hash,
    seal_derivative_export_manifest,
)
from app.services.derivative_export.snapshot import (
    ExportSnapshot,
    ExportSnapshotService,
    FrozenDerivativeExport,
)
from app.services.derivative_visual.assets import DerivativeAssetStorage

EXPORT_PREPARATION_SCHEMA_VERSION = "export-preparation.v1"
EXPORT_PREPARATION_ARTIFACT_KIND = "export_preparation"
EXPORT_PREPARATION_HASH_SCHEMA = "derivative-export-preparation.v1"

HEX64_RE_SET = frozenset("0123456789abcdef")


class ExportPreparationError(ValueError):
    """Fail-closed derivative export preparation gate violation (stable code)."""

    def __init__(self, code: str, detail: str, status_code: int = 400) -> None:
        self.code = code
        self.detail = detail
        self.status_code = status_code
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class FrozenExportPreparation:
    """The frozen snapshot + sealed manifest + preparation lineage.

    ``frozen`` carries the asset-bytes reader seam so the deterministic
    materializer can produce a reproducible bundle from the same freeze.
    """

    snapshot: ExportSnapshot
    manifest: DerivativeExportManifest
    frozen: FrozenDerivativeExport
    preparation_payload: dict[str, Any]
    preparation_hash: str


# ---------------------------------------------------------------------------
# Pure preparation gates (DB-free, unit/adversarial testable)
# ---------------------------------------------------------------------------


def _is_hex64(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in HEX64_RE_SET for ch in value)
    )


def export_preparation_hash(
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    fork_id: int,
    branch: str | None,
    fork: str | None,
    snapshot_hash: str,
    manifest_hash: str,
) -> str:
    """Byte-replayable preparation hash binding the frozen export lineage.

    The hash covers the owner/novel/project/fork scope, the branch/fork intent
    refs and the frozen snapshot + manifest hashes. Artifact revision binding is
    carried by the ApprovalRequest's ``artifact_id`` / ``artifact_revision_id``
    columns plus a current-revision check — the preparation hash itself must be
    knowable before finalize so the deterministic freeze and the approval can
    bind the exact same value. The ``approve_export`` ApprovalRequest and the
    deterministic materializer bind the same hash; a forged/stale hash or a
    mutated manifest always fails closed.
    """
    payload = {
        "schema_version": EXPORT_PREPARATION_HASH_SCHEMA,
        "owner_id": owner_id,
        "novel_id": novel_id,
        "project_id": project_id,
        "fork_id": fork_id,
        "branch": branch,
        "fork": fork,
        "snapshot_hash": snapshot_hash,
        "manifest_hash": manifest_hash,
    }
    return canonical_export_hash(payload)


def export_preparation_payload(
    snapshot: ExportSnapshot,
    manifest: DerivativeExportManifest,
    *,
    branch: str | None,
    fork: str | None,
    evidence_refs: list[str],
    generator_lineage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The candidate ``ExportPreparationPayload`` carried by the envelope.

    ``content_hash`` claims the frozen manifest hash; the deterministic
    ``prepare_export`` operation re-freezes the same state and replays it
    (stale / forged claim -> fail closed).
    """
    return {
        "schema_version": EXPORT_PREPARATION_SCHEMA_VERSION,
        "artifact_kind": EXPORT_PREPARATION_ARTIFACT_KIND,
        "authority_space": "derivative",
        "fork": fork,
        "project_id": snapshot.project_id,
        "project_key": snapshot.project_key,
        "source_snapshot": {
            "source_snapshot_id": f"novel:{snapshot.novel_id}:{fork}",
            "source_snapshot_hash": snapshot.source_snapshot,
            "source_manifest_hash": snapshot.project_manifest_hash,
            "cutoff_chapter": len(snapshot.chapters),
        },
        "base_revision": {
            "project_manifest_hash": snapshot.project_manifest_hash,
            "scope_hash": snapshot.scope_hash,
            "cutoff_snapshot_hash": snapshot.cutoff_snapshot_hash,
            "text_version_hash": snapshot.text_version_hash,
        },
        "content_hash": snapshot.snapshot_hash,
        "evidence_refs": list(evidence_refs),
        "generator_lineage": dict(generator_lineage or {}),
        "validator_report": {
            "evaluator_id": "derivative-export.preparation.v1",
            "verdict": "candidate",
            "reasons": ["deterministic_preparation_ok"],
        },
        "review_state": "candidate",
    }


def validate_preparation_payload(
    payload: dict[str, Any],
    *,
    snapshot: ExportSnapshot,
    manifest: DerivativeExportManifest,
    project_id: int,
    fork: str | None,
) -> list[str]:
    """Parity/membership/hash gates over a claimed ``ExportPreparationPayload``.

    Returns fail-closed codes (empty = pass). The claimed content hash must
    replay the frozen snapshot hash; the project/fork/source-snapshot lineage
    must be within the frozen scope; the review_state must stay candidate
    (approval bypass is blocked before any materialization).
    """
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["preparation_payload_missing"]
    if payload.get("review_state") != "candidate":
        errors.append("review_state_denied")
    if payload.get("project_id") != project_id:
        errors.append("project_mismatch")
    if payload.get("fork") != fork:
        errors.append("fork_mismatch")
    if payload.get("content_hash") != snapshot.snapshot_hash:
        errors.append("content_hash_stale")
    claimed_snapshot = payload.get("source_snapshot") or {}
    if claimed_snapshot.get("source_snapshot_hash") != snapshot.source_snapshot:
        errors.append("source_snapshot_mismatch")
    base = payload.get("base_revision") or {}
    if base.get("project_manifest_hash") != snapshot.project_manifest_hash:
        errors.append("project_manifest_mismatch")
    if base.get("scope_hash") != snapshot.scope_hash:
        errors.append("scope_hash_mismatch")
    if base.get("cutoff_snapshot_hash") != snapshot.cutoff_snapshot_hash:
        errors.append("cutoff_snapshot_mismatch")
    if base.get("text_version_hash") != snapshot.text_version_hash:
        errors.append("text_version_mismatch")
    evidence = payload.get("evidence_refs")
    if not isinstance(evidence, list) or not evidence:
        errors.append("evidence_missing")
    if not _is_hex64(payload.get("content_hash")):
        errors.append("content_hash_malformed")
    return errors


# ---------------------------------------------------------------------------
# Deterministic freeze (read-only, owner/novel/project scoped)
# ---------------------------------------------------------------------------


async def prepare_export(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    branch: str | None = None,
    fork: str | None = None,
    evidence_refs: list[str] | None = None,
    generator_lineage: dict[str, Any] | None = None,
    storage: DerivativeAssetStorage | None = None,
) -> FrozenExportPreparation:
    """Freeze the owner/novel/project-scoped export preparation (read-only).

    Re-uses ``ExportSnapshotService.build`` so the same DB state always freezes
    the same snapshot + sealed manifest; the ``preparation_hash`` replays the
    frozen lineage. Any parity/membership/security mismatch fails closed with an
    explicit ``ExportSnapshotError``.
    """
    frozen = await ExportSnapshotService(db, storage=storage).build(
        owner_id=owner_id, novel_id=novel_id, project_id=project_id
    )
    snapshot = frozen.snapshot
    manifest = seal_derivative_export_manifest(snapshot)
    payload = export_preparation_payload(
        snapshot,
        manifest,
        branch=branch,
        fork=fork or snapshot.fork_key,
        evidence_refs=list(evidence_refs or []),
        generator_lineage=generator_lineage,
    )
    preparation_hash = export_preparation_hash(
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project_id,
        fork_id=snapshot.fork_id,
        branch=branch,
        fork=fork or snapshot.fork_key,
        snapshot_hash=snapshot.snapshot_hash,
        manifest_hash=manifest.manifest_hash,
    )
    return FrozenExportPreparation(
        snapshot=snapshot,
        manifest=manifest,
        frozen=frozen,
        preparation_payload=payload,
        preparation_hash=preparation_hash,
    )


__all__ = [
    "EXPORT_PREPARATION_ARTIFACT_KIND",
    "EXPORT_PREPARATION_HASH_SCHEMA",
    "EXPORT_PREPARATION_SCHEMA_VERSION",
    "DERIVATIVE_EXPORT_VERSION",
    "ExportPreparationError",
    "FrozenExportPreparation",
    "export_preparation_hash",
    "export_preparation_payload",
    "prepare_export",
    "validate_preparation_payload",
]
