from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.narrative_memory.audit_contracts import (
    AssetEligibility,
    AssetInventory,
    AssetKind,
    AssetRequirement,
    EligibilityReport,
    EligibilityStatus,
    ReasonCode,
    RebuildRange,
)

pytestmark = pytest.mark.unit


def _result(kind: AssetKind) -> AssetEligibility:
    return AssetEligibility(
        kind=kind,
        requirement=(
            AssetRequirement.REQUIRED
            if kind == AssetKind.HIERARCHY
            else AssetRequirement.OPTIONAL
        ),
        owner_id=1,
        novel_id=2,
        status=EligibilityStatus.REUSABLE_EXACT,
    )


def test_inventory_is_strict_and_canonical() -> None:
    inventory = AssetInventory(
        kind=AssetKind.HIERARCHY,
        owner_id=1,
        novel_id=2,
        reason_codes=(ReasonCode.STALE_ASSET, ReasonCode.MANIFEST_MISMATCH),
        rebuild_ranges=(
            RebuildRange(start_chapter=4, end_chapter=5),
            RebuildRange(start_chapter=1, end_chapter=1),
        ),
    )
    assert inventory.reason_codes == (
        ReasonCode.MANIFEST_MISMATCH,
        ReasonCode.STALE_ASSET,
    )
    assert [item.start_chapter for item in inventory.rebuild_ranges] == [1, 4]
    with pytest.raises(ValidationError):
        AssetInventory(kind="hierarchy", owner_id=1, novel_id=2, unexpected=True)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        RebuildRange(start_chapter=3, end_chapter=2)
    assert RebuildRange(start_chapter=0, end_chapter=0).start_chapter == 0


def test_report_requires_all_unique_assets_and_matching_scope() -> None:
    assets = tuple(_result(kind) for kind in reversed(list(AssetKind)))
    report = EligibilityReport(owner_id=1, novel_id=2, assets=assets)
    assert [item.kind.value for item in report.assets] == sorted(
        kind.value for kind in AssetKind
    )
    assert report.provider_calls_allowed is True
    with pytest.raises(ValidationError):
        EligibilityReport(owner_id=1, novel_id=2, assets=assets[:-1])
    with pytest.raises(ValidationError):
        EligibilityReport(owner_id=1, novel_id=2, assets=assets + (assets[0],))
    forged = list(assets)
    hierarchy_index = next(
        index for index, item in enumerate(forged) if item.kind == AssetKind.HIERARCHY
    )
    forged[hierarchy_index] = forged[hierarchy_index].model_copy(
        update={"requirement": AssetRequirement.OPTIONAL}
    )
    with pytest.raises(ValidationError):
        EligibilityReport(owner_id=1, novel_id=2, assets=tuple(forged))


def test_model_dump_is_stable_for_logically_identical_input() -> None:
    first = EligibilityReport(
        owner_id=1, novel_id=2, assets=tuple(_result(kind) for kind in AssetKind)
    )
    second = EligibilityReport(
        owner_id=1,
        novel_id=2,
        assets=tuple(_result(kind) for kind in reversed(list(AssetKind))),
    )
    assert first.model_dump_json() == second.model_dump_json()
    assert EligibilityReport.model_validate_json(first.model_dump_json()) == first
    with pytest.raises(ValidationError):
        EligibilityReport.model_validate(
            {**first.model_dump(), "provider_calls_allowed": False}
        )
