"""Append-only persistence for Phase 17 qualification audit rows."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.narrative_memory_qualification import (
    NarrativeMemoryQualificationCaseResult,
    NarrativeMemoryQualificationReport,
    NarrativeMemoryQualificationRun,
)
from app.services.narrative_memory.qualification_contracts import (
    QualificationFixture,
    QualificationPolicy,
    QualificationReport,
    stable_checksum,
)


class QualificationRepositoryError(ValueError):
    pass


async def create_run(
    session: AsyncSession,
    *,
    fixture: QualificationFixture,
    policy: QualificationPolicy,
    pointer_before_digest: str,
    lineage: dict[str, Any] | None = None,
) -> NarrativeMemoryQualificationRun:
    run = NarrativeMemoryQualificationRun(
        owner_id=fixture.owner_id,
        novel_id=fixture.novel_id,
        version_id=fixture.version_id,
        status="running",
        fixture_checksum=fixture.checksum(),
        policy_checksum=policy.checksum(),
        source_snapshot_hash=fixture.source_snapshot_hash,
        hierarchy_build_id=fixture.hierarchy_build_id,
        hierarchy_checksum=fixture.hierarchy_checksum,
        candidate_manifest_checksum=fixture.candidate_manifest_checksum,
        generator_lineage=policy.generator.model_dump(mode="json"),
        judge_lineage=policy.judge.model_dump(mode="json"),
        pricing_checksum=stable_checksum(policy.price.model_dump(mode="json")),
        budget_checksum=stable_checksum(policy.budget.model_dump(mode="json")),
        pointer_before_digest=pointer_before_digest,
        lineage=lineage or {},
    )
    existing = await session.scalar(
        select(NarrativeMemoryQualificationRun).where(
            NarrativeMemoryQualificationRun.owner_id == fixture.owner_id,
            NarrativeMemoryQualificationRun.novel_id == fixture.novel_id,
            NarrativeMemoryQualificationRun.version_id == fixture.version_id,
            NarrativeMemoryQualificationRun.fixture_checksum == fixture.checksum(),
            NarrativeMemoryQualificationRun.policy_checksum == policy.checksum(),
        )
    )
    if existing is not None:
        return existing
    session.add(run)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise QualificationRepositoryError("run identity conflict") from exc
    return run


async def insert_case_result(
    session: AsyncSession,
    *,
    run: NarrativeMemoryQualificationRun,
    artifact: dict[str, Any],
) -> NarrativeMemoryQualificationCaseResult:
    # Sanitize: never store answer text that might include spoilers in public path
    safe = {
        k: v
        for k, v in artifact.items()
        if k not in {"answer"}  # keep answer out of durable public fields optional
    }
    # store answer hash only
    if "answer" in artifact:
        safe["answer_checksum"] = stable_checksum({"answer": artifact.get("answer")})
    art_cs = stable_checksum(safe)
    usage_cs = stable_checksum(
        {
            "calls": artifact.get("calls"),
            "input_tokens": artifact.get("input_tokens"),
            "output_tokens": artifact.get("output_tokens"),
            "cost_usd": artifact.get("cost_usd"),
        }
    )
    row = NarrativeMemoryQualificationCaseResult(
        owner_id=run.owner_id,
        novel_id=run.novel_id,
        run_id=run.id,
        case_key=artifact["case_key"],
        strategy=artifact["strategy"],
        bucket=artifact["bucket"],
        artifact_checksum=art_cs,
        usage_checksum=usage_cs,
        sanitized_reasons=[],
        artifact=safe,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise QualificationRepositoryError(
            f"duplicate case result {artifact['case_key']}/{artifact['strategy']}"
        ) from exc
    return row


async def seal_report(
    session: AsyncSession,
    *,
    run: NarrativeMemoryQualificationRun,
    report: QualificationReport,
    metric_payload_checksum: str,
    verifier_checksum: str,
    pointer_after_digest: str,
    command_payload: dict[str, Any],
    output_digest: str,
) -> NarrativeMemoryQualificationReport:
    existing = await session.scalar(
        select(NarrativeMemoryQualificationReport).where(
            NarrativeMemoryQualificationReport.run_id == run.id
        )
    )
    if existing is not None:
        return existing

    body = report.model_dump(mode="json")
    row = NarrativeMemoryQualificationReport(
        owner_id=run.owner_id,
        novel_id=run.novel_id,
        run_id=run.id,
        qualification_kind="single_book_candidate",
        verdict=report.verdict.value,
        reason_codes=list(report.reason_codes),
        metric_payload_checksum=metric_payload_checksum,
        verifier_checksum=verifier_checksum,
        pointer_after_digest=pointer_after_digest,
        command_payload_checksum=stable_checksum(command_payload),
        output_digest=output_digest,
        disclaimer=report.disclaimer,
        report_body=body,
        sealed_at=datetime.now(timezone.utc),
    )
    session.add(row)
    # status is append-only on run — insert-only means we cannot UPDATE run.
    # Store terminal status only via initial create when known, or accept running
    # with sealed report as authority. For tests that need completed status without
    # UPDATE, create_run may set status via constructor only.
    await session.flush()
    return row


async def get_report_for_run(
    session: AsyncSession, run_id: int
) -> NarrativeMemoryQualificationReport | None:
    return await session.scalar(
        select(NarrativeMemoryQualificationReport).where(
            NarrativeMemoryQualificationReport.run_id == run_id
        )
    )


def repository_has_promotion_capability() -> bool:
    return False


def repository_has_active_pointer() -> bool:
    return False
