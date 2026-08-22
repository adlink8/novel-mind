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

Phase 39-04 contracts live in sibling modules (no circular import):

- ``audit_lineage``: the independent lineage audit (pure recompute +
  DB-backed runner).
- ``audit_shipment``: the REQ-SHIP-01 production baseline evidence (and the
  shared audit primitives it owns).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from app.services.derivative_export.audit_lineage import (
    DERIVATIVE_EXPORT_LINEAGE_SCHEMA_VERSION,
    DerivativeExportLineageAudit,
    DerivativeExportLineageCheck,
    DerivativeExportLineageCheckKind,
    audit_derivative_export_lineage,
    run_derivative_export_lineage_audit,
)
from app.services.derivative_export.audit_shipment import (
    DERIVATIVE_EXPORT_SHIPMENT_SCHEMA_VERSION,
    REQ_SHIP01_REQUIREMENTS,
    DerivativeExportAuditStatus,
    DerivativeExportShipmentBaseline,
    DerivativeExportShipmentEvidenceStatus,
    DerivativeExportShipmentItem,
    DerivativeExportShipmentRequirement,
    _StrictAuditModel,
    _dimension_status,
    build_derivative_export_shipment_baseline,
)
from app.services.derivative_export.manifest import (
    DERIVATIVE_EXPORT_SCHEMA_VERSION,
    DerivativeExportManifest,
    canonical_export_hash,
)
from app.services.derivative_export.snapshot import ExportSnapshot

DERIVATIVE_EXPORT_AUDIT_SCHEMA_VERSION = "derivative-export-audit.v1"
DERIVATIVE_EXPORT_AUDIT_VERSION = "derivative-export-audit.v1"

PHASE22_GREEN_REQUIRED = 3

# Vocabulary that must never appear in audit blocked reasons (no promotion).
FORBIDDEN_AUDIT_WORDS = frozenset(
    {"promote", "promoted", "promotion", "active_pointer", "production_ready"}
)


class DerivativeExportAuditDimensionKind(StrEnum):
    IMPLEMENTATION_READINESS = "implementation_readiness"
    SAMPLE_DATA_COVERAGE = "sample_data_coverage"
    QUALITY_QUALIFICATION = "quality_qualification"


AUDIT_DIMENSION_KINDS: tuple[DerivativeExportAuditDimensionKind, ...] = (
    DerivativeExportAuditDimensionKind.IMPLEMENTATION_READINESS,
    DerivativeExportAuditDimensionKind.SAMPLE_DATA_COVERAGE,
    DerivativeExportAuditDimensionKind.QUALITY_QUALIFICATION,
)


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
