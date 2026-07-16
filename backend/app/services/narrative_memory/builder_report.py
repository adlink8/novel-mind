"""Deterministic structural build report from PostgreSQL authority."""

from __future__ import annotations

from collections import Counter
from decimal import Decimal
from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.narrative_memory_builder import (
    NarrativeMemoryBuildBudgetLedger,
    NarrativeMemoryBuildModelCallAttempt,
    NarrativeMemoryBuildReport,
    NarrativeMemoryBuildRun,
    NarrativeMemoryBuildStage,
)
from app.services.narrative_memory.builder_contracts import BuildOutcome, _stable_json


def derive_outcome(run: NarrativeMemoryBuildRun, stages: list[NarrativeMemoryBuildStage]) -> str:
    if run.status == "cancelled" or run.cancel_requested and run.status != "completed":
        return BuildOutcome.CANCELLED.value
    if run.status in {"paused_budget", "paused_dependency"}:
        return BuildOutcome.PAUSED.value
    if run.status == "failed":
        return BuildOutcome.FAILED.value
    if run.status == "completed":
        return BuildOutcome.COMPLETED_CANDIDATE.value
    if any(s.status == "failed" for s in stages) or any(
        s.status == "blocked_dependency" for s in stages
    ):
        return BuildOutcome.PARTIAL.value
    if run.status == "partial":
        return BuildOutcome.PARTIAL.value
    return BuildOutcome.PARTIAL.value


async def build_report_body(
    session: AsyncSession,
    *,
    run_id: int,
    worker_artifact_checksum: str | None = None,
    database_manifest_checksum: str | None = None,
) -> dict[str, Any]:
    run = await session.get(NarrativeMemoryBuildRun, run_id)
    if run is None:
        raise ValueError("run not found")
    stages = list(
        (
            await session.scalars(
                select(NarrativeMemoryBuildStage)
                .where(NarrativeMemoryBuildStage.run_id == run_id)
                .order_by(NarrativeMemoryBuildStage.id.asc())
            )
        ).all()
    )
    attempts = list(
        (
            await session.scalars(
                select(NarrativeMemoryBuildModelCallAttempt)
                .where(NarrativeMemoryBuildModelCallAttempt.run_id == run_id)
                .order_by(NarrativeMemoryBuildModelCallAttempt.id.asc())
            )
        ).all()
    )
    ledger = await session.scalar(
        select(NarrativeMemoryBuildBudgetLedger).where(
            NarrativeMemoryBuildBudgetLedger.run_id == run_id
        )
    )
    stage_counts = dict(Counter(stage.status for stage in stages))
    transport_calls = sum(1 for a in attempts if a.status == "succeeded")
    cache_hits = sum(1 for a in attempts if a.status == "cache_hit")
    input_tokens = sum(int((a.usage or {}).get("input_tokens") or 0) for a in attempts)
    output_tokens = sum(int((a.usage or {}).get("output_tokens") or 0) for a in attempts)
    cost = sum((Decimal(a.cost_usd or 0) for a in attempts), Decimal("0"))
    if ledger is not None:
        settled_cost = Decimal(ledger.settled_cost_usd)
    else:
        settled_cost = cost
    blocked = [s.stage_key for s in stages if s.status == "blocked_dependency"]
    failed = [s.stage_key for s in stages if s.status == "failed"]
    outcome = derive_outcome(run, stages)
    body = {
        "outcome": outcome,
        "stage_counts": stage_counts,
        "dependency_closure": {
            "failed": failed,
            "blocked": blocked,
        },
        "call_totals": {
            "transport_calls": transport_calls,
            "cache_hits": cache_hits,
            "attempt_rows": len(attempts),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": str(settled_cost),
        },
        "source_statuses": (run.progress or {}).get("source_statuses") or {},
        "worker_artifact_checksum": worker_artifact_checksum,
        "database_manifest_checksum": database_manifest_checksum,
        "reason_codes": sorted(
            {
                *(s.status_reason for s in stages if s.status_reason),
                *(([run.status_reason] if run.status_reason else [])),
            }
        ),
    }
    return body


def report_checksum(body: dict[str, Any]) -> str:
    return sha256(_stable_json(body).encode("utf-8")).hexdigest()


async def write_build_report(
    session: AsyncSession,
    *,
    run_id: int,
    owner_id: int,
    novel_id: int,
    version_id: int,
    worker_artifact_checksum: str | None = None,
    database_manifest_checksum: str | None = None,
) -> NarrativeMemoryBuildReport:
    body = await build_report_body(
        session,
        run_id=run_id,
        worker_artifact_checksum=worker_artifact_checksum,
        database_manifest_checksum=database_manifest_checksum,
    )
    checksum = report_checksum(body)
    existing = await session.scalar(
        select(NarrativeMemoryBuildReport).where(
            NarrativeMemoryBuildReport.run_id == run_id,
            NarrativeMemoryBuildReport.report_checksum == checksum,
        )
    )
    if existing is not None:
        return existing
    row = NarrativeMemoryBuildReport(
        run_id=run_id,
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        outcome=body["outcome"],
        stage_counts=body["stage_counts"],
        dependency_closure=body["dependency_closure"],
        call_totals=body["call_totals"],
        source_statuses=body["source_statuses"],
        worker_artifact_checksum=worker_artifact_checksum,
        database_manifest_checksum=database_manifest_checksum,
        reason_codes=body["reason_codes"],
        report_checksum=checksum,
        body=body,
    )
    session.add(row)
    await session.flush()
    return row
