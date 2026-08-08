"""Independent derivative export lineage audit (Phase 39-04, T-39-04-01/02).

Every audit verdict carries raw evidence links and can be recomputed from the
manifest / snapshot / ExportPreparationArtifact / ApprovalRequest / materialized
bundle. The only release-gate verdicts are ``qualified_candidate`` and
``blocked`` — there is no promotion path.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Iterable

from pydantic import ConfigDict, Field, model_validator

from app.services.derivative_export.audit_shipment import (
    DerivativeExportAuditStatus,
    _StrictAuditModel,
    _dimension_status,
)
from app.services.derivative_export.package import validate_package_inputs
from app.services.derivative_export.preparation import (
    export_preparation_hash,
    validate_preparation_payload,
)
from app.services.derivative_export.snapshot import (
    ExportSnapshot,
    export_snapshot_hash,
)

DERIVATIVE_EXPORT_LINEAGE_SCHEMA_VERSION = "derivative-export-lineage-audit.v1"


class DerivativeExportLineageCheckKind(StrEnum):
    """One independently auditable link of the export lineage."""

    SOURCE_SNAPSHOT = "source_snapshot"
    MANIFEST = "manifest"
    PARITY = "parity"
    PREPARATION_HASH = "preparation_hash"
    PREPARATION_PAYLOAD = "preparation_payload"
    ARTIFACT_BINDING = "artifact_binding"
    APPROVAL_BINDING = "approval_binding"
    MATERIALIZATION = "materialization"
    DOWNLOAD_AUDIT = "download_audit"
    EPUB_VALIDATION = "epub_validation"


# Stable recompute-able evidence locations (T-39-04-01 repudiation guard).
LINEAGE_RAW_EVIDENCE_LINKS: dict[str, str] = {
    DerivativeExportLineageCheckKind.SOURCE_SNAPSHOT.value: (
        "backend/app/services/derivative_export/snapshot.py:export_snapshot_hash"
    ),
    DerivativeExportLineageCheckKind.MANIFEST.value: (
        "backend/app/services/derivative_export/manifest.py:"
        "derivative_export_manifest_hash"
    ),
    DerivativeExportLineageCheckKind.PARITY.value: (
        "backend/app/services/derivative_export/package.py:validate_package_inputs"
    ),
    DerivativeExportLineageCheckKind.PREPARATION_HASH.value: (
        "backend/app/services/derivative_export/preparation.py:export_preparation_hash"
    ),
    DerivativeExportLineageCheckKind.PREPARATION_PAYLOAD.value: (
        "backend/app/services/derivative_export/preparation.py:"
        "validate_preparation_payload"
    ),
    DerivativeExportLineageCheckKind.ARTIFACT_BINDING.value: (
        "backend/app/models/agent_runtime.py:Artifact"
    ),
    DerivativeExportLineageCheckKind.APPROVAL_BINDING.value: (
        "backend/app/models/agent_runtime.py:ApprovalRequest"
    ),
    DerivativeExportLineageCheckKind.MATERIALIZATION.value: (
        "backend/app/services/derivative_export/package.py:"
        "build_derivative_export_package"
    ),
    DerivativeExportLineageCheckKind.DOWNLOAD_AUDIT.value: (
        "backend/app/api/derivative_export.py:download_derivative_export"
    ),
    DerivativeExportLineageCheckKind.EPUB_VALIDATION.value: (
        "backend/app/services/derivative_export/epub.py:render_epub"
    ),
}


def _is_hex64(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


class DerivativeExportLineageCheck(_StrictAuditModel):
    """One lineage check with its raw evidence link and blocked reasons."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: DerivativeExportLineageCheckKind
    status: DerivativeExportAuditStatus
    raw_evidence_link: str = Field(min_length=1, max_length=500)
    detail: str = Field(default="", max_length=1000)
    blocked_reasons: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _tupleize(cls, value: Any) -> Any:
        if isinstance(value, dict) and isinstance(value.get("blocked_reasons"), list):
            value = {**value, "blocked_reasons": tuple(value["blocked_reasons"])}
        return value


class DerivativeExportLineageAudit(_StrictAuditModel):
    """The complete independently recomputed export lineage audit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = DERIVATIVE_EXPORT_LINEAGE_SCHEMA_VERSION
    checks: tuple[DerivativeExportLineageCheck, ...]

    @model_validator(mode="before")
    @classmethod
    def _tupleize(cls, value: Any) -> Any:
        if isinstance(value, dict) and isinstance(value.get("checks"), list):
            value = {**value, "checks": tuple(value["checks"])}
        return value

    @property
    def status(self) -> DerivativeExportAuditStatus:
        return _dimension_status(
            [
                check.kind.value
                for check in self.checks
                if check.status == DerivativeExportAuditStatus.BLOCKED
            ],
            [
                check.kind.value
                for check in self.checks
                if check.status == DerivativeExportAuditStatus.PARTIAL
            ],
        )

    @property
    def blocked_reasons(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                f"lineage_{check.status.value}:{check.kind.value}"
                for check in self.checks
                if check.status
                in (
                    DerivativeExportAuditStatus.BLOCKED,
                    DerivativeExportAuditStatus.PARTIAL,
                )
            )
        )


def audit_derivative_export_lineage(
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    fork_id: int,
    snapshot_hash: str,
    manifest_hash: str,
    preparation_hash: str | None,
    snapshot: ExportSnapshot | dict[str, Any] | None = None,
    preparation_payload: dict[str, Any] | None = None,
    artifact_status: str | None = None,
    artifact_revision_id: int | None = None,
    artifact_preparation_hash: str | None = None,
    approval_action: str | None = None,
    approval_status: str | None = None,
    approval_artifact_revision_id: int | None = None,
    approval_payload_hash: str | None = None,
    branch: str | None = None,
    fork: str | None = None,
    package_hash: str | None = None,
    replayed_package_hash: str | None = None,
    download_manifest_hash: str | None = None,
    epub_validated: bool = False,
) -> DerivativeExportLineageAudit:
    """Independently recompute the full export lineage (pure, DB-free).

    Each check either replays from the provided evidence (deterministic
    recompute) or fails closed with an explicit blocked reason — an orphaned
    artifact, a lineage/hash mismatch, contamination, an Original mutation, an
    unauthorized export, an unverified EPUB or a missing download/audit event
    can never be silently skipped.
    """

    def _check(
        kind: DerivativeExportLineageCheckKind,
        status: DerivativeExportAuditStatus,
        detail: str = "",
        reasons: Iterable[str] = (),
    ) -> DerivativeExportLineageCheck:
        return DerivativeExportLineageCheck(
            kind=kind,
            status=status,
            raw_evidence_link=LINEAGE_RAW_EVIDENCE_LINKS[kind.value],
            detail=detail,
            blocked_reasons=tuple(sorted(set(reasons))),
        )

    checks: list[DerivativeExportLineageCheck] = []

    # --- source snapshot: the frozen snapshot hash must replay ---------------
    if snapshot is None:
        checks.append(
            _check(
                DerivativeExportLineageCheckKind.SOURCE_SNAPSHOT,
                DerivativeExportAuditStatus.BLOCKED,
                "no frozen snapshot evidence to recompute the source lineage",
                ("source_snapshot_evidence_missing",),
            )
        )
    else:
        recomputed = export_snapshot_hash(snapshot)
        if recomputed != snapshot_hash:
            checks.append(
                _check(
                    DerivativeExportLineageCheckKind.SOURCE_SNAPSHOT,
                    DerivativeExportAuditStatus.BLOCKED,
                    f"recomputed snapshot {recomputed[:12]}... does not replay "
                    f"the claimed {snapshot_hash[:12]}...",
                    ("source_snapshot_hash_mismatch",),
                )
            )
        else:
            checks.append(
                _check(
                    DerivativeExportLineageCheckKind.SOURCE_SNAPSHOT,
                    DerivativeExportAuditStatus.VERIFIED,
                    "snapshot hash replays the frozen source snapshot",
                )
            )

    # --- manifest: single canonical hash, replayable -------------------------
    if snapshot is None:
        checks.append(
            _check(
                DerivativeExportLineageCheckKind.MANIFEST,
                DerivativeExportAuditStatus.BLOCKED,
                "no frozen snapshot to derive the manifest",
                ("manifest_evidence_missing",),
            )
        )
    else:
        from app.services.derivative_export.manifest import (
            seal_derivative_export_manifest,
        )

        recomputed_manifest = seal_derivative_export_manifest(snapshot).manifest_hash
        if manifest_hash != snapshot.snapshot_hash:
            checks.append(
                _check(
                    DerivativeExportLineageCheckKind.MANIFEST,
                    DerivativeExportAuditStatus.BLOCKED,
                    "manifest hash diverges from the single snapshot hash",
                    ("manifest_hash_mismatch",),
                )
            )
        elif recomputed_manifest != manifest_hash:
            checks.append(
                _check(
                    DerivativeExportLineageCheckKind.MANIFEST,
                    DerivativeExportAuditStatus.BLOCKED,
                    "manifest hash does not replay the frozen snapshot",
                    ("manifest_hash_recompute_mismatch",),
                )
            )
        else:
            checks.append(
                _check(
                    DerivativeExportLineageCheckKind.MANIFEST,
                    DerivativeExportAuditStatus.VERIFIED,
                    "manifest shares the snapshot's single canonical hash",
                )
            )

    # --- parity / contamination / Original mutation --------------------------
    if snapshot is None:
        checks.append(
            _check(
                DerivativeExportLineageCheckKind.PARITY,
                DerivativeExportAuditStatus.BLOCKED,
                "no frozen snapshot to validate parity",
                ("parity_evidence_missing",),
            )
        )
    else:
        from app.services.derivative_export.manifest import (
            seal_derivative_export_manifest,
        )

        errors = validate_package_inputs(
            snapshot, seal_derivative_export_manifest(snapshot)
        )
        if errors:
            checks.append(
                _check(
                    DerivativeExportLineageCheckKind.PARITY,
                    DerivativeExportAuditStatus.BLOCKED,
                    "contamination / owner-isolation / citation / asset parity "
                    "violation: " + "; ".join(sorted(set(errors))),
                    [errors[0]],
                )
            )
        else:
            checks.append(
                _check(
                    DerivativeExportLineageCheckKind.PARITY,
                    DerivativeExportAuditStatus.VERIFIED,
                    "owner/project/fork/namespace/citation/asset parity is clean",
                )
            )

    # --- preparation hash: byte-replayable lineage hash ----------------------
    if snapshot is None or preparation_hash is None:
        checks.append(
            _check(
                DerivativeExportLineageCheckKind.PREPARATION_HASH,
                DerivativeExportAuditStatus.BLOCKED,
                "no frozen snapshot or claimed preparation hash to replay",
                ("preparation_evidence_missing",),
            )
        )
    else:
        recomputed_prep = export_preparation_hash(
            owner_id=owner_id,
            novel_id=novel_id,
            project_id=project_id,
            fork_id=fork_id or snapshot.fork_id,
            branch=branch,
            fork=fork or snapshot.fork_key,
            snapshot_hash=snapshot.snapshot_hash,
            manifest_hash=manifest_hash,
        )
        if recomputed_prep != preparation_hash:
            checks.append(
                _check(
                    DerivativeExportLineageCheckKind.PREPARATION_HASH,
                    DerivativeExportAuditStatus.BLOCKED,
                    "preparation hash does not replay the frozen "
                    "scope/snapshot/manifest lineage",
                    ("preparation_hash_mismatch",),
                )
            )
        else:
            checks.append(
                _check(
                    DerivativeExportLineageCheckKind.PREPARATION_HASH,
                    DerivativeExportAuditStatus.VERIFIED,
                    "preparation hash replays the frozen export lineage",
                )
            )

    # --- preparation payload parity -------------------------------------------
    if snapshot is None or preparation_payload is None:
        checks.append(
            _check(
                DerivativeExportLineageCheckKind.PREPARATION_PAYLOAD,
                DerivativeExportAuditStatus.BLOCKED,
                "no preparation payload (or frozen snapshot) to validate",
                ("preparation_payload_evidence_missing",),
            )
        )
    else:
        from app.services.derivative_export.manifest import (
            seal_derivative_export_manifest,
        )

        errors = validate_preparation_payload(
            preparation_payload,
            snapshot=snapshot,
            manifest=seal_derivative_export_manifest(snapshot),
            project_id=project_id,
            fork=fork or snapshot.fork_key,
        )
        if errors:
            checks.append(
                _check(
                    DerivativeExportLineageCheckKind.PREPARATION_PAYLOAD,
                    DerivativeExportAuditStatus.BLOCKED,
                    "claimed preparation payload does not replay the frozen "
                    "snapshot/manifest: " + "; ".join(sorted(set(errors))),
                    [errors[0]],
                )
            )
        else:
            checks.append(
                _check(
                    DerivativeExportLineageCheckKind.PREPARATION_PAYLOAD,
                    DerivativeExportAuditStatus.VERIFIED,
                    "preparation payload replays the frozen snapshot/manifest",
                )
            )

    # --- artifact binding (orphaned / pending / rejected -> blocked) ----------
    artifact_reasons: list[str] = []
    if artifact_status is None:
        artifact_reasons.append("artifact_evidence_missing")
    elif artifact_status not in ("candidate", "approved"):
        artifact_reasons.append("artifact_status_denied")
    if (
        artifact_preparation_hash is not None
        and artifact_preparation_hash != preparation_hash
    ):
        artifact_reasons.append("artifact_preparation_hash_mismatch")
    if artifact_reasons:
        checks.append(
            _check(
                DerivativeExportLineageCheckKind.ARTIFACT_BINDING,
                DerivativeExportAuditStatus.BLOCKED,
                "orphaned / pending / rejected / divergent export preparation artifact",
                artifact_reasons,
            )
        )
    else:
        checks.append(
            _check(
                DerivativeExportLineageCheckKind.ARTIFACT_BINDING,
                DerivativeExportAuditStatus.VERIFIED,
                "candidate/approved ExportPreparationArtifact binds the frozen "
                "preparation hash",
            )
        )

    # --- approve_export approval binding --------------------------------------
    approval_reasons: list[str] = []
    if approval_action is None:
        approval_reasons.append("approval_evidence_missing")
    elif approval_action != "approve_export":
        approval_reasons.append("approval_action_denied")
    if approval_status != "approved":
        approval_reasons.append("approval_not_approved")
    if approval_payload_hash is not None and approval_payload_hash != preparation_hash:
        approval_reasons.append("approval_hash_mismatch")
    if (
        artifact_revision_id is not None
        and approval_artifact_revision_id is not None
        and approval_artifact_revision_id != artifact_revision_id
    ):
        approval_reasons.append("approval_revision_mismatch")
    if approval_reasons:
        checks.append(
            _check(
                DerivativeExportLineageCheckKind.APPROVAL_BINDING,
                DerivativeExportAuditStatus.BLOCKED,
                "missing / non-approved / divergent approve_export approval",
                approval_reasons,
            )
        )
    else:
        checks.append(
            _check(
                DerivativeExportLineageCheckKind.APPROVAL_BINDING,
                DerivativeExportAuditStatus.VERIFIED,
                "approved approve_export approval binds the artifact revision "
                "and the preparation hash",
            )
        )

    # --- materialization bundle (tamper-evident package hash) -----------------
    materialization_reasons: list[str] = []
    if package_hash is None:
        materialization_reasons.append("bundle_evidence_missing")
    elif not _is_hex64(package_hash):
        materialization_reasons.append("package_hash_malformed")
    if replayed_package_hash is not None and replayed_package_hash != package_hash:
        materialization_reasons.append("package_hash_mismatch")
    if materialization_reasons:
        checks.append(
            _check(
                DerivativeExportLineageCheckKind.MATERIALIZATION,
                DerivativeExportAuditStatus.BLOCKED,
                "no reproducible materialized bundle / tampered package manifest",
                materialization_reasons,
            )
        )
    else:
        checks.append(
            _check(
                DerivativeExportLineageCheckKind.MATERIALIZATION,
                DerivativeExportAuditStatus.VERIFIED,
                "bundle package hash replays the frozen manifest",
            )
        )

    # --- download / audit event -------------------------------------------------
    if download_manifest_hash is None:
        checks.append(
            _check(
                DerivativeExportLineageCheckKind.DOWNLOAD_AUDIT,
                DerivativeExportAuditStatus.BLOCKED,
                "no download/audit event evidence",
                ("download_evidence_missing",),
            )
        )
    elif download_manifest_hash != manifest_hash:
        checks.append(
            _check(
                DerivativeExportLineageCheckKind.DOWNLOAD_AUDIT,
                DerivativeExportAuditStatus.BLOCKED,
                "download manifest header diverges from the frozen manifest hash",
                ("download_manifest_hash_mismatch",),
            )
        )
    else:
        checks.append(
            _check(
                DerivativeExportLineageCheckKind.DOWNLOAD_AUDIT,
                DerivativeExportAuditStatus.VERIFIED,
                "download header replays the frozen manifest hash",
            )
        )

    # --- EPUB interoperability (unverified is never green) ----------------------
    if epub_validated:
        checks.append(
            _check(
                DerivativeExportLineageCheckKind.EPUB_VALIDATION,
                DerivativeExportAuditStatus.VERIFIED,
                "EPUB interoperability validation evidence present",
            )
        )
    else:
        checks.append(
            _check(
                DerivativeExportLineageCheckKind.EPUB_VALIDATION,
                DerivativeExportAuditStatus.BLOCKED,
                "no EPUB validator evidence; interoperability is unverified "
                "and must not be marked green",
                ("epub_interoperability_unverified",),
            )
        )

    return DerivativeExportLineageAudit(checks=tuple(checks))


async def _find_export_preparation_artifact(
    db: Any,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    snapshot_hash: str,
) -> tuple[Any, Any, dict[str, Any]] | None:
    """Latest owner/novel export_preparation artifact bound to this snapshot."""
    from sqlalchemy import select

    from app.models.agent_runtime import Artifact, ArtifactRevision

    rows = list(
        (
            await db.scalars(
                select(Artifact)
                .where(
                    Artifact.owner_id == owner_id,
                    Artifact.novel_id == novel_id,
                    Artifact.type == "export_preparation",
                )
                .order_by(Artifact.id.desc())
            )
        ).all()
    )
    for artifact in rows:
        if artifact.current_revision_id is None:
            continue
        revision = await db.get(ArtifactRevision, artifact.current_revision_id)
        if revision is None:
            continue
        preparation = dict(revision.content or {}).get("preparation")
        if not isinstance(preparation, dict):
            continue
        if preparation.get("project_id") != project_id:
            continue
        if preparation.get("content_hash") != snapshot_hash:
            continue
        return artifact, revision, preparation
    return None


async def run_derivative_export_lineage_audit(
    db: Any,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int,
    branch: str | None = None,
    fork: str | None = None,
    snapshot_hash: str | None = None,
    manifest_hash: str | None = None,
    preparation_hash: str | None = None,
    storage: Any = None,
    epub_validated: bool = False,
    download_manifest_hash: str | None = None,
) -> DerivativeExportLineageAudit:
    """DB-backed recompute of the complete export lineage for the release gate.

    Re-freezes the owner/novel/project snapshot (deterministic recompute),
    discovers the bound ExportPreparationArtifact + approve_export
    ApprovalRequest, replays the preparation hash and rebuilds the package
    bundle hash; every link is then evaluated by
    ``audit_derivative_export_lineage`` (fail closed on any mismatch).
    """
    from app.services.derivative_export.manifest import (
        seal_derivative_export_manifest,
    )
    from app.services.derivative_export.materializer import (
        find_approve_export_approval,
    )
    from app.services.derivative_export.package import (
        build_derivative_export_package,
    )
    from app.services.derivative_export.preparation import (
        export_preparation_hash,
    )
    from app.services.derivative_export.snapshot import (
        ExportSnapshotError,
        ExportSnapshotService,
    )

    snapshot = None
    frozen = None
    snapshot_hash_observed = snapshot_hash
    manifest_hash_observed = manifest_hash
    try:
        frozen = await ExportSnapshotService(db, storage=storage).build(
            owner_id=owner_id, novel_id=novel_id, project_id=project_id
        )
        snapshot = frozen.snapshot
        snapshot_hash_observed = snapshot.snapshot_hash
        manifest_hash_observed = seal_derivative_export_manifest(snapshot).manifest_hash
    except ExportSnapshotError:
        # Recompute impossible -> the pure lineage audit fails these checks
        # closed (no snapshot evidence), never a silent pass.
        snapshot = None

    artifact_status: str | None = None
    artifact_revision_id: int | None = None
    artifact_preparation_hash: str | None = None
    preparation_payload: dict[str, Any] | None = None
    approval_action: str | None = None
    approval_status: str | None = None
    approval_artifact_revision_id: int | None = None
    approval_payload_hash: str | None = None
    package_hash: str | None = None
    replayed_package_hash: str | None = None
    observed_branch = branch
    observed_fork = fork

    if snapshot is not None:
        found = await _find_export_preparation_artifact(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            project_id=project_id,
            snapshot_hash=snapshot.snapshot_hash,
        )
        if found is not None:
            artifact, _revision, preparation = found
            artifact_status = artifact.status
            artifact_revision_id = artifact.current_revision_id
            observed_branch = artifact.branch or branch
            observed_fork = preparation.get("fork") or fork or snapshot.fork_key
            artifact_preparation_hash = export_preparation_hash(
                owner_id=owner_id,
                novel_id=novel_id,
                project_id=project_id,
                fork_id=snapshot.fork_id,
                branch=observed_branch,
                fork=observed_fork,
                snapshot_hash=snapshot.snapshot_hash,
                manifest_hash=manifest_hash_observed,
            )
            preparation_payload = preparation
            if preparation_hash is None:
                preparation_hash = artifact_preparation_hash

            approval = await find_approve_export_approval(
                db,
                owner_id=owner_id,
                fork_id=snapshot.fork_id,
                preparation_hash=artifact_preparation_hash,
            )
            if approval is not None:
                approval_action = approval.action
                approval_status = approval.status
                approval_artifact_revision_id = approval.artifact_revision_id
                approval_payload_hash = approval.payload_hash

            # Bundle recompute (only meaningful once the lineage reached an
            # approved approval; a missing binary fails closed here).
            if (
                artifact.status in ("candidate", "approved")
                and approval is not None
                and approval.status == "approved"
            ):
                try:
                    _payload, pkg = build_derivative_export_package(
                        snapshot, frozen.asset_reader()
                    )
                    package_hash = pkg.package_hash
                    replayed_package_hash = pkg.package_hash
                except ExportSnapshotError:
                    package_hash = None

    # The deterministic download always serves the single snapshot hash.
    if download_manifest_hash is None and manifest_hash_observed is not None:
        download_manifest_hash = manifest_hash_observed

    return audit_derivative_export_lineage(
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project_id,
        fork_id=snapshot.fork_id if snapshot is not None else 0,
        snapshot_hash=snapshot_hash_observed,
        manifest_hash=manifest_hash_observed,
        preparation_hash=preparation_hash,
        snapshot=snapshot,
        preparation_payload=preparation_payload,
        artifact_status=artifact_status,
        artifact_revision_id=artifact_revision_id,
        artifact_preparation_hash=artifact_preparation_hash,
        approval_action=approval_action,
        approval_status=approval_status,
        approval_artifact_revision_id=approval_artifact_revision_id,
        approval_payload_hash=approval_payload_hash,
        branch=observed_branch,
        fork=observed_fork,
        package_hash=package_hash,
        replayed_package_hash=replayed_package_hash,
        download_manifest_hash=download_manifest_hash,
        epub_validated=epub_validated,
    )


__all__ = [
    "DERIVATIVE_EXPORT_LINEAGE_SCHEMA_VERSION",
    "DerivativeExportLineageAudit",
    "DerivativeExportLineageCheck",
    "DerivativeExportLineageCheckKind",
    "audit_derivative_export_lineage",
    "run_derivative_export_lineage_audit",
]
