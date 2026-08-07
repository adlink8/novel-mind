"""Append-only durable repository for epistemic history projections.

Phase 27-02 / REQ-WM-02. Semantics (D-02, D-05):

- ``append_projection`` persists one immutable ``KnowledgeCandidateProjection``
  after the deterministic gate. A unique ``idempotency_key`` conflict only
  replays the existing row; it never creates a second row.
- ``replay_projection`` reconstructs the projection from rows, recomputes every
  canonical checksum and the sealed ``projection_hash``, and fails closed on
  byte drift (contradiction-preserving restart replay proof).
- No UPDATE / DELETE / promote path exists. Cross-owner reads fail closed.
- Stale-version writes (older than the newest stored version) are rejected so
  the version lineage stays append-only and monotonic.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.world_model_knowledge import WorldModelKnowledge
from app.services.world_model.contracts import GateStatus
from app.services.world_model.knowledge import (
    EPISTEMIC_SCHEMA_VERSION,
    EpistemicClaim,
    KnowledgeCandidateProjection,
    claim_checksum,
    projection_checksum,
)

WORLD_MODEL_KNOWLEDGE_TABLES = (WorldModelKnowledge,)


class KnowledgeRepositoryError(ValueError):
    pass


class KnowledgeRepository:
    """Append-only repository. Reads are owner-scoped and checksum-verified."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ append

    async def append_projection(self, projection: KnowledgeCandidateProjection) -> None:
        """Persist one immutable projection; idempotent on key conflicts."""

        if projection.projection_hash != projection_checksum(projection):
            raise KnowledgeRepositoryError("projection hash is not sealed")

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
    ) -> KnowledgeCandidateProjection:
        """Reconstruct the immutable projection; fail closed on checksum drift."""

        rows = (
            await self._session.scalars(
                select(WorldModelKnowledge)
                .where(
                    WorldModelKnowledge.owner_id == owner_id,
                    WorldModelKnowledge.novel_id == novel_id,
                    WorldModelKnowledge.version_id == version_id,
                )
                .order_by(WorldModelKnowledge.id.asc())
            )
        ).all()
        if not rows:
            raise KnowledgeRepositoryError(
                "projection not found in owner/novel/version scope"
            )

        stored_hash = rows[0].projection_hash
        if any(row.projection_hash != stored_hash for row in rows):
            raise KnowledgeRepositoryError(
                "projection rows disagree on the sealed projection hash"
            )

        claims = [EpistemicClaim.model_validate(row.canonical_payload) for row in rows]
        for row, claim in zip(rows, claims):
            if row.canonical_payload_hash != claim_checksum(claim):
                raise KnowledgeRepositoryError("claim checksum drift on replay")
            if claim.gate_status != GateStatus.PASSED:
                raise KnowledgeRepositoryError(
                    "replayed claim is not a gate-passed candidate"
                )

        rebuilt = KnowledgeCandidateProjection(
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
            schema_version=rows[0].schema_version,
            claims=tuple(claims),
            projection_hash=stored_hash,
        )
        if projection_checksum(rebuilt) != stored_hash:
            raise KnowledgeRepositoryError("projection checksum drift on replay")
        return rebuilt

    async def list_versions(self, *, owner_id: int, novel_id: int) -> list[int]:
        """Ascending version lineage for one owner/novel scope."""
        rows = (
            await self._session.scalars(
                select(WorldModelKnowledge.version_id)
                .where(
                    WorldModelKnowledge.owner_id == owner_id,
                    WorldModelKnowledge.novel_id == novel_id,
                )
                .distinct()
                .order_by(WorldModelKnowledge.version_id.asc())
            )
        ).all()
        return list(rows)

    # ---------------------------------------------------------------- helpers

    async def _assert_version_not_stale(
        self, projection: KnowledgeCandidateProjection
    ) -> None:
        max_version = await self._session.scalar(
            select(func.max(WorldModelKnowledge.version_id)).where(
                WorldModelKnowledge.owner_id == projection.owner_id,
                WorldModelKnowledge.novel_id == projection.novel_id,
            )
        )
        if max_version is not None and projection.version_id < int(max_version):
            raise KnowledgeRepositoryError(
                f"stale-version write rejected: newest version "
                f"is {max_version}, tried {projection.version_id}"
            )

    def _to_rows(
        self, projection: KnowledgeCandidateProjection
    ) -> list[WorldModelKnowledge]:
        rows: list[WorldModelKnowledge] = []
        for claim in projection.claims:
            rows.append(
                WorldModelKnowledge(
                    knowledge_key=claim.knowledge_key,
                    subject=claim.subject,
                    aspect=claim.aspect.value,
                    known_at=claim.known_at,
                    disclosure_cutoff=claim.disclosure_cutoff,
                    pov=claim.pov,
                    pov_kind=claim.pov_kind.value,
                    source_kind=claim.source_kind.value,
                    authority=claim.authority.value,
                    confidence=claim.confidence,
                    epistemic_status=claim.epistemic_status.value,
                    transition_from=claim.transition_from,
                    lineage=list(claim.lineage),
                    source_refs=[
                        ref.model_dump(mode="json") for ref in claim.source_refs
                    ],
                    gate_status=claim.gate_status.value,
                    owner_id=claim.owner_id,
                    novel_id=claim.novel_id,
                    version_id=claim.version_id,
                    canonical_payload=claim.model_dump(mode="json"),
                    canonical_payload_hash=claim.checksum,
                    idempotency_key=claim.idempotency_key,
                    projection_hash=projection.projection_hash,
                    schema_version=projection.schema_version,
                )
            )
        return rows

    async def _replay_existing_or_fail(
        self, projection: KnowledgeCandidateProjection
    ) -> None:
        """After a unique-key race, replay the winner instead of duplicating."""
        replayed = await self.replay_projection(
            owner_id=projection.owner_id,
            novel_id=projection.novel_id,
            version_id=projection.version_id,
        )
        if replayed.idempotency_key != projection.idempotency_key:
            raise KnowledgeRepositoryError(
                "idempotent replay race: winner differs from this projection"
            )


# Re-export the schema version for convenience (the projection stores it per row).
def projection_schema_version(row: WorldModelKnowledge) -> str:
    return row.schema_version or EPISTEMIC_SCHEMA_VERSION
