"""Facet production chain entry (Phase 35-04, REQ-CRE-02 / D-35-02).

The codebase has no standalone facet producer service yet (Phase 37-38 scope);
this module is the *minimum* write entry point for the facet production chain,
wired to the shared derivative-write guard adapter so a derivative space can
never enter facet production.  Deterministic release of facet output is a later
phase; this entry exists so the negative contamination gate has a real, shared
adapter-backed write path to attack.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.canon_fork.contamination import (
    ORIGINAL_CANON,
    facet_producer_guard,
)


class FacetProducer:
    """Facet production write entry bound to the shared facet guard."""

    async def produce(
        self,
        db: AsyncSession,
        *,
        write: Callable[[AsyncSession], Awaitable[Any]],
        space: str = ORIGINAL_CANON,
        owner_id: int | None = None,
        novel_id: int | None = None,
        scope=None,
        attempt_hash: str | None = None,
    ) -> Any:
        """Run one facet-production write under the shared guard.

        A derivative ``space`` is rejected before any facet IO, the failed
        transaction is rolled back and the blocked reason is preserved.
        """
        return await facet_producer_guard.guard_write(
            db,
            write=write,
            space=space,
            owner_id=owner_id,
            novel_id=novel_id,
            scope=scope,
            attempt_hash=attempt_hash,
        )


facet_producer = FacetProducer()

__all__ = ["FacetProducer", "facet_producer"]
