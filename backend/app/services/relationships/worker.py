"""Idempotent version-bound relationship observation build orchestration.

Scripts own source selection, packages, thresholds, state machine, and writes.
The LLM only supplies bounded semantic judgment fields. No Neo4j writes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import KnowledgeRelationJudgment
from app.core.database import async_session_factory
from app.models.relationship import (
    RelationshipBuildRun,
    RelationshipEvidenceLink,
    RelationshipObservation,
    RelationshipObservationCandidate,
    RelationshipObservationJudgment,
)
from app.services.relationships.candidates import (
    RelationshipCandidateDraft,
    RelationshipCandidateService,
    relationship_candidate_service,
)
from app.services.relationships.evidence import (
    RelationshipEvidencePackage,
    evidence_checksum_for,
    sha256_json,
)
from app.services.relationships.gates import (
    AUTO_ACCEPT_THRESHOLD,
    policy_hash,
    relationship_gate_service,
)
from app.services.relationships.judgment import (
    JudgmentCallResult,
    RelationshipJudgmentService,
    relationship_judgment_service,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WorkerRunResult:
    build_run_id: int
    status: str
    candidate_count: int = 0
    judgment_count: int = 0
    accepted_count: int = 0
    review_count: int = 0
    rejected_count: int = 0
    provider_calls: int = 0
    call_skipped: int = 0
    identity_reviews: int = 0
    rejections: list[dict[str, Any]] = field(default_factory=list)
    error_detail: str | None = None


class RelationshipObservationWorker:
    """Sole accepted-observation write path for Phase 09 pipeline."""

    def __init__(
        self,
        *,
        candidate_service: RelationshipCandidateService | None = None,
        judgment_service: RelationshipJudgmentService | None = None,
        chat_fn: Callable[..., Any] | None = None,
        model_name: str | None = None,
    ) -> None:
        self.candidate_service = candidate_service or relationship_candidate_service
        if judgment_service is not None:
            self.judgment_service = judgment_service
        else:
            self.judgment_service = RelationshipJudgmentService(
                chat_fn=chat_fn,
                model_name=model_name,
            )
        self.gate_service = relationship_gate_service
        self._policy_hash = policy_hash()

    async def run(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        analysis_version_id: int,
        build_run_id: int | None = None,
        deterministic_outputs: dict[str, dict[str, Any]] | None = None,
    ) -> WorkerRunResult:
        """Build candidates, judge, gate, and persist accepted observations.

        Network calls happen outside short persistence transactions (caller may
        share one session; we flush frequently and never write Neo4j).
        """

        build_run = await self._ensure_build_run(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            analysis_version_id=analysis_version_id,
            build_run_id=build_run_id,
        )
        build_run.status = "running"
        build_run.checkpoint = {**(build_run.checkpoint or {}), "phase": "select_sources"}
        await db.flush()

        try:
            selection = await self.candidate_service.select_and_build(
                db,
                owner_id=owner_id,
                novel_id=novel_id,
                analysis_version_id=analysis_version_id,
            )
        except Exception as exc:
            build_run.status = "failed"
            build_run.error_detail = f"candidate_selection_failed:{exc}"
            build_run.status_reason = "candidate_selection_failed"
            await db.flush()
            return WorkerRunResult(
                build_run_id=build_run.id,
                status="failed",
                error_detail=build_run.error_detail,
                rejections=[{"reason": str(exc)}],
            )

        provider_calls = 0
        call_skipped = 0
        det = deterministic_outputs or {}

        for draft in selection.drafts:
            candidate_row = await self._upsert_candidate(db, build_run=build_run, draft=draft)
            # Skip re-processing terminal candidates with existing judgments for same package.
            if candidate_row.status in {"accepted", "needs_human_review", "rejected"}:
                existing_j = await self._latest_judgment(db, candidate_id=candidate_row.id)
                if existing_j is not None:
                    continue

            candidate_row.status = "candidate"
            await db.flush()

            # Re-check source acceptance immediately before model call.
            source_ok = await self._source_still_accepted(
                db, source_judgment_id=draft.source_judgment_id
            )

            det_output = det.get(draft.package.candidate_key) or det.get(
                str(draft.source_judgment_id)
            )
            # Exact-cache / judge outside "DB lock" semantics: no long critical section.
            judge_result = await self.judgment_service.judge_package(
                draft.package,
                policy_hash_value=self._policy_hash,
                deterministic_output=det_output,
            )
            if judge_result.call_skipped or judge_result.cache_hit:
                call_skipped += 1
            elif not judge_result.call_skipped:
                provider_calls += 1
                if judge_result.repair_attempted:
                    provider_calls += 1

            candidate_row.status = "judged"
            await db.flush()

            judgment_row = await self._persist_judgment_audit(
                db,
                build_run=build_run,
                candidate=candidate_row,
                package=draft.package,
                judge_result=judge_result,
            )

            if judge_result.structured is None:
                judgment_row.status = judge_result.status
                judgment_row.gate_status = judge_result.gate_status
                judgment_row.gate_failures = list(judge_result.gate_failures)
                candidate_row.status = "rejected"
                await db.flush()
                continue

            interval = self.gate_service.interval_from_package(
                draft.package, judge_result.structured
            )
            idem_key = self.gate_service.build_idempotency_key(
                analysis_version_id=analysis_version_id,
                source_judgment_id=draft.source_judgment_id,
                source_character_id=draft.source_character_id,
                target_character_id=draft.target_character_id,
                relation_type=(
                    judge_result.structured.relation_type.value
                    if hasattr(judge_result.structured.relation_type, "value")
                    else str(judge_result.structured.relation_type)
                ),
                valid_from_chapter=interval["valid_from_chapter"],
                valid_from_narrative_index=interval["valid_from_narrative_index"],
                valid_to_chapter=interval["valid_to_chapter"],
                valid_to_narrative_index=interval["valid_to_narrative_index"],
                evidence_checksum=interval["evidence_checksum"],
                policy_hash_value=self._policy_hash,
            )

            existing_obs = await self._find_observation_by_key(db, idem_key)
            existing_keys = {idem_key} if existing_obs is not None else set()

            decision = self.gate_service.evaluate(
                package=draft.package,
                judgment=judge_result.structured,
                source_still_accepted=source_ok,
                fiction_domain=True,
                existing_idempotency_keys=existing_keys,
                proposed_idempotency_key=idem_key,
            )

            # Soft duplicate: reuse existing accepted observation (idempotent).
            if existing_obs is not None and decision.needs_review and any(
                f.startswith("soft:duplicate") for f in decision.gate_failures
            ):
                decision = type(decision)(
                    status="accepted",
                    gate_status="accepted",
                    gate_failures=[],
                    reason_codes=["idempotent_reuse"],
                    pipeline_status="accepted",
                )

            judgment_row.status = decision.status
            judgment_row.gate_status = decision.gate_status
            judgment_row.gate_failures = list(decision.gate_failures)
            candidate_row.status = "gated"
            await db.flush()

            if decision.accepted:
                if existing_obs is None:
                    await self._persist_observation(
                        db,
                        build_run=build_run,
                        candidate=candidate_row,
                        judgment=judgment_row,
                        package=draft.package,
                        judge_result=judge_result,
                        interval=interval,
                        idempotency_key=idem_key,
                    )
                candidate_row.status = "accepted"
            elif decision.needs_review:
                candidate_row.status = "needs_human_review"
            else:
                candidate_row.status = "rejected"

            await db.flush()

        await self._refresh_counts(db, build_run=build_run)
        build_run.status = "completed"
        build_run.checkpoint = {
            **(build_run.checkpoint or {}),
            "phase": "completed",
            "auto_accept_threshold": AUTO_ACCEPT_THRESHOLD,
            "policy_hash": self._policy_hash,
            "identity_reviews": len(selection.identity_reviews),
            "source_rejections": selection.rejections,
        }
        build_run.progress = {
            "provider_calls": provider_calls,
            "call_skipped": call_skipped,
            "identity_reviews": len(selection.identity_reviews),
        }
        await db.flush()

        return WorkerRunResult(
            build_run_id=build_run.id,
            status=build_run.status,
            candidate_count=build_run.candidate_count,
            judgment_count=build_run.judgment_count,
            accepted_count=build_run.accepted_count,
            review_count=build_run.review_count,
            rejected_count=build_run.rejected_count,
            provider_calls=provider_calls,
            call_skipped=call_skipped,
            identity_reviews=len(selection.identity_reviews),
            rejections=list(selection.rejections),
        )

    async def _ensure_build_run(
        self,
        db: AsyncSession,
        *,
        owner_id: int,
        novel_id: int,
        analysis_version_id: int,
        build_run_id: int | None,
    ) -> RelationshipBuildRun:
        if build_run_id is not None:
            run = await db.get(RelationshipBuildRun, build_run_id)
            if run is None:
                raise ValueError("build_run not found")
            if (
                run.owner_id != owner_id
                or run.novel_id != novel_id
                or run.analysis_version_id != analysis_version_id
            ):
                raise ValueError("build_run scope mismatch")
            return run

        run = RelationshipBuildRun(
            owner_id=owner_id,
            novel_id=novel_id,
            analysis_version_id=analysis_version_id,
            status="pending",
            checkpoint={},
            progress={},
            prompt_hash=self.judgment_service.prompt_hash,
            schema_hash=self.judgment_service.schema_hash,
            policy_hash=self._policy_hash,
            decoding_hash=self.judgment_service.decoding_hash,
            model_lineage={
                "model_name": self.judgment_service.resolve_model_name(),
                "policy_hash": self._policy_hash,
            },
        )
        db.add(run)
        await db.flush()
        return run

    async def _upsert_candidate(
        self,
        db: AsyncSession,
        *,
        build_run: RelationshipBuildRun,
        draft: RelationshipCandidateDraft,
    ) -> RelationshipObservationCandidate:
        result = await db.execute(
            select(RelationshipObservationCandidate).where(
                RelationshipObservationCandidate.analysis_version_id
                == draft.analysis_version_id,
                RelationshipObservationCandidate.package_hash == draft.package_hash,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        row = RelationshipObservationCandidate(
            owner_id=draft.owner_id,
            novel_id=draft.novel_id,
            analysis_version_id=draft.analysis_version_id,
            build_run_id=build_run.id,
            source_judgment_id=draft.source_judgment_id,
            source_relation_candidate_id=draft.source_relation_candidate_id,
            source_character_id=draft.source_character_id,
            target_character_id=draft.target_character_id,
            relation_type=draft.relation_type,
            package_hash=draft.package_hash,
            package_snapshot=draft.package.to_snapshot(),
            recall_signals=dict(draft.recall_signals or {}),
            evidence_refs=list(draft.evidence_refs),
            status="candidate",
        )
        db.add(row)
        await db.flush()
        return row

    async def _latest_judgment(
        self, db: AsyncSession, *, candidate_id: int
    ) -> RelationshipObservationJudgment | None:
        result = await db.execute(
            select(RelationshipObservationJudgment)
            .where(RelationshipObservationJudgment.candidate_id == candidate_id)
            .order_by(RelationshipObservationJudgment.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _source_still_accepted(
        self, db: AsyncSession, *, source_judgment_id: int
    ) -> bool:
        judgment = await db.get(KnowledgeRelationJudgment, source_judgment_id)
        if judgment is None:
            return False
        return judgment.status == "accepted" and judgment.gate_status == "accepted"

    async def _persist_judgment_audit(
        self,
        db: AsyncSession,
        *,
        build_run: RelationshipBuildRun,
        candidate: RelationshipObservationCandidate,
        package: RelationshipEvidencePackage,
        judge_result: JudgmentCallResult,
    ) -> RelationshipObservationJudgment:
        structured = judge_result.structured
        rel_type = (
            structured.relation_type.value
            if structured is not None and hasattr(structured.relation_type, "value")
            else (judge_result.relation_type or candidate.relation_type)
        )
        transition = (
            structured.transition.value
            if structured is not None and hasattr(structured.transition, "value")
            else (judge_result.transition or "uncertain")
        )
        confidence = (
            float(structured.confidence)
            if structured is not None
            else float(judge_result.confidence or 0.0)
        )
        valid_from = (
            structured.valid_from_evidence_id
            if structured is not None
            else (judge_result.valid_from_evidence_id or package.allowed_evidence_ids()[0])
        )
        valid_to = (
            structured.valid_to_evidence_id
            if structured is not None
            else judge_result.valid_to_evidence_id
        )
        supporting = (
            list(structured.supporting_evidence_ids)
            if structured is not None
            else list(judge_result.supporting_evidence_ids or package.allowed_evidence_ids())
        )

        row = RelationshipObservationJudgment(
            owner_id=candidate.owner_id,
            novel_id=candidate.novel_id,
            analysis_version_id=candidate.analysis_version_id,
            build_run_id=build_run.id,
            candidate_id=candidate.id,
            prompt_hash=judge_result.prompt_hash or self.judgment_service.prompt_hash,
            schema_hash=judge_result.schema_hash or self.judgment_service.schema_hash,
            policy_hash=self._policy_hash,
            model_name=judge_result.model_name or self.judgment_service.resolve_model_name(),
            model_lineage=dict(judge_result.model_lineage or {}),
            relation_type=rel_type,
            transition=transition if transition in {"establish", "change", "end", "uncertain"} else "uncertain",
            confidence=confidence,
            valid_from_evidence_id=valid_from,
            valid_to_evidence_id=valid_to,
            supporting_evidence_ids=supporting,
            structured_output=dict(judge_result.structured_output or {}),
            raw_output_hash=judge_result.raw_output_hash,
            rationale=judge_result.rationale,
            risk_flags=list(judge_result.risk_flags or []),
            status=judge_result.status,
            gate_status=judge_result.gate_status,
            gate_failures=list(judge_result.gate_failures or []),
            call_skipped=bool(judge_result.call_skipped or judge_result.cache_hit),
            latency_ms=judge_result.latency_ms,
            prompt_tokens=judge_result.prompt_tokens,
            completion_tokens=judge_result.completion_tokens,
            cost_usd=judge_result.cost_usd,
        )
        db.add(row)
        await db.flush()
        return row

    async def _find_observation_by_key(
        self, db: AsyncSession, idempotency_key: str
    ) -> RelationshipObservation | None:
        result = await db.execute(
            select(RelationshipObservation).where(
                RelationshipObservation.idempotency_key == idempotency_key
            )
        )
        return result.scalar_one_or_none()

    async def _persist_observation(
        self,
        db: AsyncSession,
        *,
        build_run: RelationshipBuildRun,
        candidate: RelationshipObservationCandidate,
        judgment: RelationshipObservationJudgment,
        package: RelationshipEvidencePackage,
        judge_result: JudgmentCallResult,
        interval: dict[str, Any],
        idempotency_key: str,
    ) -> RelationshipObservation:
        structured = judge_result.structured
        assert structured is not None
        rel_type = (
            structured.relation_type.value
            if hasattr(structured.relation_type, "value")
            else str(structured.relation_type)
        )
        transition = (
            structured.transition.value
            if hasattr(structured.transition, "value")
            else str(structured.transition)
        )
        if transition not in {"establish", "change", "end"}:
            raise ValueError("accepted observations cannot use uncertain transition")

        units = package.unit_by_id()
        supporting_units = [
            units[eid] for eid in structured.supporting_evidence_ids if eid in units
        ]
        evidence_checksum = interval["evidence_checksum"] or evidence_checksum_for(
            supporting_units
        )
        observation_checksum = sha256_json(
            {
                "idempotency_key": idempotency_key,
                "relation_type": rel_type,
                "transition": transition,
                "interval": interval,
                "evidence_checksum": evidence_checksum,
                "confidence": float(structured.confidence),
            }
        )

        obs = RelationshipObservation(
            owner_id=candidate.owner_id,
            novel_id=candidate.novel_id,
            analysis_version_id=candidate.analysis_version_id,
            build_run_id=build_run.id,
            candidate_id=candidate.id,
            judgment_id=judgment.id,
            source_judgment_id=candidate.source_judgment_id,
            source_character_id=candidate.source_character_id,
            target_character_id=candidate.target_character_id,
            relation_type=rel_type,
            transition=transition,
            status="accepted",
            valid_from_chapter=interval["valid_from_chapter"],
            valid_from_narrative_index=interval["valid_from_narrative_index"],
            valid_to_chapter=interval["valid_to_chapter"],
            valid_to_narrative_index=interval["valid_to_narrative_index"],
            valid_from_evidence_id=interval["valid_from_evidence_id"],
            valid_to_evidence_id=interval["valid_to_evidence_id"],
            confidence=float(structured.confidence),
            evidence_checksum=evidence_checksum,
            observation_checksum=observation_checksum,
            prompt_hash=judgment.prompt_hash,
            schema_hash=judgment.schema_hash,
            policy_hash=judgment.policy_hash,
            model_lineage=dict(judgment.model_lineage or {}),
            idempotency_key=idempotency_key,
        )
        db.add(obs)
        await db.flush()

        for order, unit in enumerate(supporting_units):
            db.add(
                RelationshipEvidenceLink(
                    observation_id=obs.id,
                    owner_id=candidate.owner_id,
                    novel_id=candidate.novel_id,
                    analysis_version_id=candidate.analysis_version_id,
                    evidence_id=unit.evidence_id,
                    chapter_id=unit.chapter_id,
                    source_start=unit.source_start,
                    source_end=unit.source_end,
                    content_hash=unit.content_hash,
                    excerpt=unit.excerpt,
                    sort_order=order,
                )
            )
        await db.flush()
        return obs

    async def _refresh_counts(
        self, db: AsyncSession, *, build_run: RelationshipBuildRun
    ) -> None:
        from sqlalchemy import func

        async def _count(model: Any, *clauses: Any) -> int:
            result = await db.execute(
                select(func.count()).select_from(model).where(*clauses)
            )
            return int(result.scalar_one())

        rid = build_run.id
        build_run.candidate_count = await _count(
            RelationshipObservationCandidate,
            RelationshipObservationCandidate.build_run_id == rid,
        )
        build_run.judgment_count = await _count(
            RelationshipObservationJudgment,
            RelationshipObservationJudgment.build_run_id == rid,
        )
        build_run.accepted_count = await _count(
            RelationshipObservationCandidate,
            RelationshipObservationCandidate.build_run_id == rid,
            RelationshipObservationCandidate.status == "accepted",
        )
        build_run.review_count = await _count(
            RelationshipObservationCandidate,
            RelationshipObservationCandidate.build_run_id == rid,
            RelationshipObservationCandidate.status == "needs_human_review",
        )
        build_run.rejected_count = await _count(
            RelationshipObservationCandidate,
            RelationshipObservationCandidate.build_run_id == rid,
            RelationshipObservationCandidate.status == "rejected",
        )


relationship_observation_worker = RelationshipObservationWorker()


async def dispatch_relationship_build(
    *, owner_id: int, novel_id: int, analysis_version_id: int
) -> None:
    """Run the version-bound relationship worker after timeline promotion."""
    async with async_session_factory() as session:
        try:
            await relationship_observation_worker.run(
                session,
                owner_id=owner_id,
                novel_id=novel_id,
                analysis_version_id=analysis_version_id,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("relationship build dispatch failed")
