"""Durable, owner-scoped timeline job state machine."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.analysis import AnalysisChapterStage, AnalysisRun


def stable_stage_key(stage: str, *, chapter_id: int | None = None, attempt: int | None = None) -> str:
    parts = [stage, "book" if chapter_id is None else str(chapter_id)]
    if attempt is not None:
        parts.append(str(attempt))
    return ":".join(parts)


@dataclass
class JobRecord:
    id: int
    owner_id: int
    novel_id: int
    status: str = "pending"
    lease_id: str | None = None
    lease_expires_at: datetime | None = None
    cancel_requested: bool = False
    completed_stages: dict[str, str] = field(default_factory=dict)


class TimelineJobStore(Protocol):
    async def create_or_get_active(self, owner_id: int, novel_id: int) -> JobRecord: ...
    async def get(self, run_id: int) -> JobRecord | None: ...
    async def acquire_lease(self, run_id: int, lease_id: str, now: datetime, expires: datetime) -> bool: ...
    async def complete_stage(self, run_id: int, stage_key: str, checksum: str) -> None: ...
    async def set_status(self, run_id: int, status: str, *, cancel_requested: bool = False) -> None: ...


class InMemoryTimelineJobStore:
    """Deterministic test adapter; production uses PostgresTimelineJobStore."""

    def __init__(self) -> None:
        self.rows: dict[int, JobRecord] = {}
        self._next_id = 1
        self._lock = asyncio.Lock()

    async def create_or_get_active(self, owner_id: int, novel_id: int) -> JobRecord:
        async with self._lock:
            for row in self.rows.values():
                if row.owner_id == owner_id and row.novel_id == novel_id and row.status not in {"completed", "failed"}:
                    return row
            row = JobRecord(self._next_id, owner_id, novel_id)
            self._next_id += 1
            self.rows[row.id] = row
            return row

    async def get(self, run_id: int) -> JobRecord | None:
        return self.rows.get(run_id)

    async def acquire_lease(self, run_id: int, lease_id: str, now: datetime, expires: datetime) -> bool:
        async with self._lock:
            row = self.rows[run_id]
            if row.lease_id and row.lease_expires_at and row.lease_expires_at > now:
                return False
            row.lease_id, row.lease_expires_at, row.status = lease_id, expires, "running"
            return True

    async def complete_stage(self, run_id: int, stage_key: str, checksum: str) -> None:
        self.rows[run_id].completed_stages.setdefault(stage_key, checksum)

    async def set_status(self, run_id: int, status: str, *, cancel_requested: bool = False) -> None:
        row = self.rows[run_id]
        row.status, row.cancel_requested = status, cancel_requested


class PostgresTimelineJobStore:
    """Short-transaction PostgreSQL repository with CAS lease acquisition."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self.sessions = sessions

    @staticmethod
    def _record(row: AnalysisRun, completed: dict[str, str] | None = None) -> JobRecord:
        return JobRecord(row.id, row.owner_id, row.novel_id, row.status, row.lease_id,
                         row.lease_expires_at, row.cancel_requested, completed or {})

    async def create_or_get_active(self, owner_id: int, novel_id: int) -> JobRecord:
        async with self.sessions.begin() as session:
            row = await session.scalar(select(AnalysisRun).where(AnalysisRun.owner_id == owner_id,
                AnalysisRun.novel_id == novel_id, AnalysisRun.active_key == "active").with_for_update())
            if row is None:
                row = AnalysisRun(owner_id=owner_id, novel_id=novel_id, active_key="active", status="pending")
                session.add(row)
                await session.flush()
            return self._record(row)

    async def get(self, run_id: int) -> JobRecord | None:
        async with self.sessions() as session:
            row = await session.get(AnalysisRun, run_id)
            if row is None:
                return None
            stages = (await session.execute(select(AnalysisChapterStage).where(
                AnalysisChapterStage.run_id == run_id, AnalysisChapterStage.status == "completed"))).scalars()
            return self._record(row, {s.stage_key: s.artifact_checksum or "" for s in stages})

    async def acquire_lease(self, run_id: int, lease_id: str, now: datetime, expires: datetime) -> bool:
        async with self.sessions.begin() as session:
            result = await session.execute(update(AnalysisRun).where(AnalysisRun.id == run_id,
                or_(AnalysisRun.lease_id.is_(None), AnalysisRun.lease_expires_at <= now)).values(
                    lease_id=lease_id, lease_expires_at=expires, heartbeat_at=now, status="running"))
            return result.rowcount == 1

    async def complete_stage(self, run_id: int, stage_key: str, checksum: str) -> None:
        async with self.sessions.begin() as session:
            row = await session.scalar(select(AnalysisChapterStage).where(
                AnalysisChapterStage.run_id == run_id, AnalysisChapterStage.stage_key == stage_key).with_for_update())
            if row is None:
                session.add(AnalysisChapterStage(run_id=run_id, stage_key=stage_key,
                    status="completed", artifact_checksum=checksum))
            elif row.status != "completed":
                row.status, row.artifact_checksum = "completed", checksum

    async def set_status(self, run_id: int, status: str, *, cancel_requested: bool = False) -> None:
        async with self.sessions.begin() as session:
            await session.execute(update(AnalysisRun).where(AnalysisRun.id == run_id).values(
                status=status, cancel_requested=cancel_requested,
                active_key=None if status in {"completed", "failed"} else "active"))


class TimelineJobCoordinator:
    def __init__(self, store: TimelineJobStore, *, lease_seconds: int = 300) -> None:
        self.store, self.lease_seconds = store, lease_seconds

    async def start_on_first_entry(self, *, owner_id: int, novel_id: int) -> JobRecord:
        return await self.store.create_or_get_active(owner_id, novel_id)

    async def get(self, run_id: int) -> JobRecord:
        row = await self.store.get(run_id)
        if row is None:
            raise KeyError(run_id)
        return row

    async def acquire_lease(self, run_id: int, *, now: datetime | None = None) -> str | None:
        now = now or datetime.now(UTC)
        lease_id = uuid.uuid4().hex
        if await self.store.acquire_lease(run_id, lease_id, now, now + timedelta(seconds=self.lease_seconds)):
            return lease_id
        return None

    async def complete_stage(self, run_id: int, stage_key: str, checksum: str) -> None:
        await self.store.complete_stage(run_id, stage_key, checksum)

    async def pending_stages(self, run_id: int, stages: list[str]) -> list[str]:
        row = await self.get(run_id)
        return [stage for stage in stages if stage not in row.completed_stages]

    async def request_cancel(self, run_id: int) -> None:
        await self.store.set_status(run_id, "cancelled", cancel_requested=True)

    async def resume(self, run_id: int) -> None:
        await self.store.set_status(run_id, "pending", cancel_requested=False)
