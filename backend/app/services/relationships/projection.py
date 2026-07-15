"""
Replayable Neo4j/projection boundary for accepted relationship observations.

D-02: PostgreSQL accepted observations are authoritative. Projection is optional,
one-way, and writes only RelationshipProjectionAudit checkpoints. Adapter failure
must never mutate observation status or acceptance.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.relationship import (
    RelationshipEvidenceLink,
    RelationshipObservation,
    RelationshipProjectionAudit,
)
from app.services.relationships.query import RelationshipGraphQueryService


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_canonical(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProjectionConfig:
    """Optional Neo4j target. Disabled by default."""

    enabled: bool = False
    uri: str | None = None
    username: str | None = None
    password: str | None = None
    target: str = "neo4j"


@dataclass(frozen=True)
class ProjectionReplayResult:
    status: str
    manifest_checksum: str
    observation_count: int
    audit_id: int | None = None
    reason: str | None = None
    checkpoint: dict[str, Any] | None = None


class ProjectionAdapter(Protocol):
    """One-way sink: receives manifest rows; never writes back to PostgreSQL facts."""

    def project(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """Return adapter checkpoint metadata or raise on hard failure."""
        ...


class DisabledProjectionAdapter:
    def project(self, manifest: dict[str, Any]) -> dict[str, Any]:
        return {
            "adapter": "disabled",
            "observation_count": len(manifest.get("observations") or []),
        }


class MissingNeo4jAdapter:
    """Enabled config without driver — transparent non-authoritative failure."""

    def project(self, manifest: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("neo4j_driver_not_configured")


class RelationshipProjectionService:
    """Build deterministic manifests and optionally replay to a projection adapter."""

    def __init__(self, query_service: RelationshipGraphQueryService | None = None) -> None:
        self._query = query_service or RelationshipGraphQueryService()

    async def build_manifest(
        self,
        session: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
    ) -> dict[str, Any]:
        """Deterministic accepted-observation manifest for one version."""

        observations = list(
            (
                await session.scalars(
                    select(RelationshipObservation)
                    .where(
                        RelationshipObservation.owner_id == owner_id,
                        RelationshipObservation.novel_id == novel_id,
                        RelationshipObservation.analysis_version_id == version_id,
                        RelationshipObservation.status == "accepted",
                    )
                    .order_by(RelationshipObservation.id)
                )
            ).all()
        )
        obs_ids = [row.id for row in observations]
        evidence_rows: list[RelationshipEvidenceLink] = []
        if obs_ids:
            evidence_rows = list(
                (
                    await session.scalars(
                        select(RelationshipEvidenceLink)
                        .where(RelationshipEvidenceLink.observation_id.in_(obs_ids))
                        .order_by(
                            RelationshipEvidenceLink.observation_id,
                            RelationshipEvidenceLink.sort_order,
                            RelationshipEvidenceLink.id,
                        )
                    )
                ).all()
            )
        evidence_by_obs: dict[int, list[dict[str, Any]]] = {}
        for er in evidence_rows:
            evidence_by_obs.setdefault(er.observation_id, []).append(
                {
                    "evidence_id": er.evidence_id,
                    "chapter_id": er.chapter_id,
                    "source_start": er.source_start,
                    "source_end": er.source_end,
                    "content_hash": er.content_hash,
                }
            )

        entries = []
        for obs in observations:
            entries.append(
                {
                    "observation_id": obs.id,
                    "source_character_id": obs.source_character_id,
                    "target_character_id": obs.target_character_id,
                    "relation_type": obs.relation_type,
                    "transition": obs.transition,
                    "valid_from_chapter": obs.valid_from_chapter,
                    "valid_from_narrative_index": obs.valid_from_narrative_index,
                    "valid_to_chapter": obs.valid_to_chapter,
                    "valid_to_narrative_index": obs.valid_to_narrative_index,
                    "confidence": float(obs.confidence),
                    "evidence_checksum": obs.evidence_checksum,
                    "observation_checksum": obs.observation_checksum,
                    "idempotency_key": obs.idempotency_key,
                    "evidence": evidence_by_obs.get(obs.id, []),
                }
            )

        body = {
            "schema_version": "relationship-projection-manifest.v1",
            "owner_id": owner_id,
            "novel_id": novel_id,
            "analysis_version_id": version_id,
            "observations": entries,
        }
        checksum = sha256_canonical(body)
        return {**body, "manifest_checksum": checksum}

    async def replay_accepted_observations(
        self,
        session: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        config: ProjectionConfig | None = None,
        adapter: ProjectionAdapter | None = None,
    ) -> ProjectionReplayResult:
        """Replay accepted facts into optional projection; audit only.

        Never updates RelationshipObservation rows. Adapter exceptions become
        failed audit rows with DB truth unchanged.
        """
        cfg = config or ProjectionConfig()
        # Prove version is in owner/novel scope without inventing sources.
        resolved = await self._query.resolve_version(
            session,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
        )
        if resolved is None:
            return ProjectionReplayResult(
                status="failed",
                manifest_checksum="",
                observation_count=0,
                reason="version_not_found",
            )

        manifest = await self.build_manifest(
            session,
            owner_id=owner_id,
            novel_id=novel_id,
            version_id=version_id,
        )
        checksum = manifest["manifest_checksum"]
        count = len(manifest["observations"])

        if not cfg.enabled:
            sink = adapter or DisabledProjectionAdapter()
            checkpoint = sink.project(manifest)
            audit = RelationshipProjectionAudit(
                owner_id=owner_id,
                novel_id=novel_id,
                analysis_version_id=version_id,
                status="disabled",
                manifest=manifest,
                manifest_checksum=checksum,
                checkpoint=checkpoint,
                target=cfg.target,
                error_detail=None,
            )
            session.add(audit)
            await session.flush()
            return ProjectionReplayResult(
                status="disabled",
                manifest_checksum=checksum,
                observation_count=count,
                audit_id=audit.id,
                reason="neo4j_projection_disabled",
                checkpoint=checkpoint,
            )

        sink = adapter or MissingNeo4jAdapter()
        audit = RelationshipProjectionAudit(
            owner_id=owner_id,
            novel_id=novel_id,
            analysis_version_id=version_id,
            status="running",
            manifest=manifest,
            manifest_checksum=checksum,
            checkpoint={},
            target=cfg.target,
            error_detail=None,
        )
        session.add(audit)
        await session.flush()

        try:
            checkpoint = sink.project(manifest)
        except Exception as exc:  # noqa: BLE001 — adapter boundary
            audit.status = "failed"
            audit.error_detail = str(exc)[:2000]
            audit.checkpoint = {"error": str(exc)[:500]}
            await session.flush()
            return ProjectionReplayResult(
                status="failed",
                manifest_checksum=checksum,
                observation_count=count,
                audit_id=audit.id,
                reason=str(exc),
                checkpoint=audit.checkpoint,
            )

        audit.status = "completed"
        audit.checkpoint = checkpoint
        await session.flush()
        return ProjectionReplayResult(
            status="completed",
            manifest_checksum=checksum,
            observation_count=count,
            audit_id=audit.id,
            checkpoint=checkpoint,
        )


relationship_projection_service = RelationshipProjectionService()

__all__ = [
    "DisabledProjectionAdapter",
    "MissingNeo4jAdapter",
    "ProjectionConfig",
    "ProjectionReplayResult",
    "RelationshipProjectionService",
    "replay_accepted_observations",
    "relationship_projection_service",
    "sha256_canonical",
]


async def replay_accepted_observations(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    config: ProjectionConfig | None = None,
    adapter: ProjectionAdapter | None = None,
) -> ProjectionReplayResult:
    """Module-level entry used by plan acceptance criteria."""
    return await relationship_projection_service.replay_accepted_observations(
        session,
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        config=config,
        adapter=adapter,
    )
