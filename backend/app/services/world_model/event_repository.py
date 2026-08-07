"""Append-only durable repository for world-model event/causal projections.

Phase 27-01 / REQ-WM-01. Semantics (D-02, D-04):

- ``append_projection`` persists one immutable ``WorldModelCandidateProjection``
  after the deterministic gate. A unique ``idempotency_key`` conflict only
  replays the existing row; it never creates a second row.
- ``replay_projection`` reconstructs the projection from rows, recomputes every
  canonical checksum and the sealed ``projection_hash``, and fails closed on
  byte drift (restart replay proof).
- No UPDATE / DELETE / promote path exists. Cross-owner reads fail closed.
- Stale-version writes (older than the newest stored version) are rejected so
  version lineage stays append-only and monotonic.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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
    conflict_checksum,
    edge_checksum,
    event_checksum,
    projection_checksum,
)

WORLD_MODEL_TABLES = (WorldModelEvent, WorldModelCausalEdge, WorldModelConflict)


class WorldModelRepositoryError(ValueError):
    pass


class WorldModelEventRepository:
    """Append-only repository. Reads are owner-scoped and checksum-verified."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ append

    async def append_projection(
        self, projection: WorldModelCandidateProjection
    ) -> None:
        """Persist one immutable projection; idempotent on key conflicts."""

        if projection.projection_hash != projection_checksum(projection):
            raise WorldModelRepositoryError("projection hash is not sealed")

        await self._assert_version_not_stale(projection)
        for row in self._to_rows(projection):
            self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError:
            # A concurrent unique-key winner only replays identical rows.
            await self._session.rollback()
            await self._replay_existing_or_fail(projection)

    async def replay_projection(
        self, *, owner_id: int, novel_id: int, version_id: int
    ) -> WorldModelCandidateProjection:
        """Reconstruct the immutable projection; fail closed on checksum drift."""

        events = (
            await self._session.scalars(
                select(WorldModelEvent)
                .where(
                    WorldModelEvent.owner_id == owner_id,
                    WorldModelEvent.novel_id == novel_id,
                    WorldModelEvent.version_id == version_id,
                )
                .order_by(WorldModelEvent.id.asc())
            )
        ).all()
        if not events:
            raise WorldModelRepositoryError(
                "projection not found in owner/novel/version scope"
            )

        edges = (
            await self._session.scalars(
                select(WorldModelCausalEdge)
                .where(
                    WorldModelCausalEdge.owner_id == owner_id,
                    WorldModelCausalEdge.novel_id == novel_id,
                    WorldModelCausalEdge.version_id == version_id,
                )
                .order_by(WorldModelCausalEdge.id.asc())
            )
        ).all()
        conflicts = (
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

        stored_hash = events[0].projection_hash
        if any(
            row.projection_hash != stored_hash for row in (*events, *edges, *conflicts)
        ):
            raise WorldModelRepositoryError(
                "projection rows disagree on the sealed projection hash"
            )

        fact_rows = [EventFact.model_validate(row.canonical_payload) for row in events]
        edge_rows = [CausalEdge.model_validate(row.canonical_payload) for row in edges]
        conflict_rows = [
            ConflictContract.model_validate(row.canonical_payload) for row in conflicts
        ]
        for row, fact in zip(events, fact_rows):
            if row.canonical_payload_hash != event_checksum(fact):
                raise WorldModelRepositoryError("event checksum drift on replay")
        for row, edge in zip(edges, edge_rows):
            if row.canonical_payload_hash != edge_checksum(edge):
                raise WorldModelRepositoryError("edge checksum drift on replay")
        for row, conflict in zip(conflicts, conflict_rows):
            if row.canonical_payload_hash != conflict_checksum(conflict):
                raise WorldModelRepositoryError("conflict checksum drift on replay")

        rebuilt = WorldModelCandidateProjection(
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            schema_version=projection_schema_version(events[0]),
            events=tuple(fact_rows),
            edges=tuple(edge_rows),
            conflicts=tuple(conflict_rows),
            projection_hash=stored_hash,
        )
        if projection_checksum(rebuilt) != stored_hash:
            raise WorldModelRepositoryError("projection checksum drift on replay")
        return rebuilt

    async def list_versions(self, *, owner_id: int, novel_id: int) -> list[int]:
        """Ascending version lineage for one owner/novel scope."""
        rows = (
            await self._session.scalars(
                select(WorldModelEvent.version_id)
                .where(
                    WorldModelEvent.owner_id == owner_id,
                    WorldModelEvent.novel_id == novel_id,
                )
                .distinct()
                .order_by(WorldModelEvent.version_id.asc())
            )
        ).all()
        return list(rows)

    # ---------------------------------------------------------------- helpers

    async def _assert_version_not_stale(
        self, projection: WorldModelCandidateProjection
    ) -> None:
        max_version = await self._session.scalar(
            select(func.max(WorldModelEvent.version_id)).where(
                WorldModelEvent.owner_id == projection.owner_id,
                WorldModelEvent.novel_id == projection.novel_id,
            )
        )
        if max_version is not None and projection.version_id < int(max_version):
            raise WorldModelRepositoryError(
                f"stale-version write rejected: newest version "
                f"is {max_version}, tried {projection.version_id}"
            )

    def _to_rows(self, projection: WorldModelCandidateProjection) -> list[object]:
        rows: list[object] = []
        for event in projection.events:
            rows.append(
                WorldModelEvent(
                    event_key=event.event_key,
                    owner_id=event.owner_id,
                    novel_id=event.novel_id,
                    version_id=event.version_id,
                    authority=event.authority.value,
                    confidence=event.confidence,
                    effective_start=event.effective.start,
                    effective_end=event.effective.end,
                    disclosure_cutoff=event.disclosure_cutoff,
                    gate_status=event.gate_status.value,
                    source_refs=[
                        ref.model_dump(mode="json") for ref in event.source_refs
                    ],
                    canonical_payload=event.model_dump(mode="json"),
                    canonical_payload_hash=event.checksum,
                    idempotency_key=event.idempotency_key,
                    projection_hash=projection.projection_hash,
                    schema_version=projection.schema_version,
                )
            )
        for edge in projection.edges:
            rows.append(
                WorldModelCausalEdge(
                    edge_key=edge.edge_key,
                    owner_id=edge.owner_id,
                    novel_id=edge.novel_id,
                    version_id=edge.version_id,
                    source_event_key=edge.source_event_key,
                    target_event_key=edge.target_event_key,
                    edge_type=edge.edge_type.value,
                    authority=edge.authority.value,
                    confidence=edge.confidence,
                    disclosure_cutoff=edge.disclosure_cutoff,
                    gate_status=edge.gate_status.value,
                    source_refs=[
                        ref.model_dump(mode="json") for ref in edge.source_refs
                    ],
                    canonical_payload=edge.model_dump(mode="json"),
                    canonical_payload_hash=edge.checksum,
                    idempotency_key=edge.idempotency_key,
                    projection_hash=projection.projection_hash,
                    schema_version=projection.schema_version,
                )
            )
        for conflict in projection.conflicts:
            rows.append(
                WorldModelConflict(
                    conflict_key=conflict.conflict_key,
                    owner_id=projection.owner_id,
                    novel_id=projection.novel_id,
                    version_id=projection.version_id,
                    kind=conflict.kind.value,
                    involved_keys=list(conflict.involved_keys),
                    description=conflict.description,
                    canonical_payload=conflict.model_dump(mode="json"),
                    canonical_payload_hash=conflict.checksum,
                    idempotency_key=conflict.idempotency_key,
                    projection_hash=projection.projection_hash,
                    schema_version=projection.schema_version,
                )
            )
        return rows

    async def _replay_existing_or_fail(
        self, projection: WorldModelCandidateProjection
    ) -> None:
        """After a unique-key race, replay the winner instead of duplicating."""
        replayed = await self.replay_projection(
            owner_id=projection.owner_id,
            novel_id=projection.novel_id,
            version_id=projection.version_id,
        )
        if replayed.idempotency_key != projection.idempotency_key:
            raise WorldModelRepositoryError(
                "idempotent replay race: winner differs from this projection"
            )


def projection_schema_version(row: WorldModelEvent) -> str:
    return row.schema_version
