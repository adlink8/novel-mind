"""Owner/version-scoped append and idempotent replay of QueryPlanTrace.

Phase 26-01 / REQ-QP-01. Semantics:

- ``append_trace`` persists one validated plan after a single gate. A unique
  ``idempotency_key`` conflict only replays the existing immutable row; it never
  creates a second trace.
- Any validation, owner/version lineage or database error rolls back and leaves
  no half trace.
- No UPDATE path exists. Cross-owner reads are rejected. No active-pointer or
  promotion write is defined (D-14).
- ``replay_by_key`` recomputes the canonical payload checksum before returning,
  so tampered / drifted rows fail closed.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.queryplan import QueryPlanTrace
from app.services.queryplan.schemas import (
    QueryPlan,
    plan_payload_hash,
)


class QueryPlanRepositoryError(ValueError):
    pass


class QueryPlanRepository:
    """Append-only repository for immutable ``QueryPlanTrace`` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append_trace(self, plan: QueryPlan) -> QueryPlanTrace:
        """Persist the validated plan, or replay the existing row on key conflict."""

        existing = await self._session.scalar(
            select(QueryPlanTrace).where(
                QueryPlanTrace.idempotency_key == plan.trace.idempotency_key
            )
        )
        if existing is not None:
            self._assert_same_scope(existing, plan)
            return existing

        row = self._to_row(plan)
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError:
            # Unique idempotency_key conflict under concurrency: roll back the
            # failed insert and replay the row that won the race.
            await self._session.rollback()
            existing = await self._session.scalar(
                select(QueryPlanTrace).where(
                    QueryPlanTrace.idempotency_key == plan.trace.idempotency_key
                )
            )
            if existing is None:
                raise QueryPlanRepositoryError(
                    "idempotent replay race: existing row not found after rollback"
                )
            self._assert_same_scope(existing, plan)
            return existing
        return row

    async def replay_by_key(
        self, *, owner_id: int, idempotency_key: str
    ) -> QueryPlanTrace:
        """Read a trace by idempotency key inside the owner scope (fail-closed)."""

        row = await self._session.scalar(
            select(QueryPlanTrace).where(
                QueryPlanTrace.owner_id == owner_id,
                QueryPlanTrace.idempotency_key == idempotency_key,
            )
        )
        if row is None:
            raise QueryPlanRepositoryError("trace not found in owner scope")
        self._assert_checksum(row)
        return row

    async def replay_by_trace_id(
        self, *, owner_id: int, trace_id: str
    ) -> QueryPlanTrace:
        """Read a trace by trace id inside the owner scope (fail-closed)."""

        row = await self._session.scalar(
            select(QueryPlanTrace).where(
                QueryPlanTrace.owner_id == owner_id,
                QueryPlanTrace.trace_id == trace_id,
            )
        )
        if row is None:
            raise QueryPlanRepositoryError("trace not found in owner scope")
        self._assert_checksum(row)
        return row

    async def list_for_scope(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_id: int,
        limit: int = 50,
    ) -> list[QueryPlanTrace]:
        """Owner/novel/version-scoped listing, oldest first."""

        rows = (
            await self._session.scalars(
                select(QueryPlanTrace)
                .where(
                    QueryPlanTrace.owner_id == owner_id,
                    QueryPlanTrace.novel_id == novel_id,
                    QueryPlanTrace.version_id == version_id,
                )
                .order_by(QueryPlanTrace.id.asc())
                .limit(limit)
            )
        ).all()
        return list(rows)

    # ------------------------------------------------------------------ helpers

    def _assert_same_scope(self, row: QueryPlanTrace, plan: QueryPlan) -> None:
        if (
            row.owner_id != plan.owner_id
            or row.novel_id != plan.novel_id
            or row.version_id != plan.version_id
        ):
            raise QueryPlanRepositoryError(
                "idempotency key already belongs to a different "
                "owner/novel/version scope"
            )

    def _to_row(self, plan: QueryPlan) -> QueryPlanTrace:
        payload = plan.model_dump(mode="json", exclude={"trace"})
        recomputed = plan_payload_hash(payload)
        if recomputed != plan.trace.canonical_payload_hash:
            raise QueryPlanRepositoryError(
                "canonical payload hash mismatch before append"
            )
        return QueryPlanTrace(
            trace_id=plan.trace.trace_id,
            idempotency_key=plan.trace.idempotency_key,
            owner_id=plan.owner_id,
            novel_id=plan.novel_id,
            version_id=plan.version_id,
            cutoff_mode=plan.spoiler_cutoff.mode.value,
            through_chapter=plan.spoiler_cutoff.through_chapter,
            full_book_authorized=plan.spoiler_cutoff.full_book_authorized,
            schema_version=plan.schema_version,
            parser_version=plan.parser_version,
            source=plan.trace.source,
            dataset_lineage=plan.trace.dataset_lineage,
            canonical_payload=payload,
            canonical_payload_hash=plan.trace.canonical_payload_hash,
            availability_checksum=plan.trace.availability_checksum,
            fallback=plan.fallback.model_dump(mode="json"),
            blocked_reason=None,
            created_at=plan.trace.created_at,
            updated_at=plan.trace.created_at,
        )

    def _assert_checksum(self, row: QueryPlanTrace) -> None:
        recomputed = plan_payload_hash(row.canonical_payload)
        if recomputed != row.canonical_payload_hash:
            raise QueryPlanRepositoryError(
                "canonical payload checksum mismatch on replay"
            )
