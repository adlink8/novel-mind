"""Deterministic gates for relation judgments before graph projection."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.knowledge import (
    RELATION_TYPES_BY_DOMAIN_PROFILE,
    KnowledgeEvidenceRef,
    KnowledgeExtractionRun,
    KnowledgeRelationCandidate,
    KnowledgeRelationJudgment,
    KnowledgeReviewQueue,
)
from app.models.novel import Chapter
from app.models.text_chunk import TextChunk


TERMINAL_JUDGMENT_STATUSES = {"accepted", "rejected", "needs_human_review"}


@dataclass(slots=True)
class GatePolicy:
    """Acceptance policy for deterministic gate routing."""

    auto_accept_min_confidence: float = 0.75


@dataclass(slots=True)
class GateDecision:
    """Result of deterministic gates for one judgment."""

    judgment_id: int
    status: str
    gate_status: str
    gate_failures: list[str] = field(default_factory=list)
    review_type: str | None = None
    review_reason: str | None = None

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"

    @property
    def rejected(self) -> bool:
        return self.status == "rejected"

    @property
    def needs_review(self) -> bool:
        return self.status == "needs_human_review"


class KnowledgeGateService:
    """Apply schema, evidence, threshold, and conflict gates."""

    async def gate_judgment(
        self,
        db: AsyncSession,
        *,
        judgment_id: int,
        policy: GatePolicy | None = None,
    ) -> GateDecision:
        """Evaluate one judgment and persist the resulting route."""

        judgment = await self._load_judgment(db, judgment_id)
        decision = await self.evaluate_judgment(
            db,
            judgment=judgment,
            policy=policy or GatePolicy(),
        )
        await self._apply_decision(db, judgment=judgment, decision=decision)
        await self._refresh_run_counts(db, run_id=judgment.run_id)
        await db.flush()
        return decision

    async def gate_run(
        self,
        db: AsyncSession,
        *,
        run_id: int,
        owner_id: int | None = None,
        policy: GatePolicy | None = None,
    ) -> dict[str, Any]:
        """Gate all judgments in a run with retry-safe status updates."""

        run = await db.get(KnowledgeExtractionRun, run_id)
        if run is None or (owner_id is not None and run.owner_id != owner_id):
            raise ValueError("Knowledge extraction run not found")

        run.status = "running"
        result = await db.execute(
            select(KnowledgeRelationJudgment.id)
            .where(KnowledgeRelationJudgment.run_id == run_id)
            .order_by(KnowledgeRelationJudgment.id.asc())
        )
        judgment_ids = list(result.scalars().all())

        decisions = []
        for judgment_id in judgment_ids:
            decisions.append(
                await self.gate_judgment(
                    db,
                    judgment_id=judgment_id,
                    policy=policy,
                )
            )

        await self._refresh_run_counts(db, run_id=run_id)
        return {
            "run_id": run_id,
            "processed": len(decisions),
            "accepted": sum(1 for item in decisions if item.accepted),
            "rejected": sum(1 for item in decisions if item.rejected),
            "needs_human_review": sum(1 for item in decisions if item.needs_review),
        }

    async def accept_reviewed_judgment(
        self,
        db: AsyncSession,
        *,
        judgment_id: int,
        reviewer_notes: str | None = None,
    ) -> GateDecision:
        """Manually accept a reviewed judgment without bypassing evidence gates."""

        judgment = await self._load_judgment(db, judgment_id)
        decision = await self.evaluate_judgment(
            db,
            judgment=judgment,
            policy=GatePolicy(auto_accept_min_confidence=0.0),
            manual_accept=True,
        )
        if decision.rejected:
            await self._apply_decision(db, judgment=judgment, decision=decision)
            await self._refresh_run_counts(db, run_id=judgment.run_id)
            await db.flush()
            return decision

        decision = GateDecision(
            judgment_id=judgment.id,
            status="accepted",
            gate_status="accepted",
        )
        await self._apply_decision(db, judgment=judgment, decision=decision)
        await self._resolve_review_items(
            db,
            judgment=judgment,
            resolution="accepted",
            reviewer_notes=reviewer_notes,
        )
        await self._refresh_run_counts(db, run_id=judgment.run_id)
        await db.flush()
        return decision

    async def reject_reviewed_judgment(
        self,
        db: AsyncSession,
        *,
        judgment_id: int,
        reviewer_notes: str | None = None,
    ) -> GateDecision:
        """Manually reject a judgment and close open review items."""

        judgment = await self._load_judgment(db, judgment_id)
        failures = list(judgment.gate_failures or [])
        if "manual_reject" not in failures:
            failures.append("manual_reject")
        decision = GateDecision(
            judgment_id=judgment.id,
            status="rejected",
            gate_status="rejected",
            gate_failures=failures,
        )
        await self._apply_decision(db, judgment=judgment, decision=decision)
        await self._resolve_review_items(
            db,
            judgment=judgment,
            resolution="rejected",
            reviewer_notes=reviewer_notes,
        )
        await self._refresh_run_counts(db, run_id=judgment.run_id)
        await db.flush()
        return decision

    async def evaluate_judgment(
        self,
        db: AsyncSession,
        *,
        judgment: KnowledgeRelationJudgment,
        policy: GatePolicy,
        manual_accept: bool = False,
    ) -> GateDecision:
        """Evaluate gates without mutating ORM state."""

        schema_failures = self._schema_failures(judgment)
        if schema_failures:
            return GateDecision(
                judgment_id=judgment.id,
                status="rejected",
                gate_status="rejected",
                gate_failures=schema_failures,
            )

        evidence_failures = await self._evidence_failures(db, judgment=judgment)
        if evidence_failures:
            return GateDecision(
                judgment_id=judgment.id,
                status="rejected",
                gate_status="rejected",
                gate_failures=evidence_failures,
            )

        if manual_accept:
            return GateDecision(
                judgment_id=judgment.id,
                status="accepted",
                gate_status="accepted",
            )

        review_failures = self._threshold_review_failures(judgment, policy)
        conflict_failures = await self._conflict_failures(db, judgment=judgment)
        review_failures.extend(conflict_failures)
        if review_failures:
            return GateDecision(
                judgment_id=judgment.id,
                status="needs_human_review",
                gate_status="needs_human_review",
                gate_failures=review_failures,
                review_type="gate_review",
                review_reason="; ".join(review_failures),
            )

        return GateDecision(
            judgment_id=judgment.id,
            status="accepted",
            gate_status="accepted",
        )

    def _schema_failures(self, judgment: KnowledgeRelationJudgment) -> list[str]:
        failures: list[str] = []
        candidate = judgment.candidate
        if candidate is None:
            return ["missing_relation_candidate"]

        if judgment.status in {"schema_failed", "blocked"}:
            failures.append(f"schema_status:{judgment.status}")
        if judgment.gate_status == "schema_failed":
            failures.append("schema_gate_failed")
        if not judgment.relation_type:
            failures.append("missing_relation_type")
        if not judgment.prompt_version:
            failures.append("missing_prompt_version")
        if not judgment.model_name:
            failures.append("missing_model_name")
        if not judgment.evidence_refs:
            failures.append("missing_evidence_refs")
        if judgment.confidence < 0 or judgment.confidence > 1:
            failures.append("confidence_out_of_range")

        if (
            judgment.owner_id != candidate.owner_id
            or judgment.novel_id != candidate.novel_id
            or judgment.run_id != candidate.run_id
        ):
            failures.append("candidate_scope_mismatch")

        allowed_types = RELATION_TYPES_BY_DOMAIN_PROFILE.get(
            candidate.domain_profile,
            (),
        )
        if judgment.relation_type not in allowed_types:
            failures.append(f"relation_type_not_allowed:{judgment.relation_type}")

        return failures

    async def _evidence_failures(
        self,
        db: AsyncSession,
        *,
        judgment: KnowledgeRelationJudgment,
    ) -> list[str]:
        candidate = judgment.candidate
        if candidate is None:
            return ["missing_relation_candidate"]

        refs = [str(ref) for ref in judgment.evidence_refs or []]
        if not refs:
            return ["missing_evidence_refs"]

        allowed_refs = self._allowed_refs(candidate)
        out_of_package = sorted(set(refs) - allowed_refs)
        if out_of_package:
            return [
                f"out_of_package_evidence:{','.join(out_of_package)}",
            ]

        result = await db.execute(
            select(KnowledgeEvidenceRef).where(
                KnowledgeEvidenceRef.owner_id == judgment.owner_id,
                KnowledgeEvidenceRef.novel_id == judgment.novel_id,
                KnowledgeEvidenceRef.run_id == judgment.run_id,
                KnowledgeEvidenceRef.ref_key.in_(refs),
            )
        )
        evidence_by_key = {item.ref_key: item for item in result.scalars().all()}
        missing = sorted(set(refs) - set(evidence_by_key))
        if missing:
            return [f"missing_evidence:{','.join(missing)}"]

        locator_failures = await self._source_locator_failures(
            db,
            evidence_refs=list(evidence_by_key.values()),
        )
        return locator_failures

    def _allowed_refs(self, candidate: KnowledgeRelationCandidate) -> set[str]:
        snapshot = candidate.package_snapshot or {}
        allowed = set(str(ref) for ref in candidate.evidence_refs or [])
        allowed.update(str(ref) for ref in snapshot.get("allowed_evidence_ids", []))
        allowed.update(str(ref) for ref in snapshot.get("allowed_evidence_refs", []))
        candidate_payload = snapshot.get("candidate") or {}
        allowed.update(str(ref) for ref in candidate_payload.get("evidence_refs", []))
        return {ref for ref in allowed if ref}

    async def _source_locator_failures(
        self,
        db: AsyncSession,
        *,
        evidence_refs: list[KnowledgeEvidenceRef],
    ) -> list[str]:
        failures: list[str] = []
        chunk_refs = [ref for ref in evidence_refs if ref.source_type == "text_chunk"]
        chapter_refs = [ref for ref in evidence_refs if ref.source_type == "chapter"]
        unsupported_refs = [
            ref.ref_key
            for ref in evidence_refs
            if ref.source_type not in {"text_chunk", "chapter"}
        ]
        if unsupported_refs:
            failures.append(
                f"unsupported_evidence_source:{','.join(sorted(unsupported_refs))}"
            )

        chunk_ids = [ref.text_chunk_id for ref in chunk_refs if ref.text_chunk_id]
        if len(chunk_ids) != len(chunk_refs):
            failures.append("text_chunk_evidence_missing_locator")
        if chunk_ids:
            result = await db.execute(
                select(TextChunk.id, TextChunk.novel_id).where(
                    TextChunk.id.in_(chunk_ids)
                )
            )
            chunks = {row.id: row.novel_id for row in result.fetchall()}
            for ref in chunk_refs:
                if ref.text_chunk_id not in chunks:
                    failures.append(f"text_chunk_missing:{ref.ref_key}")
                elif chunks[ref.text_chunk_id] != ref.novel_id:
                    failures.append(f"text_chunk_scope_mismatch:{ref.ref_key}")

        chapter_ids = [ref.chapter_id for ref in chapter_refs if ref.chapter_id]
        if len(chapter_ids) != len(chapter_refs):
            failures.append("chapter_evidence_missing_locator")
        if chapter_ids:
            result = await db.execute(
                select(Chapter.id, Chapter.novel_id).where(Chapter.id.in_(chapter_ids))
            )
            chapters = {row.id: row.novel_id for row in result.fetchall()}
            for ref in chapter_refs:
                if ref.chapter_id not in chapters:
                    failures.append(f"chapter_missing:{ref.ref_key}")
                elif chapters[ref.chapter_id] != ref.novel_id:
                    failures.append(f"chapter_scope_mismatch:{ref.ref_key}")

        return failures

    def _threshold_review_failures(
        self,
        judgment: KnowledgeRelationJudgment,
        policy: GatePolicy,
    ) -> list[str]:
        failures: list[str] = []
        if judgment.confidence < policy.auto_accept_min_confidence:
            failures.append(f"low_confidence:{judgment.confidence:.2f}")
        if judgment.risk_flags:
            failures.append(f"risk_flags:{','.join(judgment.risk_flags)}")
        if judgment.needs_human_review:
            failures.append("llm_requested_human_review")
        return failures

    async def _conflict_failures(
        self,
        db: AsyncSession,
        *,
        judgment: KnowledgeRelationJudgment,
    ) -> list[str]:
        candidate = judgment.candidate
        if candidate is None:
            return ["missing_relation_candidate"]

        result = await db.execute(
            select(KnowledgeRelationJudgment)
            .join(
                KnowledgeRelationCandidate,
                KnowledgeRelationCandidate.id
                == KnowledgeRelationJudgment.relation_candidate_id,
            )
            .where(
                KnowledgeRelationJudgment.id != judgment.id,
                KnowledgeRelationJudgment.owner_id == judgment.owner_id,
                KnowledgeRelationJudgment.novel_id == judgment.novel_id,
                KnowledgeRelationJudgment.status == "accepted",
                KnowledgeRelationJudgment.gate_status == "accepted",
                KnowledgeRelationCandidate.source_kind == candidate.source_kind,
                KnowledgeRelationCandidate.source_id == candidate.source_id,
                KnowledgeRelationCandidate.target_kind == candidate.target_kind,
                KnowledgeRelationCandidate.target_id == candidate.target_id,
                KnowledgeRelationJudgment.relation_type != judgment.relation_type,
            )
        )
        conflicts = result.scalars().all()
        if not conflicts:
            return []
        conflict_ids = ",".join(str(item.id) for item in conflicts)
        return [f"conflicting_accepted_judgment:{conflict_ids}"]

    async def _apply_decision(
        self,
        db: AsyncSession,
        *,
        judgment: KnowledgeRelationJudgment,
        decision: GateDecision,
    ) -> None:
        judgment.status = decision.status
        judgment.gate_status = decision.gate_status
        judgment.gate_failures = list(decision.gate_failures)
        judgment.needs_human_review = decision.needs_review

        if judgment.candidate is not None:
            judgment.candidate.status = decision.status

        if decision.needs_review:
            await self._ensure_review_item(
                db,
                judgment=judgment,
                review_type=decision.review_type or "gate_review",
                reason=decision.review_reason or "Judgment requires human review.",
            )

    async def _ensure_review_item(
        self,
        db: AsyncSession,
        *,
        judgment: KnowledgeRelationJudgment,
        review_type: str,
        reason: str,
    ) -> KnowledgeReviewQueue:
        result = await db.execute(
            select(KnowledgeReviewQueue).where(
                KnowledgeReviewQueue.judgment_id == judgment.id,
                KnowledgeReviewQueue.status.in_(("open", "in_review")),
            )
        )
        review_item = result.scalar_one_or_none()
        if review_item is None:
            review_item = KnowledgeReviewQueue(
                owner_id=judgment.owner_id,
                novel_id=judgment.novel_id,
                run_id=judgment.run_id,
                relation_candidate_id=judgment.relation_candidate_id,
                judgment_id=judgment.id,
                review_type=review_type,
                reason=reason,
                evidence_refs=list(judgment.evidence_refs or []),
            )
            db.add(review_item)
        else:
            review_item.review_type = review_type
            review_item.reason = reason
            review_item.evidence_refs = list(judgment.evidence_refs or [])
        return review_item

    async def _resolve_review_items(
        self,
        db: AsyncSession,
        *,
        judgment: KnowledgeRelationJudgment,
        resolution: str,
        reviewer_notes: str | None,
    ) -> None:
        result = await db.execute(
            select(KnowledgeReviewQueue).where(
                KnowledgeReviewQueue.judgment_id == judgment.id,
                KnowledgeReviewQueue.status.in_(("open", "in_review")),
            )
        )
        for review_item in result.scalars().all():
            review_item.status = "resolved"
            review_item.resolution = resolution
            review_item.reviewer_notes = reviewer_notes

    async def _refresh_run_counts(self, db: AsyncSession, *, run_id: int) -> None:
        run = await db.get(KnowledgeExtractionRun, run_id)
        if run is None:
            return

        run.candidate_count = await self._count(
            db,
            KnowledgeRelationCandidate,
            KnowledgeRelationCandidate.run_id == run_id,
        )
        run.judgment_count = await self._count(
            db,
            KnowledgeRelationJudgment,
            KnowledgeRelationJudgment.run_id == run_id,
        )
        run.accepted_count = await self._count(
            db,
            KnowledgeRelationJudgment,
            KnowledgeRelationJudgment.run_id == run_id,
            KnowledgeRelationJudgment.status == "accepted",
        )
        run.rejected_count = await self._count(
            db,
            KnowledgeRelationJudgment,
            KnowledgeRelationJudgment.run_id == run_id,
            KnowledgeRelationJudgment.status == "rejected",
        )
        run.review_count = await self._count(
            db,
            KnowledgeReviewQueue,
            KnowledgeReviewQueue.run_id == run_id,
            KnowledgeReviewQueue.status.in_(("open", "in_review")),
        )
        pending_count = await self._count(
            db,
            KnowledgeRelationJudgment,
            KnowledgeRelationJudgment.run_id == run_id,
            KnowledgeRelationJudgment.status.notin_(TERMINAL_JUDGMENT_STATUSES),
        )
        if pending_count == 0 and run.status in {"pending", "running"}:
            run.status = "completed"

    async def _count(self, db: AsyncSession, model: Any, *clauses: Any) -> int:
        result = await db.execute(
            select(func.count()).select_from(model).where(*clauses)
        )
        return int(result.scalar_one())

    async def _load_judgment(
        self,
        db: AsyncSession,
        judgment_id: int,
    ) -> KnowledgeRelationJudgment:
        result = await db.execute(
            select(KnowledgeRelationJudgment)
            .options(selectinload(KnowledgeRelationJudgment.candidate))
            .where(KnowledgeRelationJudgment.id == judgment_id)
        )
        judgment = result.scalar_one_or_none()
        if judgment is None:
            raise ValueError("Knowledge relation judgment not found")
        return judgment


knowledge_gate_service = KnowledgeGateService()
