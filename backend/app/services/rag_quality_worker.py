"""Durable RAG quality job worker — lease / heartbeat / checkpoint / resume / cancel.

Phase 06-04. Idempotent stage keys: run+stage+input_hash.
Crash recovery resumes from last committed checkpoint without re-issuing model calls.
"""

from __future__ import annotations

import copy
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.schemas.eval import CalibrationReport, EvalCase, ModelLineage, SourceSnapshot
from app.services.rag_fixture import DEFAULT_SIGNING_SECRET, stable_hash
from app.services.rag_quality import (
    default_healthy,
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
    checkpoint: dict[str, Any] = field(default_factory=dict)
    stage_cache: dict[str, Any] = field(default_factory=dict)
    report: dict[str, Any] | None = None
    error_detail: str | None = None
    quality_comparable: bool = False
    metrics: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    # Serialized run inputs (kept for resume)
    payload: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "owner_id": self.owner_id,
            "status": self.status,
            "attempt": self.attempt,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "quality_comparable": self.quality_comparable,
            "metrics": self.metrics if self.quality_comparable else None,
            "error_detail": self.error_detail,
            "checkpoint_stage": (self.checkpoint or {}).get("stage"),
            "lease_held": bool(
                self.lease_id
                and self.lease_expires_at
                and self.lease_expires_at > _utcnow()
            ),
            "cancel_requested": self.cancel_requested,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "report": self.report if self.status in TERMINAL_STATUSES else None,
        }


class QualityJobStore:
    """Thread-safe in-process durable job store (swap for DB later without API change)."""

    def __init__(self) -> None:
        self._jobs: dict[str, QualityJob] = {}
        self._lock = threading.RLock()

    def create(self, job: QualityJob) -> QualityJob:
        with self._lock:
            self._jobs[job.job_id] = job
            return job

    def get(self, job_id: str) -> QualityJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def save(self, job: QualityJob) -> None:
        with self._lock:
            job.updated_at = _utcnow()
            self._jobs[job.job_id] = job

    def list_for_owner(self, owner_id: int) -> list[QualityJob]:
        with self._lock:
            return [j for j in self._jobs.values() if j.owner_id == owner_id]

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()


# Process-global store for API adapter / tests
quality_job_store = QualityJobStore()


class QualityWorkerError(Exception):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class RagQualityWorker:
    """Lease-guarded durable executor for quality runs."""

    def __init__(
        self,
        store: QualityJobStore | None = None,
        *,
        lease_seconds: int = LEASE_SECONDS,
        secret: str = DEFAULT_SIGNING_SECRET,
    ) -> None:
        self.store = store or quality_job_store
        self.lease_seconds = lease_seconds
        self.secret = secret

    def create_job(
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
        }
        input_hash = stable_hash(
            {
                "snapshot": payload["snapshot"].get("manifest_hash"),
                "cases": [c.get("fixture_hash") for c in payload["cases"]],
                "baseline": baseline,
            }
        )
        job = QualityJob(
            job_id=job_id or f"qjob-{uuid.uuid4().hex[:16]}",
            owner_id=owner_id,
            status="queued",
            input_hash=input_hash,
            checkpoint={"stage": "queued", "committed": []},
            payload=payload,
        )
        self.store.create(job)
        return job

    def acquire_lease(self, job_id: str, *, owner_id: int | None = None) -> str:
        job = self.store.get(job_id)
        if job is None:
            raise QualityWorkerError("job not found", status_code=404)
        if owner_id is not None and job.owner_id != owner_id:
            raise QualityWorkerError("cross-owner access denied", status_code=404)
        now = _utcnow()
        if (
            job.lease_id
            and job.lease_expires_at
            and job.lease_expires_at > now
        ):
            raise QualityWorkerError("lease held by another worker", status_code=409)
        lease_id = uuid.uuid4().hex
        job.lease_id = lease_id
        job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
        job.heartbeat_at = now
        self.store.save(job)
        return lease_id

    def heartbeat(self, job_id: str, lease_id: str) -> bool:
        job = self.store.get(job_id)
        if job is None or job.lease_id != lease_id:
            return False
        now = _utcnow()
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
        self.store.save(job)
        return True

    def release_lease(self, job_id: str, lease_id: str) -> bool:
        job = self.store.get(job_id)
        if job is None or job.lease_id != lease_id:
            return False
        job.lease_id = None
        job.lease_expires_at = None
        self.store.save(job)
        return True

    def request_cancel(self, job_id: str, *, owner_id: int | None = None) -> QualityJob:
        job = self.store.get(job_id)
        if job is None:
            raise QualityWorkerError("job not found", status_code=404)
        if owner_id is not None and job.owner_id != owner_id:
            raise QualityWorkerError("cross-owner access denied", status_code=404)
        if job.status in TERMINAL_STATUSES:
            return job
        job.cancel_requested = True
        # If still queued and no active lease, cancel immediately
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
        self.store.save(job)
        return job

    def _checkpoint(self, job: QualityJob, stage: str, **artifacts: Any) -> None:
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
        self.store.save(job)

    def _stage_done(self, job: QualityJob, stage: str) -> bool:
        committed = (job.checkpoint or {}).get("committed") or []
        return stage in committed

    def run(
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
        job = self.store.get(job_id)
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
            self.store.save(job)
            return job

        # Restore stage cache for idempotent resume
        job.stage_cache = dict((job.checkpoint or {}).get("stage_cache") or job.stage_cache)
        job.attempt += 1
        self.store.save(job)

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

        def _maybe_crash(stage: str) -> None:
            if crash_after_stage and crash_after_stage == stage:
                self._checkpoint(job, stage)
                raise RuntimeError(f"simulated crash after {stage}")

        try:
            if not self._stage_done(job, "validating"):
                job.status = "validating"
                self._checkpoint(job, "validating")
                _maybe_crash("validating")

            # retrieving/answering/scoring are internal to run_quality_evaluation;
            # we checkpoint around the combined scoring call using stage_cache for
            # model-call idempotency.
            if not self._stage_done(job, "scoring"):
                job.status = "retrieving"
                self._checkpoint(job, "retrieving")
                _maybe_crash("retrieving")

                job.status = "answering"
                self._checkpoint(job, "answering")
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
                )
                self._checkpoint(job, "scoring", partial_report_status=report.get("status"))
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
                self.store.save(job)
                return job

            job.status = "arbitrating"
            self._checkpoint(job, "arbitrating")

            # Terminal from arbiter
            job.report = report
            job.status = report["status"]
            job.quality_comparable = bool(report.get("quality_comparable"))
            job.metrics = report.get("metrics") if job.quality_comparable else None
            job.output_hash = stable_hash(
                {
                    "status": job.status,
                    "metrics": job.metrics,
                    "signature": report.get("report_signature"),
                }
            )
            self._checkpoint(job, job.status, final=True)
            return job
        except RuntimeError as exc:
            if "simulated crash" in str(exc):
                job.error_detail = str(exc)
                self.store.save(job)
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
            self.store.save(job)
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
            self.store.save(job)
            return job
        finally:
            # keep lease until caller releases (tests may inspect mid-lease)
            pass

    def resume(
        self,
        job_id: str,
        *,
        owner_id: int | None = None,
        retrieve_fn: Callable | None = None,
        answer_fn: Callable | None = None,
        judge_fn: Callable | None = None,
    ) -> QualityJob:
        """Acquire lease (if free) and continue from last checkpoint."""
        job = self.store.get(job_id)
        if job is None:
            raise QualityWorkerError("job not found", status_code=404)
        if owner_id is not None and job.owner_id != owner_id:
            raise QualityWorkerError("cross-owner access denied", status_code=404)
        if job.status in TERMINAL_STATUSES:
            return job
        lease_id = self.acquire_lease(job_id, owner_id=owner_id)
        try:
            return self.run(
                job_id,
                lease_id=lease_id,
                owner_id=owner_id,
                retrieve_fn=retrieve_fn,
                answer_fn=answer_fn,
                judge_fn=judge_fn,
            )
        finally:
            self.release_lease(job_id, lease_id)

    def get_status(self, job_id: str, *, owner_id: int | None = None) -> dict[str, Any]:
        job = self.store.get(job_id)
        if job is None:
            raise QualityWorkerError("job not found", status_code=404)
        if owner_id is not None and job.owner_id != owner_id:
            raise QualityWorkerError("cross-owner access denied", status_code=404)
        return job.to_public()


# Module singleton
rag_quality_worker = RagQualityWorker()
