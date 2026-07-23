"""Read-only source seams for asset eligibility audits."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from app.services.narrative_memory.audit_contracts import AssetInventory


@runtime_checkable
class AssetInventorySource(Protocol):
    async def inventory(
        self, *, owner_id: int, novel_id: int
    ) -> tuple[AssetInventory, ...]:
        """Observe assets without repairing or mutating their authority state."""
        ...


class InMemoryAssetInventorySource:
    """Deterministic read-only adapter used by unit tests and dry contracts."""

    def __init__(self, items: Iterable[AssetInventory]) -> None:
        self._items = tuple(items)

    async def inventory(
        self, *, owner_id: int, novel_id: int
    ) -> tuple[AssetInventory, ...]:
        del owner_id, novel_id
        return self._items
