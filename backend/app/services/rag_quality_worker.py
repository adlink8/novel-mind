"""Durable RAG quality job worker — lease / heartbeat / checkpoint / resume / cancel.

Phase 06-04 + 06-08: QualityRun is the persistent fact source (PostgreSQL).
In-memory QualityJobStore remains a test double implementing the same protocol.
Idempotent stage keys bind run input_hash + five-tuple chunker lineage.
"""

from __future__ import annotations

import copy
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.eval import QualityRun
from app.schemas.eval import (
    INVALID_LINEAGE_REASON,
    LEGACY_INCOMPARABLE_REASON,
    CalibrationReport,
    ChunkerLineage,
    EvalCase,
    ModelLineage,
    SourceSnapshot,
)
from app.services.rag_fixture import DEFAULT_SIGNING_SECRET, stable_hash
from app.services.rag_quality import (
    build_quality_input_hash,
    canonicalize_chunker_lineage,
    default_healthy,
    policy_hash,
    load_policy,
    run_quality_evaluation,
)

LEASE_SECONDS = 60
HEARTBEAT_SECONDS = 15

# Ordered pipeline stages for SUT quality runs (fixtures already frozen).
QUALITY_STAGES = (
    "queued",
    "validating",
    "retrieving",
    "answering",
    "scoring",
    "arbitrating",
)

TERMINAL_STATUSES = frozenset(
    {
        "passed",
        "qualified",
        "quality_regression",
        "failed_policy",
        "blocked_dependency",
        "invalid_fixture",
        "invalid_lineage",
        "quarantined",
        "cancelled",
    }
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class QualityJob:
    job_id: str
    owner_id: int
    status: str = "queued"
    lease_id: str | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    attempt: int = 0
    cancel_requested: bool = False
    input_hash: str | None = None
    output_hash: str | None = None
    report_signature: str | None = None
    checkpoint: dict[str, Any] = field(default_factory=dict)
    stage_cache: dict[str, Any] = field(default_factory=dict)
    report: dict[str, Any] | None = None
    error_detail: str | None = None
    quality_comparable: bool = False
    incomparable_reason: str | None = None
    metrics: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    # Serialized run inputs (kept for resume)
    payload: dict[str, Any] = field(default_factory=dict)
    work_id: int | None = None
    chunker_name: str | None = None
    chunker_version: str | None = None
    chunker_config_hash: str | None = None
    chunk_manifest_hash: str | None = None
    source_snapshot_hash: str | None = None

    def lineage_dict(self) -> dict[str, str] | None:
        if not all(
            [
                self.chunker_name,
                self.chunker_version,
                self.chunker_config_hash,
                self.chunk_manifest_hash,
                self.source_snapshot_hash,
            ]
        ):
            return None
        return {
            "chunker_name": self.chunker_name or "",
            "chunker_version": self.chunker_version or "",
            "chunker_config_hash": self.chunker_config_hash or "",
            "chunk_manifest_hash": self.chunk_manifest_hash or "",
            "source_snapshot_hash": self.source_snapshot_hash or "",
        }

    def to_public(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "owner_id": self.owner_id,
            "status": self.status,
            "attempt": self.attempt,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "report_signature": self.report_signature,
            "quality_comparable": self.quality_comparable,
            "metrics": self.metrics if self.quality_comparable else None,
            "error_detail": self.error_detail,
            "incomparable_reason": self.incomparable_reason,
            "checkpoint_stage": (self.checkpoint or {}).get("stage"),
            "lease_held": bool(
                self.lease_id
                and self.lease_expires_at
                and self.lease_expires_at > _utcnow()
            ),
            "cancel_requested": self.cancel_requested,
            "chunker_name": self.chunker_name,
            "chunker_version": self.chunker_version,
            "chunker_config_hash": self.chunker_config_hash,
            "chunk_manifest_hash": self.chunk_manifest_hash,
            "source_snapshot_hash": self.source_snapshot_hash,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "report": self.report if self.status in TERMINAL_STATUSES else None,
        }


class QualityJobRepository(Protocol):
    """Repository protocol for durable quality jobs (in-memory or PostgreSQL)."""

    async def create(self, job: QualityJob) -> QualityJob: ...

    async def get(self, job_id: str) -> QualityJob | None: ...

    async def save(self, job: QualityJob) -> None: ...

    async def list_for_owner(self, owner_id: int) -> list[QualityJob]: ...

    async def try_acquire_lease(
        self, job_id: str, *, lease_seconds: int
    ) -> str | None: ...

    async def heartbeat_lease(
        self, job_id: str, lease_id: str, *, lease_seconds: int
    ) -> bool: ...

    async def release_lease(self, job_id: str, lease_id: str) -> bool: ...

    async def clear(self) -> None: ...


class QualityJobStore:
    """Thread-safe in-memory repository (test double; not production fact source)."""

    def __init__(self) -> None:
        self._jobs: dict[str, QualityJob] = {}
        self._lock = threading.RLock()

    async def create(self, job: QualityJob) -> QualityJob:
        with self._lock:
            self._jobs[job.job_id] = job
            return job

    async def get(self, job_id: str) -> QualityJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    async def save(self, job: QualityJob) -> None:
        with self._lock:
            job.updated_at = _utcnow()
            self._jobs[job.job_id] = job

    async def list_for_owner(self, owner_id: int) -> list[QualityJob]:
        with self._lock:
            return [j for j in self._jobs.values() if j.owner_id == owner_id]

    async def try_acquire_lease(self, job_id: str, *, lease_seconds: int) -> str | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            now = _utcnow()
            if job.lease_id and job.lease_expires_at and job.lease_expires_at > now:
                return None
            lease_id = uuid.uuid4().hex
            job.lease_id = lease_id
            job.lease_expires_at = now + timedelta(seconds=lease_seconds)
            job.heartbeat_at = now
            job.updated_at = now
            return lease_id

    async def heartbeat_lease(
        self, job_id: str, lease_id: str, *, lease_seconds: int
    ) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.lease_id != lease_id:
                return False
            now = _utcnow()
            job.heartbeat_at = now
            job.lease_expires_at = now + timedelta(seconds=lease_seconds)
            job.updated_at = now
            return True

    async def release_lease(self, job_id: str, lease_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.lease_id != lease_id:
                return False
            job.lease_id = None
            job.lease_expires_at = None
            job.updated_at = _utcnow()
            return True

    async def clear(self) -> None:
        with self._lock:
            self._jobs.clear()


def _row_to_job(row: QualityRun) -> QualityJob:
    created = row.created_at or _utcnow()
    updated = row.updated_at or created
    return QualityJob(
        job_id=row.job_id,
        owner_id=row.owner_id,
        work_id=row.work_id,
        status=row.status,
        lease_id=row.lease_id,
        lease_expires_at=row.lease_expires_at,
        heartbeat_at=row.heartbeat_at,
        attempt=row.attempt or 0,
        cancel_requested=bool(row.cancel_requested),
        input_hash=row.input_hash,
        output_hash=row.output_hash,
        report_signature=row.report_signature,
        checkpoint=dict(row.checkpoint or {}),
        stage_cache=dict(row.stage_cache or {}),
        report=row.report,
        error_detail=row.error_detail,
        quality_comparable=bool(row.quality_comparable),
        incomparable_reason=row.incomparable_reason,
        metrics=row.metrics,
        created_at=created,
        updated_at=updated,
        payload=dict(row.payload or {}),
        chunker_name=row.chunker_name,
        chunker_version=row.chunker_version,
        chunker_config_hash=row.chunker_config_hash,
        chunk_manifest_hash=row.chunk_manifest_hash,
        source_snapshot_hash=row.source_snapshot_hash,
    )


def _apply_job_to_row(row: QualityRun, job: QualityJob) -> None:
    row.status = job.status
    row.attempt = job.attempt
    row.cancel_requested = job.cancel_requested
    row.lease_id = job.lease_id
    row.lease_expires_at = job.lease_expires_at
    row.heartbeat_at = job.heartbeat_at
    row.payload = job.payload
    row.checkpoint = job.checkpoint
    row.stage_cache = job.stage_cache
    row.metrics = job.metrics
    row.report = job.report
    row.input_hash = job.input_hash
    row.output_hash = job.output_hash
    row.report_signature = job.report_signature
    row.chunker_name = job.chunker_name
    row.chunker_version = job.chunker_version
    row.chunker_config_hash = job.chunker_config_hash
    row.chunk_manifest_hash = job.chunk_manifest_hash
    row.source_snapshot_hash = job.source_snapshot_hash
    row.quality_comparable = job.quality_comparable
    row.incomparable_reason = job.incomparable_reason
    row.error_detail = job.error_detail
    row.work_id = job.work_id


class QualityRunRepository:
    """PostgreSQL-backed repository over QualityRun with CAS lease updates."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, job: QualityJob) -> QualityJob:
        row = QualityRun(
            job_id=job.job_id,
            owner_id=job.owner_id,
            work_id=job.work_id,
            status=job.status,
            attempt=job.attempt,
            cancel_requested=job.cancel_requested,
            lease_id=job.lease_id,
            lease_expires_at=job.lease_expires_at,
            heartbeat_at=job.heartbeat_at,
            payload=job.payload,
            checkpoint=job.checkpoint,
            stage_cache=job.stage_cache,
            metrics=job.metrics,
            report=job.report,
            input_hash=job.input_hash,
            output_hash=job.output_hash,
            report_signature=job.report_signature,
            chunker_name=job.chunker_name,
            chunker_version=job.chunker_version,
            chunker_config_hash=job.chunker_config_hash,
            chunk_manifest_hash=job.chunk_manifest_hash,
            source_snapshot_hash=job.source_snapshot_hash,
            quality_comparable=job.quality_comparable,
            incomparable_reason=job.incomparable_reason,
            error_detail=job.error_detail,
        )
        self.session.add(row)
        await self.session.flush()
        await self.session.commit()
        await self.session.refresh(row)
        return _row_to_job(row)

    async def get(self, job_id: str) -> QualityJob | None:
        row = (
            await self.session.execute(
                select(QualityRun).where(QualityRun.job_id == job_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return _row_to_job(row)

    async def save(self, job: QualityJob) -> None:
        row = (
            await self.session.execute(
                select(QualityRun).where(QualityRun.job_id == job.job_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise QualityWorkerError("job not found", status_code=404)
        _apply_job_to_row(row, job)
        await self.session.flush()
        await self.session.commit()
        job.updated_at = _utcnow()

    async def list_for_owner(self, owner_id: int) -> list[QualityJob]:
        rows = (
            (
                await self.session.execute(
                    select(QualityRun)
                    .where(QualityRun.owner_id == owner_id)
                    .order_by(QualityRun.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [_row_to_job(r) for r in rows]

    async def try_acquire_lease(self, job_id: str, *, lease_seconds: int) -> str | None:
        """Atomic lease acquire: free or expired leases only (CAS)."""
        now = _utcnow()
        lease_id = uuid.uuid4().hex
        expires = now + timedelta(seconds=lease_seconds)
        # Claim if no lease or expired.
        result = await self.session.execute(
            update(QualityRun)
            .where(
                QualityRun.job_id == job_id,
                (
                    (QualityRun.lease_id.is_(None))
                    | (QualityRun.lease_expires_at.is_(None))
                    | (QualityRun.lease_expires_at <= now)
                ),
            )
            .values(
                lease_id=lease_id,
                lease_expires_at=expires,
                heartbeat_at=now,
            )
        )
        await self.session.commit()
        if result.rowcount and result.rowcount > 0:
            return lease_id
        # Distinguish missing job vs held lease
        exists = await self.get(job_id)
        if exists is None:
            return None
        return None

    async def heartbeat_lease(
        self, job_id: str, lease_id: str, *, lease_seconds: int
    ) -> bool:
        now = _utcnow()
        expires = now + timedelta(seconds=lease_seconds)
        result = await self.session.execute(
            update(QualityRun)
            .where(
                QualityRun.job_id == job_id,
                QualityRun.lease_id == lease_id,
            )
            .values(heartbeat_at=now, lease_expires_at=expires)
        )
        await self.session.commit()
        return bool(result.rowcount and result.rowcount > 0)

    async def release_lease(self, job_id: str, lease_id: str) -> bool:
        result = await self.session.execute(
            update(QualityRun)
            .where(
                QualityRun.job_id == job_id,
                QualityRun.lease_id == lease_id,
            )
            .values(lease_id=None, lease_expires_at=None)
        )
        await self.session.commit()
        return bool(result.rowcount and result.rowcount > 0)

    async def clear(self) -> None:
        # Production repository does not support bulk clear.
        raise NotImplementedError("clear is test-only on in-memory store")


# Process-local test double default (API injects QualityRunRepository).
quality_job_store = QualityJobStore()


class QualityWorkerError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _bind_lineage_fields(
    job: QualityJob,
    canonical: ChunkerLineage | None,
    reason: str | None,
) -> None:
    if canonical is None:
        job.chunker_name = None
        job.chunker_version = None
        job.chunker_config_hash = None
        job.chunk_manifest_hash = None
        job.source_snapshot_hash = None
        job.quality_comparable = False
        job.incomparable_reason = reason or LEGACY_INCOMPARABLE_REASON
        return
    five = canonical.five_tuple()
    job.chunker_name = five["chunker_name"]
    job.chunker_version = five["chunker_version"]
    job.chunker_config_hash = five["chunker_config_hash"]
    job.chunk_manifest_hash = five["chunk_manifest_hash"]
    job.source_snapshot_hash = five["source_snapshot_hash"]
    if reason and reason.startswith(INVALID_LINEAGE_REASON):
        job.quality_comparable = False
        job.incomparable_reason = reason
    else:
        job.incomparable_reason = None


class RagQualityWorker:
    """Lease-guarded durable executor for quality runs."""

    def __init__(
        self,
        store: QualityJobRepository | QualityJobStore | None = None,
        *,
        lease_seconds: int = LEASE_SECONDS,
        secret: str = DEFAULT_SIGNING_SECRET,
    ) -> None:
        self.store: QualityJobRepository = store or quality_job_store
        self.lease_seconds = lease_seconds
        self.secret = secret

    async def create_job(
        self,
        *,
        owner_id: int,
        snapshot: SourceSnapshot,
        cases: list[EvalCase],
        generator_lineage: ModelLineage | None,
        judge_lineage: ModelLineage | None,
        calibration_report: CalibrationReport | dict[str, Any] | None,
        baseline: dict[str, Any] | None,
        health: dict[str, Any] | None = None,
        job_id: str | None = None,
        extras: dict[str, Any] | None = None,
        chunker_lineage: ChunkerLineage | dict[str, Any] | None = None,
        require_chunker_lineage: bool = True,
        work_id: int | None = None,
    ) -> QualityJob:
        payload = {
            "snapshot": snapshot.model_dump(mode="json"),
            "cases": [c.model_dump(by_alias=True, mode="json") for c in cases],
            "generator_lineage": (
                generator_lineage.model_dump(by_alias=True, mode="json")
                if generator_lineage
                else None
            ),
            "judge_lineage": (
                judge_lineage.model_dump(by_alias=True, mode="json")
                if judge_lineage
                else None
            ),
            "calibration_report": (
                calibration_report.model_dump(by_alias=True, mode="json")
                if isinstance(calibration_report, CalibrationReport)
                else calibration_report
            ),
            "baseline": baseline,
            "health": health if health is not None else default_healthy(),
            "extras": extras or {},
            "require_chunker_lineage": require_chunker_lineage,
        }

        canonical, lin_reason = canonicalize_chunker_lineage(
            chunker_lineage,
            expected_source_snapshot_hash=snapshot.manifest_hash,
        )
        if canonical is not None:
            payload["chunker_lineage"] = {
                **canonical.five_tuple(),
                "chunker_config": canonical.chunker_config,
            }
        else:
            payload["chunker_lineage"] = None

        try:
            p_hash = policy_hash(load_policy())
        except Exception:
            p_hash = None

        input_hash = build_quality_input_hash(
            snapshot_manifest_hash=snapshot.manifest_hash,
            case_fixture_hashes=[c.fixture_hash for c in cases],
            baseline=baseline,
            policy_hash_value=p_hash,
            chunker_lineage=canonical,
        )

        job = QualityJob(
            job_id=job_id or f"qjob-{uuid.uuid4().hex[:16]}",
            owner_id=owner_id,
            # Only persist work_id when caller proves a real novel FK (fixtures may not).
            work_id=work_id,
            status="queued",
            input_hash=input_hash,
            checkpoint={"stage": "queued", "committed": []},
            payload=payload,
        )
        _bind_lineage_fields(job, canonical, lin_reason)
        # New jobs without proven lineage are never comparable; do not invent hashes.
        if canonical is None or (
            lin_reason and lin_reason.startswith(INVALID_LINEAGE_REASON)
        ):
            job.quality_comparable = False
            if lin_reason is None or lin_reason == LEGACY_INCOMPARABLE_REASON:
                job.incomparable_reason = LEGACY_INCOMPARABLE_REASON
        await self.store.create(job)
        return job

    async def acquire_lease(self, job_id: str, *, owner_id: int | None = None) -> str:
        job = await self.store.get(job_id)
        if job is None:
            raise QualityWorkerError("job not found", status_code=404)
        if owner_id is not None and job.owner_id != owner_id:
            raise QualityWorkerError("cross-owner access denied", status_code=404)
        lease_id = await self.store.try_acquire_lease(
            job_id, lease_seconds=self.lease_seconds
        )
        if lease_id is None:
            # Re-check existence vs conflict
            again = await self.store.get(job_id)
            if again is None:
                raise QualityWorkerError("job not found", status_code=404)
            raise QualityWorkerError("lease held by another worker", status_code=409)
        return lease_id

    async def heartbeat(self, job_id: str, lease_id: str) -> bool:
        return await self.store.heartbeat_lease(
            job_id, lease_id, lease_seconds=self.lease_seconds
        )

    async def release_lease(self, job_id: str, lease_id: str) -> bool:
        return await self.store.release_lease(job_id, lease_id)

    async def request_cancel(
        self, job_id: str, *, owner_id: int | None = None
    ) -> QualityJob:
        job = await self.store.get(job_id)
        if job is None:
            raise QualityWorkerError("job not found", status_code=404)
        if owner_id is not None and job.owner_id != owner_id:
            raise QualityWorkerError("cross-owner access denied", status_code=404)
        if job.status in TERMINAL_STATUSES:
            return job
        job.cancel_requested = True
        now = _utcnow()
        active = job.lease_id and job.lease_expires_at and job.lease_expires_at > now
        if job.status == "queued" and not active:
            job.status = "cancelled"
            job.metrics = None
            job.quality_comparable = False
            job.report = {
                "status": "cancelled",
                "metrics": None,
                "quality_comparable": False,
                "reason": "cancelled before start",
            }
        await self.store.save(job)
        return job

    async def _checkpoint(self, job: QualityJob, stage: str, **artifacts: Any) -> None:
        cp = dict(job.checkpoint or {})
        committed = list(cp.get("committed") or [])
        if stage not in committed:
            committed.append(stage)
        cp["stage"] = stage
        cp["committed"] = committed
        cp.update(artifacts)
        # Persist stage_cache snapshot for resume
        cp["stage_cache"] = copy.deepcopy(job.stage_cache)
        job.checkpoint = cp
        if stage in QUALITY_STAGES and stage not in TERMINAL_STATUSES:
            if job.status not in TERMINAL_STATUSES:
                job.status = stage
        await self.store.save(job)

    def _stage_done(self, job: QualityJob, stage: str) -> bool:
        committed = (job.checkpoint or {}).get("committed") or []
        return stage in committed

    async def run(
        self,
        job_id: str,
        *,
        lease_id: str,
        owner_id: int | None = None,
        retrieve_fn: Callable | None = None,
        answer_fn: Callable | None = None,
        judge_fn: Callable | None = None,
        crash_after_stage: str | None = None,
    ) -> QualityJob:
        """Execute or resume a quality job under lease. crash_after_stage for tests."""
        job = await self.store.get(job_id)
        if job is None:
            raise QualityWorkerError("job not found", status_code=404)
        if owner_id is not None and job.owner_id != owner_id:
            raise QualityWorkerError("cross-owner access denied", status_code=404)
        if job.lease_id != lease_id:
            raise QualityWorkerError("lease mismatch", status_code=409)
        if job.status in TERMINAL_STATUSES:
            return job
        if job.cancel_requested:
            job.status = "cancelled"
            job.metrics = None
            job.quality_comparable = False
            job.report = {
                "status": "cancelled",
                "metrics": None,
                "quality_comparable": False,
                "reason": "cancel requested",
            }
            await self.store.save(job)
            return job

        # Restore stage cache for idempotent resume
        job.stage_cache = dict(
            (job.checkpoint or {}).get("stage_cache") or job.stage_cache
        )
        job.attempt += 1
        await self.store.save(job)

        payload = job.payload
        snapshot = SourceSnapshot.model_validate(payload["snapshot"])
        cases = [EvalCase.model_validate(c) for c in payload["cases"]]
        g = (
            ModelLineage.model_validate(payload["generator_lineage"])
            if payload.get("generator_lineage")
            else None
        )
        j = (
            ModelLineage.model_validate(payload["judge_lineage"])
            if payload.get("judge_lineage")
            else None
        )
        cal = payload.get("calibration_report")
        if isinstance(cal, dict) and cal.get("judge_lineage"):
            try:
                cal = CalibrationReport.model_validate(cal)
            except Exception:
                pass
        baseline = payload.get("baseline")
        health = payload.get("health")
        require_lineage = bool(payload.get("require_chunker_lineage", True))
        raw_lineage = payload.get("chunker_lineage")
        if raw_lineage is None and job.lineage_dict():
            raw_lineage = job.lineage_dict()

        def _maybe_crash(stage: str) -> None:
            if crash_after_stage and crash_after_stage == stage:
                raise RuntimeError(f"simulated crash after {stage}")

        try:
            if not self._stage_done(job, "validating"):
                job.status = "validating"
                await self._checkpoint(job, "validating")
                # Validate lineage before retrieval/scoring
                canonical, lin_reason = canonicalize_chunker_lineage(
                    raw_lineage,
                    expected_source_snapshot_hash=snapshot.manifest_hash,
                )
                if require_lineage and (
                    canonical is None
                    or lin_reason == LEGACY_INCOMPARABLE_REASON
                    or (lin_reason and lin_reason.startswith(INVALID_LINEAGE_REASON))
                ):
                    reason = lin_reason or INVALID_LINEAGE_REASON
                    if reason == LEGACY_INCOMPARABLE_REASON:
                        reason = (
                            f"{INVALID_LINEAGE_REASON}: missing chunker/source lineage"
                        )
                    job.status = "invalid_lineage"
                    job.metrics = None
                    job.quality_comparable = False
                    job.incomparable_reason = reason
                    job.error_detail = reason
                    job.report = {
                        "status": "invalid_lineage",
                        "metrics": None,
                        "quality_comparable": False,
                        "reason": reason,
                        "chunker_lineage": None,
                        "report_signature": None,
                        "output_hash": None,
                    }
                    await self.store.save(job)
                    return job
                if lin_reason and lin_reason.startswith(INVALID_LINEAGE_REASON):
                    job.status = "invalid_lineage"
                    job.metrics = None
                    job.quality_comparable = False
                    job.incomparable_reason = lin_reason
                    job.error_detail = lin_reason
                    job.report = {
                        "status": "invalid_lineage",
                        "metrics": None,
                        "quality_comparable": False,
                        "reason": lin_reason,
                        "chunker_lineage": None,
                        "report_signature": None,
                        "output_hash": None,
                    }
                    await self.store.save(job)
                    return job
                _bind_lineage_fields(job, canonical, lin_reason)
                await self.store.save(job)
                _maybe_crash("validating")

            # retrieving/answering/scoring are internal to run_quality_evaluation;
            # we checkpoint around the combined scoring call using stage_cache for
            # model-call idempotency.
            if not self._stage_done(job, "scoring"):
                job.status = "retrieving"
                await self._checkpoint(job, "retrieving")
                _maybe_crash("retrieving")

                job.status = "answering"
                await self._checkpoint(job, "answering")
                _maybe_crash("answering")

                job.status = "scoring"
                report = run_quality_evaluation(
                    snapshot=snapshot,
                    cases=cases,
                    generator_lineage=g,
                    judge_lineage=j,
                    calibration_report=cal,
                    baseline=baseline,
                    health=health,
                    secret=self.secret,
                    retrieve_fn=retrieve_fn,
                    answer_fn=answer_fn,
                    judge_fn=judge_fn,
                    stage_cache=job.stage_cache,
                    chunker_lineage=raw_lineage,
                    require_chunker_lineage=require_lineage,
                    run_input_hash=job.input_hash,
                )
                await self._checkpoint(
                    job, "scoring", partial_report_status=report.get("status")
                )
                _maybe_crash("scoring")
            else:
                # Resume after scoring checkpoint — re-run with full stage_cache
                # (no new model calls for cached keys)
                report = run_quality_evaluation(
                    snapshot=snapshot,
                    cases=cases,
                    generator_lineage=g,
                    judge_lineage=j,
                    calibration_report=cal,
                    baseline=baseline,
                    health=health,
                    secret=self.secret,
                    retrieve_fn=retrieve_fn,
                    answer_fn=answer_fn,
                    judge_fn=judge_fn,
                    stage_cache=job.stage_cache,
                    chunker_lineage=raw_lineage,
                    require_chunker_lineage=require_lineage,
                    run_input_hash=job.input_hash,
                )

            if job.cancel_requested:
                job.status = "cancelled"
                job.metrics = None
                job.quality_comparable = False
                job.report = {
                    "status": "cancelled",
                    "metrics": None,
                    "quality_comparable": False,
                    "reason": "cancel requested during run",
                }
                await self.store.save(job)
                return job

            job.status = "arbitrating"
            await self._checkpoint(job, "arbitrating")

            # Terminal from arbiter
            job.report = report
            job.status = report["status"]
            job.quality_comparable = bool(report.get("quality_comparable"))
            job.metrics = report.get("metrics") if job.quality_comparable else None
            job.report_signature = report.get("report_signature")
            job.output_hash = report.get("output_hash") or stable_hash(
                {
                    "status": job.status,
                    "metrics": job.metrics,
                    "chunker_lineage": report.get("chunker_lineage"),
                    "signature": report.get("report_signature"),
                }
            )
            if not job.quality_comparable and report.get("status") == "invalid_lineage":
                job.incomparable_reason = report.get(
                    "incomparable_reason"
                ) or report.get("reason")
            await self._checkpoint(job, job.status, final=True)
            return job
        except RuntimeError as exc:
            if "simulated crash" in str(exc):
                # Persist checkpoint for the stage that completed before crash.
                stage = str(exc).rsplit(" ", 1)[-1]
                if stage and not self._stage_done(job, stage):
                    await self._checkpoint(job, stage)
                job.error_detail = str(exc)
                await self.store.save(job)
                raise
            job.status = "failed_policy"
            job.metrics = None
            job.quality_comparable = False
            job.error_detail = f"{type(exc).__name__}: {exc}"
            job.report = {
                "status": "failed_policy",
                "metrics": None,
                "quality_comparable": False,
                "reason": job.error_detail,
            }
            await self.store.save(job)
            return job
        except Exception as exc:
            # Never swallow into zero scores
            job.status = "failed_policy"
            job.metrics = None
            job.quality_comparable = False
            job.error_detail = f"{type(exc).__name__}: {exc}"
            job.report = {
                "status": "failed_policy",
                "metrics": None,
                "quality_comparable": False,
                "reason": job.error_detail,
            }
            await self.store.save(job)
            return job
        finally:
            # keep lease until caller releases (tests may inspect mid-lease)
            pass

    async def resume(
        self,
        job_id: str,
        *,
        owner_id: int | None = None,
        retrieve_fn: Callable | None = None,
        answer_fn: Callable | None = None,
        judge_fn: Callable | None = None,
    ) -> QualityJob:
        """Acquire lease (if free) and continue from last checkpoint."""
        job = await self.store.get(job_id)
        if job is None:
            raise QualityWorkerError("job not found", status_code=404)
        if owner_id is not None and job.owner_id != owner_id:
            raise QualityWorkerError("cross-owner access denied", status_code=404)
        if job.status in TERMINAL_STATUSES:
            return job
        lease_id = await self.acquire_lease(job_id, owner_id=owner_id)
        try:
            return await self.run(
                job_id,
                lease_id=lease_id,
                owner_id=owner_id,
                retrieve_fn=retrieve_fn,
                answer_fn=answer_fn,
                judge_fn=judge_fn,
            )
        finally:
            await self.release_lease(job_id, lease_id)

    async def get_status(
        self, job_id: str, *, owner_id: int | None = None
    ) -> dict[str, Any]:
        job = await self.store.get(job_id)
        if job is None:
            raise QualityWorkerError("job not found", status_code=404)
        if owner_id is not None and job.owner_id != owner_id:
            raise QualityWorkerError("cross-owner access denied", status_code=404)
        return job.to_public()


def make_quality_worker(session: AsyncSession, **kwargs: Any) -> RagQualityWorker:
    """Factory: owner-scoped API uses session-backed QualityRun repository."""
    return RagQualityWorker(store=QualityRunRepository(session), **kwargs)


# Module singleton for scripts / offline use (in-memory only).
rag_quality_worker = RagQualityWorker()
