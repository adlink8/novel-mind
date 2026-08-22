"""Owner/novel/version/cutoff query API with evidence, lineage and conflicts.

Phase 27-01 / REQ-WM-01. The query layer is read-only: it never writes, never
promotes, and never resolves a conflict by overwrite. D-05 disclosure cutoff is
applied here: a reader only sees events/edges whose ``disclosure_cutoff`` is at
or before the requested cutoff, and only the conflicts touching visible rows.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.world_model_event import (
    WorldModelCausalEdge,
    WorldModelConflict,
    WorldModelEvent,
)
from app.services.world_model.contracts import (
    CausalEdge,
    EventFact,
    WorldModelCandidateProjection,
    WorldModelConflict as ConflictContract,
    build_projection,
)


class WorldModelQueryError(ValueError):
    pass


class WorldModelEventQueries:
    """Read-only, owner-scoped, cutoff-aware query API."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def query_cutoff_projection(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        cutoff: int,
    ) -> WorldModelCandidateProjection | None:
        """Return the immutable candidate filtered to the disclosure cutoff.

        Returns ``None`` when no projection exists in the owner scope (fail
        closed); hidden events/edges are never returned, and no raw row is
        leaked across the cutoff (D-05).
        """
        event_rows = (
            await self._session.scalars(
                select(WorldModelEvent)
                .where(
                    WorldModelEvent.owner_id == owner_id,
                    WorldModelEvent.novel_id == novel_id,
                    WorldModelEvent.version_id == version_id,
                    WorldModelEvent.disclosure_cutoff <= cutoff,
                )
                .order_by(WorldModelEvent.id.asc())
            )
        ).all()
        if not event_rows:
            return None

        visible_keys = {row.event_key for row in event_rows}
        edge_rows = (
            await self._session.scalars(
                select(WorldModelCausalEdge)
                .where(
                    WorldModelCausalEdge.owner_id == owner_id,
                    WorldModelCausalEdge.novel_id == novel_id,
                    WorldModelCausalEdge.version_id == version_id,
                    WorldModelCausalEdge.disclosure_cutoff <= cutoff,
                )
                .order_by(WorldModelCausalEdge.id.asc())
            )
        ).all()
        conflict_rows = (
            await self._session.scalars(
                select(WorldModelConflict)
                .where(
                    WorldModelConflict.owner_id == owner_id,
                    WorldModelConflict.novel_id == novel_id,
                    WorldModelConflict.version_id == version_id,
                )
                .order_by(WorldModelConflict.id.asc())
            )
        ).all()

        events = [EventFact.model_validate(row.canonical_payload) for row in event_rows]
        edges: list[CausalEdge] = []
        for row in edge_rows:
            edge = CausalEdge.model_validate(row.canonical_payload)
            if (
                edge.source_event_key in visible_keys
                and edge.target_event_key in visible_keys
            ):
                edges.append(edge)
        conflicts: list[ConflictContract] = []
        for row in conflict_rows:
            conflict = ConflictContract.model_validate(row.canonical_payload)
            if set(conflict.involved_keys) <= visible_keys:
                conflicts.append(conflict)

        return build_projection(
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            events=events,
            edges=edges,
            conflicts=conflicts,
        )

    async def query_event_lineage(
        self, *, owner_id: int, novel_id: int, event_key: str
    ) -> tuple[EventFact, ...]:
        """Full version lineage of one event key (D-03), oldest first."""
        rows = (
            await self._session.scalars(
                select(WorldModelEvent)
                .where(
                    WorldModelEvent.owner_id == owner_id,
                    WorldModelEvent.novel_id == novel_id,
                    WorldModelEvent.event_key == event_key,
                )
                .order_by(WorldModelEvent.version_id.asc(), WorldModelEvent.id.asc())
            )
        ).all()
        return tuple(EventFact.model_validate(row.canonical_payload) for row in rows)

    async def query_conflicts(
        self, *, owner_id: int, novel_id: int, version_id: int
    ) -> tuple[ConflictContract, ...]:
        """All preserved conflicts for one version; never resolved by overwrite."""
        rows = (
            await self._session.scalars(
                select(WorldModelConflict)
                .where(
                    WorldModelConflict.owner_id == owner_id,
                    WorldModelConflict.novel_id == novel_id,
                    WorldModelConflict.version_id == version_id,
                )
                .order_by(WorldModelConflict.id.asc())
            )
        ).all()
        return tuple(
            ConflictContract.model_validate(row.canonical_payload) for row in rows
        )
