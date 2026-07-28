from __future__ import annotations

from pathlib import Path

import pytest

from app.services.narrative_memory.audit import audit_assets, provider_calls_allowed
from app.services.narrative_memory.audit_contracts import (
    AssetInventory,
    AssetKind,
    EligibilityStatus,
    ReasonCode,
    RebuildRange,
)
from app.services.narrative_memory.audit_sources import InMemoryAssetInventorySource

pytestmark = pytest.mark.unit


def _inventory(kind: AssetKind, **changes: object) -> AssetInventory:
    payload: dict[str, object] = {
        "kind": kind,
        "owner_id": 1,
        "novel_id": 2,
        "version_id": f"{kind.value}-v1",
        "item_count": 1,
    }
    payload.update(changes)
    return AssetInventory.model_validate(payload)


@pytest.mark.asyncio
async def test_exact_hierarchy_allows_provider_and_optional_empty_is_healthy() -> None:
    source = InMemoryAssetInventorySource(
        [
            _inventory(AssetKind.HIERARCHY),
            _inventory(AssetKind.TIMELINE, item_count=0, healthy_empty=True),
            _inventory(AssetKind.RELATIONSHIP),
            _inventory(AssetKind.CLUE),
        ]
    )
    report = await audit_assets(source, owner_id=1, novel_id=2)
    assert provider_calls_allowed(report) is True
    timeline = next(item for item in report.assets if item.kind == AssetKind.TIMELINE)
    assert timeline.status == EligibilityStatus.REUSABLE_EXACT
    assert timeline.healthy_empty is True


@pytest.mark.asyncio
async def test_required_missing_blocks_but_optional_missing_is_unavailable() -> None:
    report = await audit_assets(
        InMemoryAssetInventorySource([]), owner_id=1, novel_id=2
    )
    statuses = {item.kind: item.status for item in report.assets}
    assert statuses[AssetKind.HIERARCHY] == EligibilityStatus.BLOCKED
    assert statuses[AssetKind.TIMELINE] == EligibilityStatus.OPTIONAL_UNAVAILABLE
    assert statuses[AssetKind.RELATIONSHIP] == EligibilityStatus.OPTIONAL_UNAVAILABLE
    assert statuses[AssetKind.CLUE] == EligibilityStatus.OPTIONAL_UNAVAILABLE
    assert provider_calls_allowed(report) is False


@pytest.mark.asyncio
async def test_stale_hierarchy_requires_bounded_rebuild() -> None:
    source = InMemoryAssetInventorySource(
        [
            _inventory(
                AssetKind.HIERARCHY,
                reason_codes=(ReasonCode.CONTENT_HASH_MISMATCH,),
                rebuild_ranges=(RebuildRange(start_chapter=7, end_chapter=7),),
            )
        ]
    )
    report = await audit_assets(source, owner_id=1, novel_id=2)
    hierarchy = next(item for item in report.assets if item.kind == AssetKind.HIERARCHY)
    assert hierarchy.status == EligibilityStatus.REBUILD_REQUIRED
    assert hierarchy.rebuild_ranges[0].start_chapter == 7
    assert provider_calls_allowed(report) is False


@pytest.mark.asyncio
async def test_scope_mismatch_fails_closed_without_copying_foreign_scope() -> None:
    report = await audit_assets(
        InMemoryAssetInventorySource([_inventory(AssetKind.HIERARCHY, owner_id=99)]),
        owner_id=1,
        novel_id=2,
    )
    hierarchy = next(item for item in report.assets if item.kind == AssetKind.HIERARCHY)
    assert hierarchy.status == EligibilityStatus.BLOCKED
    assert ReasonCode.OWNER_SCOPE_MISMATCH in hierarchy.reason_codes
    assert hierarchy.owner_id == 1


@pytest.mark.asyncio
async def test_duplicate_inventory_is_rejected() -> None:
    source = InMemoryAssetInventorySource(
        [_inventory(AssetKind.HIERARCHY), _inventory(AssetKind.HIERARCHY)]
    )
    with pytest.raises(ValueError, match="duplicate inventory"):
        await audit_assets(source, owner_id=1, novel_id=2)


def test_package_has_no_mutation_or_provider_capabilities() -> None:
    root = Path(__file__).parents[3] / "app" / "services" / "narrative_memory"
    pure_files = ("__init__.py", "audit.py", "audit_contracts.py", "audit_sources.py")
    pure_text = "\n".join(
        (root / name).read_text(encoding="utf-8") for name in pure_files
    ).lower()
    pure_forbidden = (
        "sqlalchemy",
        "model_gateway",
        "litellm",
        "set_active_pointer",
        "dispatch_job",
        "session.add",
        "session.execute(update",
        "session.execute(delete",
    )
    assert not [token for token in pure_forbidden if token in pure_text]

    pg_path = root / "audit_pg.py"
    if pg_path.exists():
        pg_text = pg_path.read_text(encoding="utf-8").lower()
        pg_forbidden = (
            "model_gateway",
            "litellm",
            "from app.services.chunking.promotion",
            "set_active_pointer",
            "self._session.add(",
            "self._session.delete(",
            "self._session.commit(",
            "self._session.flush(",
            "from sqlalchemy import update",
            "from sqlalchemy import insert",
        )
        assert not [token for token in pg_forbidden if token in pg_text]
