"""REQ-SHIP-01 production baseline evidence (Phase 39-04, D-39-04).

The five-requirement shipment baseline is the independent release-gate evidence
that ships with a derivative export deliverable; every requirement must carry
evidence and missing evidence fails closed into a ``blocked`` baseline.

The shared audit primitives (``_StrictAuditModel``, ``DerivativeExportAuditStatus``
and ``_dimension_status``) also live here so that ``audit_lineage`` and the core
``audit`` module can import them without a circular dependency.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict, Field, model_validator

DERIVATIVE_EXPORT_SHIPMENT_SCHEMA_VERSION = "derivative-export-shipment-baseline.v1"


class _StrictAuditModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DerivativeExportAuditStatus(StrEnum):
    VERIFIED = "verified"
    PARTIAL = "partial"
    BLOCKED = "blocked"


def _dimension_status(
    blocking: list[str], partial: list[str]
) -> DerivativeExportAuditStatus:
    if blocking:
        return DerivativeExportAuditStatus.BLOCKED
    if partial:
        return DerivativeExportAuditStatus.PARTIAL
    return DerivativeExportAuditStatus.VERIFIED


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


__all__ = [
    "DERIVATIVE_EXPORT_SHIPMENT_SCHEMA_VERSION",
    "DerivativeExportAuditStatus",
    "REQ_SHIP01_REQUIREMENTS",
    "DerivativeExportShipmentBaseline",
    "DerivativeExportShipmentEvidenceStatus",
    "DerivativeExportShipmentItem",
    "DerivativeExportShipmentRequirement",
    "build_derivative_export_shipment_baseline",
]
