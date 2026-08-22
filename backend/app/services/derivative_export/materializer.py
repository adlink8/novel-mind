"""Deterministic derivative export materialization (Phase 39-05, D-39-01/D-39-02).

Ownership (39-05 PLAN): this module owns the ``approve_export`` approval
creation (server-authoritative, bound to the artifact revision + preparation_hash)
and the deterministic ``materialize_export`` operation. ``materialize_export``
accepts **only** an approved ``approve_export`` ApprovalRequest whose payload
replays the frozen preparation_hash of an approvable ``ExportPreparationArtifact``;
it then promotes the candidate artifact to approved and produces a reproducible
bundle through the existing 39-01/02 package/serializer services (frozen
manifest replay).

Rules (fail closed, no authoritative write on any violation):

- forged/stale/expired/cancelled/rejected approval -> blocked; only an approved
  ``approve_export`` approval with an identical preparation_hash may materialize.
- wrong owner/novel/branch/fork/project scope -> blocked; an Original/future
  scope or an archived project fails inside ``ExportSnapshotService``.
- stale preparation hash (DB state changed after the approval) -> blocked; the
  bundle is reproducible from the frozen manifest and the materializer re-freezes
  the exact same state before producing it.
- pending/rejected/published artifact -> blocked; only a candidate artifact under
  an approved matching approval can reach the bundle (and then becomes approved).
- ``download`` is read-only and never changes Artifact status or approval lineage.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_runtime import ApprovalRequest, Artifact, ArtifactRevision
from app.services.agent_runtime.artifacts import (
    get_artifact,
    transition_artifact_status,
)
from app.services.derivative_export.package import (
    DERIVATIVE_EXPORT_PACKAGE_SCHEMA_VERSION,
    build_derivative_export_package,
)
from app.services.derivative_export.preparation import (
    export_preparation_hash,
    prepare_export,
    validate_preparation_payload,
)
from app.services.derivative_export.snapshot import ExportSnapshotError
from app.services.derivative_visual.assets import (
    DerivativeAssetStorage,
    DerivativeAssetStorageError,
)

# Phase 39-05 Agent approval action (versioned prepare-export Skill).
APPROVE_EXPORT_APPROVAL_ACTION = "approve_export"

# Stable fail-closed codes for the export approval / materialization boundary.
CODE_APPROVAL_NOT_FOUND = "approval_not_found"
CODE_APPROVAL_NOT_APPROVED = "approval_not_approved"
CODE_APPROVAL_HASH_MISMATCH = "approval_hash_mismatch"
CODE_PREPARATION_HASH_MISMATCH = "preparation_hash_mismatch"
CODE_ARTIFACT_NOT_FOUND = "artifact_not_found"
CODE_ARTIFACT_NOT_APPROVABLE = "artifact_not_approvable"
CODE_ARTIFACT_TYPE_MISMATCH = "artifact_type_mismatch"
CODE_ARTIFACT_REVISION_STALE = "artifact_revision_stale"
CODE_PREPARATION_PARITY = "preparation_parity"
CODE_FORK_SCOPE_MISMATCH = "fork_scope_mismatch"
CODE_PROJECT_SCOPE_MISMATCH = "project_scope_mismatch"
CODE_BUNDLE_BLOCKED = "bundle_blocked"


class ExportMaterializationError(ValueError):
    """Fail-closed 39-05 export approval/materialization boundary violation."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


# Asset-bytes seam for the deterministic bundle (integration tests override).
_materializer_asset_storage: DerivativeAssetStorage | None = None


def set_materializer_asset_storage(storage: DerivativeAssetStorage | None) -> None:
    """Override the derivative asset bytes backend (used by integration tests)."""
    global _materializer_asset_storage
    _materializer_asset_storage = storage


def _asset_storage() -> DerivativeAssetStorage:
    if _materializer_asset_storage is not None:
        return _materializer_asset_storage
    try:
        return DerivativeAssetStorage(DerivativeAssetStorage.default_storage_root())
    except DerivativeAssetStorageError:
        return DerivativeAssetStorage(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Artifact lineage loading
# ---------------------------------------------------------------------------


async def _load_export_artifact(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    artifact_id: int,
    artifact_revision_id: int,
) -> tuple[Artifact, ArtifactRevision, dict[str, Any]]:
    """Load an owner/novel-scoped candidate ExportPreparationArtifact + revision.

    Returns ``(artifact, revision, preparation_payload)`` or raises a stable
    fail-closed error.
    """
    artifact = await get_artifact(
        db, artifact_id=artifact_id, owner_id=owner_id, novel_id=novel_id
    )
    if artifact is None:
        raise ExportMaterializationError(
            CODE_ARTIFACT_NOT_FOUND,
            "export preparation artifact not found in the owner/novel scope",
        )
    if artifact.type != "export_preparation":
        raise ExportMaterializationError(
            CODE_ARTIFACT_TYPE_MISMATCH,
            f"artifact {artifact.id} is {artifact.type!r}; not an export preparation",
        )
    if artifact.current_revision_id != artifact_revision_id:
        raise ExportMaterializationError(
            CODE_ARTIFACT_REVISION_STALE,
            f"artifact revision {artifact_revision_id} is not the current revision "
            f"({artifact.current_revision_id}) of artifact {artifact.id}",
        )
    revision = await db.get(ArtifactRevision, artifact_revision_id)
    if revision is None:
        raise ExportMaterializationError(
            CODE_ARTIFACT_NOT_FOUND,
            "artifact revision not found in the owner/novel scope",
        )
    content = dict(revision.content or {})
    preparation = content.get("preparation")
    if not isinstance(preparation, dict):
        raise ExportMaterializationError(
            CODE_PREPARATION_PARITY,
            "export preparation artifact content carries no preparation payload",
        )
    return artifact, revision, preparation


async def _freeze_and_verify(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    branch: str | None,
    fork: str | None,
    artifact: Artifact,
    preparation: dict[str, Any],
    storage: DerivativeAssetStorage | None,
) -> Any:
    """Deterministic re-freeze + parity/hash replay against the frozen artifact.

    Returns the ``FrozenExportPreparation`` when the claimed preparation payload
    replays the frozen snapshot/manifest; otherwise raises a stable error.
    """
    try:
        frozen = await prepare_export(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            project_id=project_id,
            branch=branch,
            fork=fork,
            evidence_refs=list(preparation.get("evidence_refs") or []),
            generator_lineage=dict(preparation.get("generator_lineage") or {}),
            storage=storage,
        )
    except ExportSnapshotError as exc:
        raise ExportMaterializationError(CODE_PROJECT_SCOPE_MISMATCH, str(exc)) from exc

    snapshot = frozen.snapshot
    manifest = frozen.manifest
    errors = validate_preparation_payload(
        preparation,
        snapshot=snapshot,
        manifest=manifest,
        project_id=project_id,
        fork=fork or snapshot.fork_key,
    )
    if errors:
        raise ExportMaterializationError(
            CODE_PREPARATION_PARITY,
            "claimed export preparation lineage does not replay the frozen "
            "snapshot/manifest: " + "; ".join(sorted(set(errors))),
        )
    return frozen


# ---------------------------------------------------------------------------
# approve_export action (server-authoritative, candidate-only)
# ---------------------------------------------------------------------------


async def find_approve_export_approval(
    db: AsyncSession,
    *,
    owner_id: int,
    fork_id: int,
    preparation_hash: str,
) -> ApprovalRequest | None:
    """Latest ``approve_export`` ApprovalRequest with the identical hash."""
    return await db.scalar(
        select(ApprovalRequest)
        .where(
            ApprovalRequest.owner_id == owner_id,
            ApprovalRequest.action == APPROVE_EXPORT_APPROVAL_ACTION,
            ApprovalRequest.fork_id == fork_id,
            ApprovalRequest.payload_hash == preparation_hash,
        )
        .order_by(ApprovalRequest.id.desc())
        .limit(1)
    )


async def request_approve_export(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    artifact_id: int,
    artifact_revision_id: int,
    preparation_hash: str,
    actor_id: int,
    branch: str | None = None,
    fork: str | None = None,
    approval_note: str | None = None,
    run_id: int | None = None,
    skill_version_id: int | None = None,
    storage: DerivativeAssetStorage | None = None,
) -> dict[str, Any]:
    """Server-authoritative ``approve_export`` action (39-05, candidate-only).

    Loads the candidate ExportPreparationArtifact + current revision in the
    owner/novel scope, deterministically re-freezes the approved-only snapshot +
    sealed manifest and verifies the claimed preparation payload replays it
    (stale/forged hash -> fail closed). Creates one pending Web ApprovalRequest
    (action=approve_export) whose payload_hash binds the artifact revision + the
    preparation_hash (D-11/D-15). Same artifact + identical lineage replay the
    existing approval (idempotent). It never materializes and never writes an
    Original / domain row.
    """
    artifact, _revision, preparation = await _load_export_artifact(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        artifact_id=artifact_id,
        artifact_revision_id=artifact_revision_id,
    )
    if artifact.status != "candidate":
        raise ExportMaterializationError(
            CODE_ARTIFACT_NOT_APPROVABLE,
            f"artifact {artifact.id} is {artifact.status!r}; only a candidate "
            "export preparation can be approved",
        )
    if preparation.get("project_id") != project_id:
        raise ExportMaterializationError(
            CODE_PROJECT_SCOPE_MISMATCH,
            f"preparation project_id {preparation.get('project_id')!r} does not "
            f"match {project_id!r} (wrong project scope)",
        )

    frozen = await _freeze_and_verify(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project_id,
        branch=branch,
        fork=fork,
        artifact=artifact,
        preparation=preparation,
        storage=storage or _asset_storage(),
    )
    snapshot = frozen.snapshot

    computed = export_preparation_hash(
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project_id,
        fork_id=snapshot.fork_id,
        branch=branch,
        fork=fork or snapshot.fork_key,
        snapshot_hash=snapshot.snapshot_hash,
        manifest_hash=frozen.manifest.manifest_hash,
    )
    if computed != preparation_hash:
        raise ExportMaterializationError(
            CODE_PREPARATION_HASH_MISMATCH,
            "supplied preparation_hash does not replay the frozen export lineage "
            "(stale hash)",
        )

    # 幂等重放：同一 artifact + 相同 frozen lineage 已存在 approval → 复用。
    existing = await find_approve_export_approval(
        db,
        owner_id=owner_id,
        fork_id=snapshot.fork_id,
        preparation_hash=computed,
    )
    if existing is not None:
        return _approve_view_for_tool(
            artifact, existing, preparation_hash, replayed=True
        )

    approval = ApprovalRequest(
        owner_id=owner_id,
        run_id=run_id,
        skill_version_id=skill_version_id,
        artifact_id=artifact.id,
        artifact_revision_id=artifact.current_revision_id,
        novel_id=novel_id,
        branch_id=None,
        fork_id=snapshot.fork_id,
        action=APPROVE_EXPORT_APPROVAL_ACTION,
        payload_summary={
            "project_id": project_id,
            "project_key": snapshot.project_key,
            "fork_id": snapshot.fork_id,
            "fork": fork or snapshot.fork_key,
            "branch": branch,
            "artifact_id": artifact.id,
            "artifact_revision_id": artifact.current_revision_id,
            "snapshot_hash": snapshot.snapshot_hash,
            "manifest_hash": frozen.manifest.manifest_hash,
            "approval_note": (approval_note or "")[:400],
        },
        payload_hash=computed,
        status="pending",
        expires_at=None,
    )
    db.add(approval)
    await db.flush()
    return _approve_view_for_tool(artifact, approval, computed, replayed=False)


# ---------------------------------------------------------------------------
# Deterministic materialize_export (approved-only, bundle producer)
# ---------------------------------------------------------------------------


async def materialize_export(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    artifact_id: int,
    artifact_revision_id: int,
    approval_id: int,
    preparation_hash: str,
    reason: str | None = None,
    actor_id: int | None = None,
    branch: str | None = None,
    fork: str | None = None,
    storage: DerivativeAssetStorage | None = None,
) -> dict[str, Any]:
    """Deterministic materializer: consume an approved approve_export approval.

    This is the **only** Agent-path operation that produces a bundle
    (D-39-01/D-39-02). It verifies, in order:

    - the ``approve_export`` ApprovalRequest exists, is owned and ``approved``;
    - the candidate ExportPreparationArtifact + current revision exist in the
      owner/novel scope and are approvable (candidate/approved; rejected and
      published fail closed);
    - the artifact revision is the current revision (stale revision -> fail);
    - the approval payload still replays the frozen preparation (forged/stale
      approval -> fail);
    - the supplied preparation_hash still replays the frozen lineage (stale
      DB state -> fail);
    - the approval fork matches the frozen project fork (wrong fork scope ->
      fail).

    Only then does it promote the candidate artifact to approved
    (candidate -> validated -> approved) and produce the reproducible bundle
    through ``build_derivative_export_package`` (frozen manifest replay).
    It never writes an Original row, never touches approval lineage and never
    promotes any active pointer.
    """
    approval = await db.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.id == approval_id,
            ApprovalRequest.owner_id == owner_id,
        )
    )
    if approval is None:
        raise ExportMaterializationError(
            CODE_APPROVAL_NOT_FOUND,
            "approve_export approval not found in the owner scope",
        )
    if approval.action != APPROVE_EXPORT_APPROVAL_ACTION:
        raise ExportMaterializationError(
            CODE_APPROVAL_NOT_FOUND,
            f"approval {approval.id} is {approval.action!r}; not an "
            "approve_export approval",
        )
    if approval.status != "approved":
        raise ExportMaterializationError(
            CODE_APPROVAL_NOT_APPROVED,
            f"approve_export approval {approval.id} is {approval.status!r}; only "
            "an approved approval can be consumed",
        )

    artifact, _revision, preparation = await _load_export_artifact(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        artifact_id=artifact_id,
        artifact_revision_id=artifact_revision_id,
    )
    if artifact.status not in ("candidate", "approved"):
        raise ExportMaterializationError(
            CODE_ARTIFACT_NOT_APPROVABLE,
            f"artifact {artifact.id} is {artifact.status!r}; a rejected/published "
            "export preparation cannot be materialized",
        )
    if preparation.get("project_id") != project_id:
        raise ExportMaterializationError(
            CODE_PROJECT_SCOPE_MISMATCH,
            f"preparation project_id {preparation.get('project_id')!r} does not "
            f"match {project_id!r} (wrong project scope)",
        )

    storage = storage or _asset_storage()
    frozen = await _freeze_and_verify(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project_id,
        branch=branch,
        fork=fork,
        artifact=artifact,
        preparation=preparation,
        storage=storage,
    )
    snapshot = frozen.snapshot

    if approval.fork_id != snapshot.fork_id:
        raise ExportMaterializationError(
            CODE_FORK_SCOPE_MISMATCH,
            f"approval fork_id {approval.fork_id} does not match the frozen "
            f"project fork_id {snapshot.fork_id} (wrong fork scope)",
        )

    computed = export_preparation_hash(
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project_id,
        fork_id=snapshot.fork_id,
        branch=branch,
        fork=fork or snapshot.fork_key,
        snapshot_hash=snapshot.snapshot_hash,
        manifest_hash=frozen.manifest.manifest_hash,
    )
    if computed != approval.payload_hash:
        raise ExportMaterializationError(
            CODE_APPROVAL_HASH_MISMATCH,
            "approve_export approval payload no longer replays the frozen export "
            "lineage (forged/stale approval)",
        )
    if computed != preparation_hash:
        raise ExportMaterializationError(
            CODE_PREPARATION_HASH_MISMATCH,
            "supplied preparation_hash no longer replays the frozen export "
            "lineage (stale preparation)",
        )

    # Promote the candidate artifact to approved (forward-only legal transition).
    if artifact.status == "candidate":
        await transition_artifact_status(
            db, artifact_id=artifact.id, owner_id=owner_id, to_status="validated"
        )
        await transition_artifact_status(
            db, artifact_id=artifact.id, owner_id=owner_id, to_status="approved"
        )
        artifact = await get_artifact(
            db, artifact_id=artifact.id, owner_id=owner_id, novel_id=novel_id
        )

    # Produce the reproducible bundle (frozen manifest replay; missing bytes fail).
    try:
        payload, package_manifest = build_derivative_export_package(
            snapshot, frozen.frozen.asset_reader()
        )
    except ExportSnapshotError as exc:
        raise ExportMaterializationError(CODE_BUNDLE_BLOCKED, str(exc)) from exc

    return {
        "owner_id": owner_id,
        "novel_id": novel_id,
        "project_id": project_id,
        "fork_id": snapshot.fork_id,
        "artifact_id": artifact.id,
        "artifact_revision_id": artifact.current_revision_id,
        "approval_request_id": approval.id,
        "approval_action": approval.action,
        "approval_status": approval.status,
        "preparation_hash": computed,
        "snapshot_hash": snapshot.snapshot_hash,
        "manifest_hash": frozen.manifest.manifest_hash,
        "package_hash": package_manifest.package_hash,
        "package_schema_version": DERIVATIVE_EXPORT_PACKAGE_SCHEMA_VERSION,
        "bundle_size": len(payload),
        "bundle_formats": ["package"],
        "status": "approved",
        "candidate_only": False,
        "materialized": True,
    }


def _approve_view_for_tool(
    artifact: Artifact,
    approval: ApprovalRequest,
    preparation_hash: str,
    *,
    replayed: bool,
) -> dict[str, Any]:
    """Artifact + ApprovalRequest ORM -> JSON-safe tool response.

    candidate-only：artifact status 恒为 candidate、绝不 approved/published；
    approval_request_id / payload_hash 供 Web 审批轮询与确定性 materializer 引用。
    """
    return {
        "owner_id": artifact.owner_id,
        "novel_id": artifact.novel_id,
        "project_id": None,
        "artifact_id": artifact.id,
        "artifact_revision_id": artifact.current_revision_id,
        "preparation_hash": preparation_hash,
        "approval_request_id": approval.id,
        "approval_action": approval.action,
        "approval_status": approval.status,
        "approval_payload_hash": approval.payload_hash,
        "status": artifact.status,
        "candidate_only": True,
        "replayed": bool(replayed),
    }


__all__ = [
    "APPROVE_EXPORT_APPROVAL_ACTION",
    "CODE_APPROVAL_HASH_MISMATCH",
    "CODE_APPROVAL_NOT_APPROVED",
    "CODE_APPROVAL_NOT_FOUND",
    "CODE_ARTIFACT_NOT_APPROVABLE",
    "CODE_ARTIFACT_NOT_FOUND",
    "CODE_ARTIFACT_REVISION_STALE",
    "CODE_ARTIFACT_TYPE_MISMATCH",
    "CODE_BUNDLE_BLOCKED",
    "CODE_FORK_SCOPE_MISMATCH",
    "CODE_PREPARATION_HASH_MISMATCH",
    "CODE_PREPARATION_PARITY",
    "CODE_PROJECT_SCOPE_MISMATCH",
    "ExportMaterializationError",
    "find_approve_export_approval",
    "materialize_export",
    "request_approve_export",
    "set_materializer_asset_storage",
]
