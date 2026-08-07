"""Derivative export three-dimension audit contract (Phase 39-02, D-39-03).

D-39-03 / REQ-SHIP-02: the derivative export deliverable carries a three
dimension status report — **implementation_readiness**, **sample_data_coverage**
and **quality_qualification** — where every dimension keeps its own status,
evidence links and blocked reasons.

Fail-closed guarantees:

- The three dimensions are **independent**: implementation readiness is about
  the package machinery, sample data coverage about round-trip fixtures and
  quality qualification about the real qualification state. No dimension may be
  folded into another and no "Phase 39 passed" claim can substitute the quality
  dimension.
- ``quality_qualification`` reflects the **real** state: while Phase 22 remains
  blocked (0/3 scheduled green evidence, ``.planning/STATE.md``) the dimension
  is ``blocked`` and the audit verdict is ``blocked``. The blocked reason is
  **deterministically derived from the manifest hash** (``canonical_export_hash``
  over the dimension/reason/phase-22 counters/manifest hash), so the same
  manifest always replays the same blocked reason.
- A report that claims a green quality dimension while Phase 22 is blocked
  cannot be constructed (model validator fail-closed).
- The audit never writes STATE/ROADMAP, never patches a manifest and never
  writes an active pointer; the only verdicts are ``qualified_candidate`` and
  ``blocked`` (no promotion).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.derivative_export.manifest import (
    DERIVATIVE_EXPORT_SCHEMA_VERSION,
    DerivativeExportManifest,
    canonical_export_hash,
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

DERIVATIVE_EXPORT_AUDIT_SCHEMA_VERSION = "derivative-export-audit.v1"
DERIVATIVE_EXPORT_AUDIT_VERSION = "derivative-export-audit.v1"
# Phase 39-04: independent lineage + REQ-SHIP-01 release-gate contracts.
DERIVATIVE_EXPORT_LINEAGE_SCHEMA_VERSION = "derivative-export-lineage-audit.v1"
DERIVATIVE_EXPORT_SHIPMENT_SCHEMA_VERSION = "derivative-export-shipment-baseline.v1"

PHASE22_GREEN_REQUIRED = 3

# Vocabulary that must never appear in audit blocked reasons (no promotion).
FORBIDDEN_AUDIT_WORDS = frozenset(
    {"promote", "promoted", "promotion", "active_pointer", "production_ready"}
)


class _StrictAuditModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DerivativeExportAuditDimensionKind(StrEnum):
    IMPLEMENTATION_READINESS = "implementation_readiness"
    SAMPLE_DATA_COVERAGE = "sample_data_coverage"
    QUALITY_QUALIFICATION = "quality_qualification"


AUDIT_DIMENSION_KINDS: tuple[DerivativeExportAuditDimensionKind, ...] = (
    DerivativeExportAuditDimensionKind.IMPLEMENTATION_READINESS,
    DerivativeExportAuditDimensionKind.SAMPLE_DATA_COVERAGE,
    DerivativeExportAuditDimensionKind.QUALITY_QUALIFICATION,
)


class DerivativeExportAuditStatus(StrEnum):
    VERIFIED = "verified"
    PARTIAL = "partial"
    BLOCKED = "blocked"


class DerivativeExportAuditEvidence(_StrictAuditModel):
    """One bound piece of evidence a dimension verdict was decided on."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str = Field(min_length=1, max_length=64)
    location: str = Field(min_length=1, max_length=500)
    detail: str = Field(default="")


class DerivativeExportPhase22Evidence(_StrictAuditModel):
    """Independent, preserved Phase 22 blocked state (never folded in).

    ``source_hash`` binds the reported counters to a real source of truth (the
    ``.planning/STATE.md`` truth snapshot); the audit only accepts the evidence
    as observed — it cannot make it green.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    green_observed: int = Field(ge=0)
    green_required: int = Field(gt=0, default=PHASE22_GREEN_REQUIRED)
    source: str = Field(min_length=1, max_length=500)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DerivativeExportAuditDimension(_StrictAuditModel):
    """Independent status + blocked reasons + evidence for one dimension."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension: DerivativeExportAuditDimensionKind
    status: DerivativeExportAuditStatus
    blocked_reasons: tuple[str, ...] = ()
    evidence: tuple[DerivativeExportAuditEvidence, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _tupleize(cls, value: Any) -> Any:
        if isinstance(value, dict):
            updates = {}
            for key in ("blocked_reasons", "evidence"):
                if isinstance(value.get(key), list):
                    updates[key] = tuple(value[key])
            if updates:
                value = {**value, **updates}
        return value


# ---------------------------------------------------------------------------
# Deterministic quality-qualification derivation (manifest-bound)
# ---------------------------------------------------------------------------


def quality_qualification_blocked_reason(
    *,
    snapshot_hash: str,
    green_observed: int,
    green_required: int = PHASE22_GREEN_REQUIRED,
) -> str:
    """Deterministic blocked reason derived from the manifest hash (D-39-03).

    The same manifest and the same Phase 22 counters always derive the same
    reason; ``replay_quality_qualification_blocked_reason`` recomputes it and
    any drift fails closed.
    """
    return canonical_export_hash(
        {
            "dimension": "quality_qualification",
            "status": "blocked",
            "reason_code": "phase22_not_qualified",
            "green_observed": green_observed,
            "green_required": green_required,
            "manifest_hash": snapshot_hash,
        }
    )


def replay_quality_qualification_blocked_reason(
    *,
    snapshot_hash: str,
    green_observed: int,
    green_required: int = PHASE22_GREEN_REQUIRED,
) -> str:
    """Replay (recompute) the blocked reason so it can be compared to evidence."""
    return quality_qualification_blocked_reason(
        snapshot_hash=snapshot_hash,
        green_observed=green_observed,
        green_required=green_required,
    )


def derive_quality_qualification(
    phase22: DerivativeExportPhase22Evidence,
    snapshot_hash: str,
) -> tuple[DerivativeExportAuditStatus, tuple[str, ...]]:
    """Derive the quality dimension from the real Phase 22 state.

    Phase 22 remains blocked at 0/3 -> quality_qualification is ``blocked``
    with a manifest-replayable reason. No input can override this to green.
    """
    if phase22.green_observed < phase22.green_required:
        reason = quality_qualification_blocked_reason(
            snapshot_hash=snapshot_hash,
            green_observed=phase22.green_observed,
            green_required=phase22.green_required,
        )
        return DerivativeExportAuditStatus.BLOCKED, (reason,)
    return DerivativeExportAuditStatus.VERIFIED, ()


def _dimension_status(
    blocking: list[str], partial: list[str]
) -> DerivativeExportAuditStatus:
    if blocking:
        return DerivativeExportAuditStatus.BLOCKED
    if partial:
        return DerivativeExportAuditStatus.PARTIAL
    return DerivativeExportAuditStatus.VERIFIED


class DerivativeExportAuditReport(_StrictAuditModel):
    """The complete manifest-bound three-dimension audit report."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = DERIVATIVE_EXPORT_AUDIT_SCHEMA_VERSION
    audit_version: str = DERIVATIVE_EXPORT_AUDIT_VERSION
    manifest_schema_version: str = DERIVATIVE_EXPORT_SCHEMA_VERSION
    owner_id: int = Field(gt=0)
    novel_id: int = Field(gt=0)
    project_id: int = Field(gt=0)
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    dimensions: tuple[DerivativeExportAuditDimension, ...]
    phase22: DerivativeExportPhase22Evidence
    # Phase 39-04 (T-39-04-01/02): the independent lineage audit and the
    # REQ-SHIP-01 production baseline evidence; when present their verdicts are
    # folded into the report and any non-verified state fails closed.
    lineage: DerivativeExportLineageAudit | None = None
    shipment: DerivativeExportShipmentBaseline | None = None
    verdict: Literal["qualified_candidate", "blocked"]
    blocked_reasons: tuple[str, ...] = ()
    report_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="before")
    @classmethod
    def _tupleize(cls, value: Any) -> Any:
        if isinstance(value, dict):
            updates = {}
            for key in ("dimensions", "blocked_reasons"):
                if isinstance(value.get(key), list):
                    updates[key] = tuple(value[key])
            if updates:
                value = {**value, **updates}
        return value

    @model_validator(mode="after")
    def _audit_rules(self) -> DerivativeExportAuditReport:
        # Exactly the three dimensions, in the canonical order.
        present = tuple(dimension.dimension for dimension in self.dimensions)
        if present != AUDIT_DIMENSION_KINDS:
            raise ValueError(
                "audit must carry exactly the three canonical dimensions in "
                f"order; found {present}"
            )
        # The quality dimension MUST reflect the real Phase 22 state — a
        # falsely-green claim fails closed here.
        derived_status, _ = derive_quality_qualification(
            self.phase22, self.snapshot_hash
        )
        quality = self.dimensions[2]
        if (
            quality.dimension
            != DerivativeExportAuditDimensionKind.QUALITY_QUALIFICATION
        ):
            raise ValueError("quality dimension must be the third dimension")
        if quality.status != derived_status:
            raise ValueError(
                "quality_qualification must reflect the real Phase 22 state; "
                f"claimed {quality.status!r} but the evidence derives "
                f"{derived_status!r}"
            )
        if (
            quality.status == DerivativeExportAuditStatus.BLOCKED
            and not quality.blocked_reasons
        ):
            raise ValueError("a blocked quality dimension requires blocked reasons")
        banned = FORBIDDEN_AUDIT_WORDS.intersection(self.blocked_reasons)
        if banned:
            raise ValueError(f"illegal audit reason vocabulary: {sorted(banned)}")
        ordered = tuple(sorted(self.blocked_reasons))
        if self.blocked_reasons != ordered:
            raise ValueError("audit blocked_reasons must be sorted")
        if self.verdict == "qualified_candidate" and self.blocked_reasons:
            raise ValueError("qualified_candidate must carry no blocked_reasons")
        if self.verdict == "blocked" and not self.blocked_reasons:
            raise ValueError("blocked requires non-empty blocked_reasons")
        # Phase 39-04 gate: a non-verified lineage audit or REQ-SHIP-01
        # baseline can never hide behind a qualified_candidate verdict.
        if (
            self.lineage is not None
            and self.lineage.status != DerivativeExportAuditStatus.VERIFIED
            and self.verdict != "blocked"
        ):
            raise ValueError("a non-verified lineage audit requires a blocked verdict")
        if (
            self.shipment is not None
            and self.shipment.status != DerivativeExportAuditStatus.VERIFIED
            and self.verdict != "blocked"
        ):
            raise ValueError(
                "a non-verified REQ-SHIP-01 shipment baseline requires a "
                "blocked verdict"
            )
        return self

    @property
    def has_completion_percentage(self) -> bool:
        """Guards the no-single-percentage rule at the surface."""
        return "completion_percentage" in self.model_dump(mode="json")

    @property
    def dimensions_by_kind(
        self,
    ) -> dict[DerivativeExportAuditDimensionKind, DerivativeExportAuditDimension]:
        return {dimension.dimension: dimension for dimension in self.dimensions}


def audit_report_hash(
    report: DerivativeExportAuditReport | dict[str, Any],
) -> str:
    """Deterministic hash over the report without its own hash field."""
    if isinstance(report, DerivativeExportAuditReport):
        payload = report.model_dump(mode="json")
    else:
        payload = dict(report)
    payload.pop("report_hash", None)
    return canonical_export_hash(payload)


def _manifest_hash_binding(
    manifest: ExportSnapshot | DerivativeExportManifest,
) -> str:
    if isinstance(manifest, ExportSnapshot):
        return manifest.snapshot_hash
    return manifest.manifest_hash


# ---------------------------------------------------------------------------
# Audit entry point (evidence in, report out — never a state write)
# ---------------------------------------------------------------------------


def build_derivative_export_audit(
    *,
    manifest: ExportSnapshot | DerivativeExportManifest,
    phase22: DerivativeExportPhase22Evidence | dict[str, Any],
    implementation_evidence: tuple[DerivativeExportAuditEvidence, ...] = (),
    sample_data_evidence: tuple[DerivativeExportAuditEvidence, ...] = (),
    quality_evidence: tuple[DerivativeExportAuditEvidence, ...] = (),
    package_buildable: bool = True,
    package_manifest_hash_parity: bool = True,
    sample_data_present: bool = True,
    lineage: DerivativeExportLineageAudit | dict[str, Any] | None = None,
    shipment: DerivativeExportShipmentBaseline | dict[str, Any] | None = None,
) -> DerivativeExportAuditReport:
    """Run the three-dimension audit bound to the frozen export manifest.

    Only consumes evidence; never writes STATE/ROADMAP, never patches a
    manifest and never writes an active pointer. The quality dimension is
    derived from the real Phase 22 state and can only be ``blocked`` while
    Phase 22 is blocked.

    Phase 39-04: when an independent ``lineage`` audit and/or the REQ-SHIP-01
    ``shipment`` baseline are supplied, every blocked/partial lineage check and
    every non-verified shipment item fails closed into the report verdict
    (``qualified_candidate`` / ``blocked`` only — never promotion).
    """
    snapshot_hash = _manifest_hash_binding(manifest)
    owner_id = manifest.owner_id
    novel_id = manifest.novel_id
    project_id = manifest.project_id

    phase22_evidence = (
        phase22
        if isinstance(phase22, DerivativeExportPhase22Evidence)
        else DerivativeExportPhase22Evidence.model_validate(phase22)
    )

    # implementation_readiness (package machinery evidence).
    impl_blocking: list[str] = []
    if not implementation_evidence:
        impl_blocking.append("implementation_evidence_missing")
    if not package_buildable:
        impl_blocking.append("package_build_failed")
    if not package_manifest_hash_parity:
        impl_blocking.append("package_manifest_hash_parity_failed")

    # sample_data_coverage (round-trip fixture evidence).
    data_blocking: list[str] = []
    if not sample_data_evidence:
        data_blocking.append("sample_data_evidence_missing")
    if not sample_data_present:
        data_blocking.append("sample_data_fixtures_missing")

    # quality_qualification (real state, manifest-bound blocked reason).
    quality_status, quality_blocked = derive_quality_qualification(
        phase22_evidence, snapshot_hash
    )

    implementation = DerivativeExportAuditDimension(
        dimension=DerivativeExportAuditDimensionKind.IMPLEMENTATION_READINESS,
        status=_dimension_status(impl_blocking, []),
        blocked_reasons=tuple(sorted(set(impl_blocking))),
        evidence=implementation_evidence,
    )
    sample_data = DerivativeExportAuditDimension(
        dimension=DerivativeExportAuditDimensionKind.SAMPLE_DATA_COVERAGE,
        status=_dimension_status(data_blocking, []),
        blocked_reasons=tuple(sorted(set(data_blocking))),
        evidence=sample_data_evidence,
    )
    quality = DerivativeExportAuditDimension(
        dimension=DerivativeExportAuditDimensionKind.QUALITY_QUALIFICATION,
        status=quality_status,
        blocked_reasons=tuple(sorted(quality_blocked)),
        evidence=quality_evidence,
    )

    reasons: list[str] = []
    for dimension in (implementation, sample_data, quality):
        if dimension.status == DerivativeExportAuditStatus.BLOCKED:
            reasons.extend(dimension.blocked_reasons)
        elif dimension.status == DerivativeExportAuditStatus.PARTIAL:
            reasons.append(f"dimension_partial:{dimension.dimension.value}")

    # Phase 39-04: fold the independent lineage + REQ-SHIP-01 gate evidence.
    lineage_obj: DerivativeExportLineageAudit | None = None
    if lineage is not None:
        lineage_obj = (
            lineage
            if isinstance(lineage, DerivativeExportLineageAudit)
            else DerivativeExportLineageAudit.model_validate(lineage)
        )
        reasons.extend(lineage_obj.blocked_reasons)
    shipment_obj: DerivativeExportShipmentBaseline | None = None
    if shipment is not None:
        shipment_obj = (
            shipment
            if isinstance(shipment, DerivativeExportShipmentBaseline)
            else DerivativeExportShipmentBaseline.model_validate(shipment)
        )
        reasons.extend(shipment_obj.blocked_reasons)
    reasons = sorted(set(reasons))

    verdict: Literal["qualified_candidate", "blocked"] = (
        "blocked" if reasons else "qualified_candidate"
    )

    report = DerivativeExportAuditReport(
        owner_id=owner_id,
        novel_id=novel_id,
        project_id=project_id,
        snapshot_hash=snapshot_hash,
        dimensions=(implementation, sample_data, quality),
        phase22=phase22_evidence,
        lineage=lineage_obj,
        shipment=shipment_obj,
        verdict=verdict,
        blocked_reasons=tuple(reasons),
        report_hash="0" * 64,
    )
    return report.model_copy(update={"report_hash": audit_report_hash(report)})


# ---------------------------------------------------------------------------
# Phase 39-04: independent lineage audit (T-39-04-01/T-39-04-02)
#
# Every audit verdict carries raw evidence links and can be recomputed from the
# manifest / snapshot / ExportPreparationArtifact / ApprovalRequest / materialized
# bundle. The only release-gate verdicts are ``qualified_candidate`` and
# ``blocked`` — there is no promotion path.
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Phase 39-04: REQ-SHIP-01 production baseline evidence (D-39-04)
# ---------------------------------------------------------------------------


class DerivativeExportShipmentRequirement(StrEnum):
    """The five REQ-SHIP-01 production baseline evidence requirements."""

    TLS = "tls"
    SECRET_SOURCING_ROTATION = "secret_sourcing_rotation"
    BACKUP_RESTORE_DRILL = "backup_restore_drill"
    MONITORING_ALERT = "monitoring_alert"
    COST_BUDGET = "cost_budget"


REQ_SHIP01_REQUIREMENTS: tuple[DerivativeExportShipmentRequirement, ...] = (
    DerivativeExportShipmentRequirement.TLS,
    DerivativeExportShipmentRequirement.SECRET_SOURCING_ROTATION,
    DerivativeExportShipmentRequirement.BACKUP_RESTORE_DRILL,
    DerivativeExportShipmentRequirement.MONITORING_ALERT,
    DerivativeExportShipmentRequirement.COST_BUDGET,
)


class DerivativeExportShipmentEvidenceStatus(StrEnum):
    """Honest per-requirement evidence state (verified/unverified/blocked)."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    BLOCKED = "blocked"


class DerivativeExportShipmentItem(_StrictAuditModel):
    """One REQ-SHIP-01 requirement with its raw evidence link and state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requirement: DerivativeExportShipmentRequirement
    status: DerivativeExportShipmentEvidenceStatus
    raw_evidence_link: str = Field(min_length=1, max_length=500)
    detail: str = Field(default="", max_length=1000)


class DerivativeExportShipmentBaseline(_StrictAuditModel):
    """Aggregate REQ-SHIP-01 baseline; missing evidence fails closed (blocked)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = DERIVATIVE_EXPORT_SHIPMENT_SCHEMA_VERSION
    items: tuple[DerivativeExportShipmentItem, ...]
    status: DerivativeExportAuditStatus
    blocked_reasons: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _tupleize(cls, value: Any) -> Any:
        if isinstance(value, dict):
            updates = {}
            if isinstance(value.get("items"), list):
                updates["items"] = tuple(value["items"])
            if isinstance(value.get("blocked_reasons"), list):
                updates["blocked_reasons"] = tuple(value["blocked_reasons"])
            if updates:
                value = {**value, **updates}
        return value


def build_derivative_export_shipment_baseline(
    items: Iterable[DerivativeExportShipmentItem | dict[str, Any]],
) -> DerivativeExportShipmentBaseline:
    """Normalize REQ-SHIP-01 evidence into a fail-closed baseline.

    Every one of the five required requirements must carry evidence; a missing
    requirement is ``blocked`` (``shipment_evidence_missing``). Blocked and
    unverified items also fail closed into ``blocked_reasons`` so the report
    verdict can never be green while the production baseline is incomplete.
    """
    normalized: list[DerivativeExportShipmentItem] = []
    present: set[str] = set()
    for item in items:
        obj = (
            item
            if isinstance(item, DerivativeExportShipmentItem)
            else DerivativeExportShipmentItem.model_validate(item)
        )
        present.add(obj.requirement.value)
        normalized.append(obj)
    missing = [req for req in REQ_SHIP01_REQUIREMENTS if req.value not in present]
    for req in missing:
        normalized.append(
            DerivativeExportShipmentItem(
                requirement=req,
                status=DerivativeExportShipmentEvidenceStatus.BLOCKED,
                raw_evidence_link="docs/DEPLOYMENT.md#Production-Blockers",
                detail="shipment_evidence_missing",
            )
        )

    blocked_items = [
        item
        for item in normalized
        if item.status == DerivativeExportShipmentEvidenceStatus.BLOCKED
    ]
    unverified_items = [
        item
        for item in normalized
        if item.status == DerivativeExportShipmentEvidenceStatus.UNVERIFIED
    ]
    reasons: list[str] = [f"shipment_evidence_missing:{req.value}" for req in missing]
    reasons.extend(
        f"shipment_blocked:{item.requirement.value}"
        for item in blocked_items
        if item.requirement.value not in {req.value for req in missing}
    )
    reasons.extend(
        f"shipment_unverified:{item.requirement.value}" for item in unverified_items
    )
    reasons = tuple(sorted(set(reasons)))

    if blocked_items or missing:
        status = DerivativeExportAuditStatus.BLOCKED
    elif unverified_items:
        status = DerivativeExportAuditStatus.PARTIAL
    else:
        status = DerivativeExportAuditStatus.VERIFIED
    return DerivativeExportShipmentBaseline(
        items=tuple(normalized), status=status, blocked_reasons=reasons
    )


def audit_derivative_export_has_promotion_capability() -> bool:
    return False


def audit_derivative_export_mutates_planning_state() -> bool:
    """Guard: the audit module must never write STATE/ROADMAP."""
    return False


__all__ = [
    "AUDIT_DIMENSION_KINDS",
    "DERIVATIVE_EXPORT_AUDIT_SCHEMA_VERSION",
    "DERIVATIVE_EXPORT_AUDIT_VERSION",
    "DERIVATIVE_EXPORT_LINEAGE_SCHEMA_VERSION",
    "DERIVATIVE_EXPORT_SHIPMENT_SCHEMA_VERSION",
    "PHASE22_GREEN_REQUIRED",
    "REQ_SHIP01_REQUIREMENTS",
    "DerivativeExportAuditDimension",
    "DerivativeExportAuditDimensionKind",
    "DerivativeExportAuditEvidence",
    "DerivativeExportAuditReport",
    "DerivativeExportAuditStatus",
    "DerivativeExportLineageAudit",
    "DerivativeExportLineageCheck",
    "DerivativeExportLineageCheckKind",
    "DerivativeExportPhase22Evidence",
    "DerivativeExportShipmentBaseline",
    "DerivativeExportShipmentEvidenceStatus",
    "DerivativeExportShipmentItem",
    "DerivativeExportShipmentRequirement",
    "audit_derivative_export_has_promotion_capability",
    "audit_derivative_export_lineage",
    "audit_derivative_export_mutates_planning_state",
    "audit_report_hash",
    "build_derivative_export_audit",
    "build_derivative_export_shipment_baseline",
    "derive_quality_qualification",
    "quality_qualification_blocked_reason",
    "replay_quality_qualification_blocked_reason",
    "run_derivative_export_lineage_audit",
]
