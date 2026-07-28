"""Pure eligibility policy. This module intentionally has no database or provider imports."""

from __future__ import annotations

from app.services.narrative_memory.audit_contracts import (
    AssetEligibility,
    AssetInventory,
    AssetKind,
    AssetRequirement,
    EligibilityReport,
    EligibilityStatus,
    ReasonCode,
)
from app.services.narrative_memory.audit_sources import AssetInventorySource

_REQUIREMENTS = {
    AssetKind.HIERARCHY: AssetRequirement.REQUIRED,
    AssetKind.TIMELINE: AssetRequirement.OPTIONAL,
    AssetKind.RELATIONSHIP: AssetRequirement.OPTIONAL,
    AssetKind.CLUE: AssetRequirement.OPTIONAL,
}

_BLOCKING_REASONS = {
    ReasonCode.SOURCE_MISSING,
    ReasonCode.OWNER_SCOPE_MISMATCH,
    ReasonCode.NOVEL_SCOPE_MISMATCH,
    ReasonCode.ACTIVE_VERSION_MISSING,
    ReasonCode.MALFORMED_HIERARCHY,
}


def _evaluate(
    item: AssetInventory, *, owner_id: int, novel_id: int
) -> AssetEligibility:
    requirement = _REQUIREMENTS[item.kind]
    reasons = set(item.reason_codes)
    if item.owner_id != owner_id:
        reasons.add(ReasonCode.OWNER_SCOPE_MISMATCH)
    if item.novel_id != novel_id:
        reasons.add(ReasonCode.NOVEL_SCOPE_MISMATCH)

    if requirement == AssetRequirement.OPTIONAL and (
        not item.available
        or ReasonCode.SOURCE_UNAVAILABLE in reasons
        or ReasonCode.SOURCE_MISSING in reasons
        or ReasonCode.OPTIONAL_LINEAGE_MISMATCH in reasons
    ):
        status = EligibilityStatus.OPTIONAL_UNAVAILABLE
    elif requirement == AssetRequirement.REQUIRED and (
        not item.available or bool(reasons & _BLOCKING_REASONS)
    ):
        status = EligibilityStatus.BLOCKED
    elif reasons:
        status = EligibilityStatus.REBUILD_REQUIRED
    else:
        status = EligibilityStatus.REUSABLE_EXACT

    return AssetEligibility(
        kind=item.kind,
        requirement=requirement,
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=item.version_id,
        status=status,
        reason_codes=tuple(sorted(reasons, key=str)),
        rebuild_ranges=item.rebuild_ranges,
        item_count=item.item_count,
        healthy_empty=item.healthy_empty,
    )


async def audit_assets(
    source: AssetInventorySource, *, owner_id: int, novel_id: int
) -> EligibilityReport:
    observed = await source.inventory(owner_id=owner_id, novel_id=novel_id)
    by_kind: dict[AssetKind, AssetInventory] = {}
    for item in observed:
        if item.kind in by_kind:
            raise ValueError(f"duplicate inventory for {item.kind.value}")
        by_kind[item.kind] = item

    for kind in AssetKind:
        if kind not in by_kind:
            by_kind[kind] = AssetInventory(
                kind=kind,
                owner_id=owner_id,
                novel_id=novel_id,
                available=False,
                reason_codes=(ReasonCode.SOURCE_MISSING,),
            )

    return EligibilityReport(
        owner_id=owner_id,
        novel_id=novel_id,
        assets=tuple(
            _evaluate(by_kind[kind], owner_id=owner_id, novel_id=novel_id)
            for kind in AssetKind
        ),
    )


def provider_calls_allowed(report: EligibilityReport) -> bool:
    """Explicit guard for later provider boundaries; caller flags are never trusted."""

    return report.provider_calls_allowed
