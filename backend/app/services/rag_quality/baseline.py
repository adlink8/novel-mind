"""Durable baseline prepare/commit + cross-chunker report (rag_quality package).

Phase 06-09: async DB services, consumed by api/eval.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.rag_fixture import stable_hash

from .lineage import LEGACY_INCOMPARABLE_REASON

_BASELINE_ELIGIBLE = frozenset({"passed", "qualified"})


class BaselineServiceError(Exception):
    """Owner-safe baseline prepare/commit/report error with HTTP status."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def compute_prepare_fingerprint(
    *,
    run_status: str,
    input_hash: str,
    output_hash: str,
    report_signature: str,
    metrics: dict[str, Any],
    chunker_name: str,
    chunker_version: str,
    chunker_config_hash: str,
    chunk_manifest_hash: str,
    source_snapshot_hash: str,
) -> str:
    """Canonical fingerprint frozen at prepare; commit revalidates equality."""
    return stable_hash(
        {
            "run_status": run_status,
            "input_hash": input_hash,
            "output_hash": output_hash,
            "report_signature": report_signature,
            "metrics": metrics,
            "chunker_name": chunker_name,
            "chunker_version": chunker_version,
            "chunker_config_hash": chunker_config_hash,
            "chunk_manifest_hash": chunk_manifest_hash,
            "source_snapshot_hash": source_snapshot_hash,
        }
    )


def _run_lineage_complete(run: Any) -> bool:
    fields = (
        run.chunker_name,
        run.chunker_version,
        run.chunker_config_hash,
        run.chunk_manifest_hash,
        run.source_snapshot_hash,
    )
    if not all(fields):
        return False
    for h in (
        run.chunker_config_hash,
        run.chunk_manifest_hash,
        run.source_snapshot_hash,
        run.input_hash,
        run.output_hash,
    ):
        if not h or len(str(h)) != 64:
            return False
    if not run.report_signature:
        return False
    return True


def _validate_run_for_baseline(run: Any, *, owner_id: int) -> None:
    if run is None or run.owner_id != owner_id:
        raise BaselineServiceError("quality run not found", status_code=404)
    if run.status not in _BASELINE_ELIGIBLE:
        raise BaselineServiceError(
            f"run status {run.status!r} is not baseline-eligible "
            f"(require passed/qualified)",
            status_code=400,
        )
    if not run.quality_comparable:
        reason = run.incomparable_reason or LEGACY_INCOMPARABLE_REASON
        raise BaselineServiceError(
            f"run is not quality_comparable ({reason})",
            status_code=400,
        )
    if not run.metrics or not isinstance(run.metrics, dict):
        raise BaselineServiceError("run metrics missing", status_code=400)
    if not _run_lineage_complete(run):
        raise BaselineServiceError(
            "incomplete canonical lineage or identity hashes",
            status_code=400,
        )


def _candidate_public(c: Any) -> dict[str, Any]:
    return {
        "id": c.id,
        "owner_id": c.owner_id,
        "quality_run_id": c.quality_run_id,
        "quality_run_job_id": c.quality_run_job_id,
        "prepare_token": c.prepare_token,
        "prepare_version": c.prepare_version,
        "state": c.state,
        "reason": c.reason,
        "chunker_name": c.chunker_name,
        "chunker_version": c.chunker_version,
        "chunker_config_hash": c.chunker_config_hash,
        "chunk_manifest_hash": c.chunk_manifest_hash,
        "source_snapshot_hash": c.source_snapshot_hash,
        "run_status": c.run_status,
        "input_hash": c.input_hash,
        "output_hash": c.output_hash,
        "report_signature": c.report_signature,
        "metrics_snapshot": c.metrics_snapshot or {},
        "prepare_fingerprint": c.prepare_fingerprint,
        "journal": list(c.journal or []),
        "prepared_at": c.prepared_at.isoformat() if c.prepared_at else None,
        "committed_at": c.committed_at.isoformat() if c.committed_at else None,
    }


def _active_public(a: Any) -> dict[str, Any]:
    return {
        "owner_id": a.owner_id,
        "candidate_id": a.candidate_id,
        "quality_run_id": a.quality_run_id,
        "metrics_snapshot": a.metrics_snapshot or {},
        "chunker_name": a.chunker_name,
        "chunker_version": a.chunker_version,
        "chunker_config_hash": a.chunker_config_hash,
        "chunk_manifest_hash": a.chunk_manifest_hash,
        "source_snapshot_hash": a.source_snapshot_hash,
        "committed_at": a.committed_at.isoformat() if a.committed_at else None,
    }


async def prepare_baseline_candidate(
    session: Any,
    *,
    owner_id: int,
    job_id: str,
) -> dict[str, Any]:
    """Persist prepare evidence from current QualityRun (DB is sole source)."""
    from sqlalchemy import select
    from app.models.eval import BaselineCandidate, QualityRun
    import uuid

    result = await session.execute(
        select(QualityRun).where(QualityRun.job_id == job_id)
    )
    run = result.scalar_one_or_none()
    _validate_run_for_baseline(run, owner_id=owner_id)

    metrics = dict(run.metrics)
    fingerprint = compute_prepare_fingerprint(
        run_status=run.status,
        input_hash=run.input_hash,
        output_hash=run.output_hash,
        report_signature=run.report_signature,
        metrics=metrics,
        chunker_name=run.chunker_name,
        chunker_version=run.chunker_version,
        chunker_config_hash=run.chunker_config_hash,
        chunk_manifest_hash=run.chunk_manifest_hash,
        source_snapshot_hash=run.source_snapshot_hash,
    )
    now = datetime.now(timezone.utc)
    token = uuid.uuid4().hex
    entry = {
        "at": now.isoformat(),
        "event": "prepared",
        "job_id": run.job_id,
        "fingerprint": fingerprint,
    }
    cand = BaselineCandidate(
        owner_id=owner_id,
        quality_run_id=run.id,
        quality_run_job_id=run.job_id,
        prepare_token=token,
        prepare_version=1,
        state="prepared",
        reason=None,
        chunker_name=run.chunker_name,
        chunker_version=run.chunker_version,
        chunker_config_hash=run.chunker_config_hash,
        chunk_manifest_hash=run.chunk_manifest_hash,
        source_snapshot_hash=run.source_snapshot_hash,
        run_status=run.status,
        input_hash=run.input_hash,
        output_hash=run.output_hash,
        report_signature=run.report_signature,
        metrics_snapshot=metrics,
        prepare_fingerprint=fingerprint,
        journal=[entry],
        prepared_at=now,
    )
    session.add(cand)
    await session.flush()
    return _candidate_public(cand)


async def commit_baseline_candidate(
    session: Any,
    *,
    owner_id: int,
    candidate_id: int,
    prepare_token: str,
) -> dict[str, Any]:
    """Revalidate prepare fingerprint against current QualityRun; set active."""
    from sqlalchemy import select
    from app.models.eval import ActiveBaseline, BaselineCandidate, QualityRun

    result = await session.execute(
        select(BaselineCandidate)
        .where(
            BaselineCandidate.id == candidate_id,
            BaselineCandidate.owner_id == owner_id,
        )
        .with_for_update()
    )
    cand = result.scalar_one_or_none()
    if cand is None:
        raise BaselineServiceError("candidate not found", status_code=404)
    if cand.prepare_token != prepare_token:
        raise BaselineServiceError("prepare_token mismatch", status_code=403)

    # Idempotent: already committed this candidate
    if cand.state == "committed":
        active = (
            await session.execute(
                select(ActiveBaseline).where(ActiveBaseline.owner_id == owner_id)
            )
        ).scalar_one_or_none()
        return {
            "ok": True,
            "candidate": _candidate_public(cand),
            "active": _active_public(active) if active else None,
            "idempotent": True,
            "error": None,
        }

    if cand.state != "prepared":
        raise BaselineServiceError(
            f"candidate state {cand.state!r} is not committable",
            status_code=400,
        )

    run = (
        await session.execute(
            select(QualityRun)
            .where(QualityRun.id == cand.quality_run_id)
            .with_for_update()
        )
    ).scalar_one_or_none()

    # Capture previous active for "unchanged on reject"
    prev_active = (
        await session.execute(
            select(ActiveBaseline).where(ActiveBaseline.owner_id == owner_id)
        )
    ).scalar_one_or_none()
    prev_candidate_id = prev_active.candidate_id if prev_active else None

    reject_reason: str | None = None
    try:
        _validate_run_for_baseline(run, owner_id=owner_id)
    except BaselineServiceError as exc:
        reject_reason = exc.message

    if reject_reason is None:
        current_fp = compute_prepare_fingerprint(
            run_status=run.status,
            input_hash=run.input_hash or "",
            output_hash=run.output_hash or "",
            report_signature=run.report_signature or "",
            metrics=dict(run.metrics or {}),
            chunker_name=run.chunker_name or "",
            chunker_version=run.chunker_version or "",
            chunker_config_hash=run.chunker_config_hash or "",
            chunk_manifest_hash=run.chunk_manifest_hash or "",
            source_snapshot_hash=run.source_snapshot_hash or "",
        )
        if current_fp != cand.prepare_fingerprint:
            reject_reason = (
                "run changed after prepare (lineage/hash/signature/metrics/status)"
            )
        else:
            for attr in (
                "chunker_name",
                "chunker_version",
                "chunker_config_hash",
                "chunk_manifest_hash",
                "source_snapshot_hash",
                "input_hash",
                "output_hash",
                "report_signature",
            ):
                if getattr(run, attr) != getattr(cand, attr):
                    reject_reason = f"field {attr} diverged from prepare evidence"
                    break

    if reject_reason is not None:
        now = datetime.now(timezone.utc)
        journal = list(cand.journal or [])
        journal.append(
            {
                "at": now.isoformat(),
                "event": "rejected",
                "reason": reject_reason,
                "prev_active_candidate_id": prev_candidate_id,
            }
        )
        cand.state = "rejected"
        cand.reason = reject_reason
        cand.journal = journal
        await session.flush()
        return {
            "ok": False,
            "candidate": _candidate_public(cand),
            "active": _active_public(prev_active) if prev_active else None,
            "idempotent": False,
            "error": reject_reason,
        }

    now = datetime.now(timezone.utc)
    journal = list(cand.journal or [])
    journal.append(
        {
            "at": now.isoformat(),
            "event": "committed",
            "fingerprint": cand.prepare_fingerprint,
            "prev_active_candidate_id": prev_candidate_id,
        }
    )
    cand.state = "committed"
    cand.committed_at = now
    cand.journal = journal
    cand.reason = None

    if prev_active is None:
        active = ActiveBaseline(
            owner_id=owner_id,
            candidate_id=cand.id,
            quality_run_id=cand.quality_run_id,
            metrics_snapshot=dict(cand.metrics_snapshot or {}),
            chunker_name=cand.chunker_name,
            chunker_version=cand.chunker_version,
            chunker_config_hash=cand.chunker_config_hash,
            chunk_manifest_hash=cand.chunk_manifest_hash,
            source_snapshot_hash=cand.source_snapshot_hash,
            committed_at=now,
        )
        session.add(active)
    else:
        prev_active.candidate_id = cand.id
        prev_active.quality_run_id = cand.quality_run_id
        prev_active.metrics_snapshot = dict(cand.metrics_snapshot or {})
        prev_active.chunker_name = cand.chunker_name
        prev_active.chunker_version = cand.chunker_version
        prev_active.chunker_config_hash = cand.chunker_config_hash
        prev_active.chunk_manifest_hash = cand.chunk_manifest_hash
        prev_active.source_snapshot_hash = cand.source_snapshot_hash
        prev_active.committed_at = now
        active = prev_active

    await session.flush()
    return {
        "ok": True,
        "candidate": _candidate_public(cand),
        "active": _active_public(active),
        "idempotent": False,
        "error": None,
    }


async def get_active_baseline(session: Any, *, owner_id: int) -> dict[str, Any] | None:
    from sqlalchemy import select
    from app.models.eval import ActiveBaseline

    row = (
        await session.execute(
            select(ActiveBaseline).where(ActiveBaseline.owner_id == owner_id)
        )
    ).scalar_one_or_none()
    return _active_public(row) if row else None


async def build_cross_chunker_report(
    session: Any,
    *,
    owner_id: int,
    source_snapshot_hash: str,
) -> dict[str, Any]:
    """Same-snapshot multi-chunker report; excludes legacy/incomplete/invalid."""
    from sqlalchemy import select
    from app.models.eval import QualityRun

    if not source_snapshot_hash or len(source_snapshot_hash) != 64:
        raise BaselineServiceError(
            "source_snapshot_hash must be sha256 hex", status_code=400
        )

    rows = (
        (
            await session.execute(
                select(QualityRun).where(
                    QualityRun.owner_id == owner_id,
                )
            )
        )
        .scalars()
        .all()
    )

    series: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []

    for run in rows:
        # Different snapshot → skip silently (not part of this report boundary)
        if (
            run.source_snapshot_hash
            and run.source_snapshot_hash != source_snapshot_hash
        ):
            exclusions.append(
                {
                    "job_id": run.job_id,
                    "quality_run_id": run.id,
                    "reason": "different_source_snapshot",
                }
            )
            continue
        if not run.source_snapshot_hash:
            exclusions.append(
                {
                    "job_id": run.job_id,
                    "quality_run_id": run.id,
                    "reason": "missing_source_snapshot",
                }
            )
            continue
        if run.source_snapshot_hash != source_snapshot_hash:
            continue
        if not run.quality_comparable:
            exclusions.append(
                {
                    "job_id": run.job_id,
                    "quality_run_id": run.id,
                    "reason": run.incomparable_reason or LEGACY_INCOMPARABLE_REASON,
                }
            )
            continue
        if not _run_lineage_complete(run):
            exclusions.append(
                {
                    "job_id": run.job_id,
                    "quality_run_id": run.id,
                    "reason": "incomplete_lineage",
                }
            )
            continue
        if run.status not in _BASELINE_ELIGIBLE and run.status not in {
            "quality_regression",
            "failed_policy",
        }:
            # Allow non-passed comparable runs only if complete; still report series
            # but prefer terminal scored statuses with metrics.
            if not run.metrics:
                exclusions.append(
                    {
                        "job_id": run.job_id,
                        "quality_run_id": run.id,
                        "reason": f"status_not_reportable:{run.status}",
                    }
                )
                continue
        if not run.metrics:
            exclusions.append(
                {
                    "job_id": run.job_id,
                    "quality_run_id": run.id,
                    "reason": "metrics_missing",
                }
            )
            continue

        metrics = dict(run.metrics)
        series.append(
            {
                "job_id": run.job_id,
                "quality_run_id": run.id,
                "status": run.status,
                "chunker_name": run.chunker_name,
                "chunker_version": run.chunker_version,
                "chunker_config_hash": run.chunker_config_hash,
                "chunk_manifest_hash": run.chunk_manifest_hash,
                "source_snapshot_hash": run.source_snapshot_hash,
                "metrics": metrics,
                "input_hash": run.input_hash,
                "output_hash": run.output_hash,
                "report_signature": run.report_signature,
                "cost_usd_total": metrics.get("cost_usd_total"),
                "latency_ms_total": metrics.get("latency_ms_total"),
            }
        )

    # Deterministic sort: name, version, config_hash, manifest_hash, job_id
    series.sort(
        key=lambda s: (
            s["chunker_name"],
            s["chunker_version"],
            s["chunker_config_hash"],
            s["chunk_manifest_hash"],
            s["job_id"],
        )
    )
    exclusions.sort(key=lambda e: (e.get("job_id") or "", e.get("quality_run_id") or 0))

    return {
        "source_snapshot_hash": source_snapshot_hash,
        "series": series,
        "exclusions": exclusions,
    }
