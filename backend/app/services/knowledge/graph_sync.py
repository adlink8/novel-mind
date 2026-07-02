"""Optional Neo4j sync boundary for accepted PostgreSQL graph rows."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeRelationJudgment


@dataclass(slots=True)
class GraphSyncConfig:
    """Neo4j sync configuration. Disabled by default."""

    enabled: bool = False
    uri: str | None = None
    username: str | None = None
    password: str | None = None


@dataclass(slots=True)
class GraphSyncResult:
    """Outcome of a graph sync attempt."""

    status: str
    accepted_rows_seen: int
    synced_count: int = 0
    reason: str | None = None


class KnowledgeGraphSyncService:
    """Read accepted PostgreSQL rows and optionally project to Neo4j."""

    async def sync_run(
        self,
        db: AsyncSession,
        *,
        run_id: int,
        owner_id: int | None = None,
        config: GraphSyncConfig | None = None,
    ) -> GraphSyncResult:
        """Sync accepted rows. Disabled mode is a no-op."""

        cfg = config or GraphSyncConfig()
        accepted_count = await self._accepted_count(db, run_id=run_id, owner_id=owner_id)
        if not cfg.enabled:
            return GraphSyncResult(
                status="skipped",
                accepted_rows_seen=accepted_count,
                reason="neo4j_sync_disabled",
            )
        if not cfg.uri or not cfg.username or not cfg.password:
            return GraphSyncResult(
                status="failed",
                accepted_rows_seen=accepted_count,
                reason="neo4j_config_missing",
            )

        return GraphSyncResult(
            status="failed",
            accepted_rows_seen=accepted_count,
            reason="neo4j_driver_not_configured",
        )

    async def _accepted_count(
        self,
        db: AsyncSession,
        *,
        run_id: int,
        owner_id: int | None,
    ) -> int:
        stmt = select(func.count()).select_from(KnowledgeRelationJudgment).where(
            KnowledgeRelationJudgment.run_id == run_id,
            KnowledgeRelationJudgment.status == "accepted",
            KnowledgeRelationJudgment.gate_status == "accepted",
        )
        if owner_id is not None:
            stmt = stmt.where(KnowledgeRelationJudgment.owner_id == owner_id)
        result = await db.execute(stmt)
        return int(result.scalar_one())


knowledge_graph_sync_service = KnowledgeGraphSyncService()
