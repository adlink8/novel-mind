"""Illustration review, compare and approval workflow (Phase 33-04, REQ-VIS-04).

D-33-03 / D-33-04: approval is an explicit, append-only human/machine action
that only moves a candidate asset's approval projection to ``proposal_ready``
(Phase 34 owns publish; nothing here becomes reader visible). This module owns
the *review seam* for the generation candidate gallery / compare / explicit
approval-reject-supersede-retry workflow:

- ``evaluate_illustration_proposal_gate`` — the pure, replayable fail-closed
  proposal gate: a candidate cannot become ``proposal_ready`` unless its job
  succeeded, its source/prompt/model/asset lineage is complete, its rights
  status is cleared, its job carries settled durable budget evidence and a
  visible consistency report exists. Missing evidence fails closed with a
  stable reason code.
- ``IllustrationReviewService.append_event`` — server-side gated, append-only,
  idempotent review actions (``approve`` / ``reject`` / ``supersede`` /
  ``needs_relink``). A repeated ``event_key`` replays the existing state and
  never appends a second row; approvals re-run the proposal gate before any
  row is written; the asset row's ``approval_state`` / ``approved_by`` are the
  only mutable projections (everything else fails closed).
- ``build_review_envelope`` / ``build_gallery`` — owner-scoped read envelopes
  for the gallery / lineage drawer / compare (candidate asset + job/attempt/
  budget evidence + consistency report + review history + approval gate).
- ``build_proposal_ref`` — the ``FrozenAssetRevisionView`` Phase 34 can
  consume for ``proposal_ready`` assets (never auto-created, D-33-03).

No ``publish`` action exists in the review vocabulary: Phase 33 ends at
``proposal_ready`` and nothing here switches an active pointer or becomes
reader/export visible. Job ``retry`` stays on the durable job service
(33-02); the gallery surfaces failed/unknown jobs with their retry status so
a reviewer can recover explicitly instead of an empty success.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.illustration import AssetRevision, ConsistencyReport
from app.models.illustration_job import (
    IllustrationAttempt,
    IllustrationBudgetLedger,
    IllustrationBudgetReservation,
    IllustrationJob,
    IllustrationReviewEvent,
)
from app.schemas.illustration import (
    AssetRevisionView,
    FrozenAssetRevisionView,
    IllustrationApprovalState,
    IllustrationGateError,
    IllustrationJobView,
    IllustrationReviewAction,
    IllustrationReviewEventInput,
    IllustrationRightsStatus,
    StrictIllustrationModel,
    approval_state_after,
)
from app.services.illustrations.consistency import ConsistencyReportView, report_view

# Lineage fields that must be complete (64-hex hashes) before a candidate may
# become proposal_ready (D-33-01/03: replayable source/prompt/model/asset
# lineage is the gate, not the client's word).
_LINEAGE_HEX64_FIELDS = (
    "scene_spec_hash",
    "prompt_revision_hash",
    "visual_bible_revision_hash",
    "source_snapshot_hash",
    "config_hash",
)

# Only ``cleared`` resolves rights for approval; unreviewed/pending/denied all
# leave a generated candidate unable to silently become canon (D-33-03).
_RIGHTS_BLOCKED_FOR_APPROVAL: frozenset[str] = frozenset(
    {
        IllustrationRightsStatus.UNREVIEWED.value,
        IllustrationRightsStatus.PENDING.value,
        IllustrationRightsStatus.DENIED.value,
    }
)


def _is_hex64(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


# ---------------------------------------------------------------------------
# Pure, replayable proposal gate (unit-testable without a database)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IllustrationProposalGateResult:
    """Fail-closed proposal readiness with a stable machine reason code."""

    ok: bool
    reason_code: str | None = None
    detail: str | None = None


def evaluate_illustration_proposal_gate(
    *,
    job_status: str,
    rights_status: str,
    budget_settled: bool,
    has_consistency_report: bool,
    lineage: Mapping[str, object] | None = None,
) -> IllustrationProposalGateResult:
    """Fail closed unless every proposal-ready precondition holds.

    - the asset's durable job must be ``succeeded`` (a failed/paused/unknown
      job can never yield a proposal);
    - rights must be ``cleared`` (unreviewed/pending/denied stays a candidate);
    - the job must carry a settled budget reservation (durable cost evidence,
      D-33-02);
    - a visible consistency report must exist (review signal, D-33-04);
    - the source/prompt/model/asset lineage must be complete (no missing or
      malformed hash, snapshot id or cutoff).
    """
    if job_status != "succeeded":
        return IllustrationProposalGateResult(
            ok=False,
            reason_code="job_not_succeeded",
            detail=(
                f"job status {job_status!r}; a proposal requires a succeeded "
                "generation job"
            ),
        )
    if rights_status != IllustrationRightsStatus.CLEARED.value:
        return IllustrationProposalGateResult(
            ok=False,
            reason_code="rights_unresolved",
            detail=(
                f"asset rights_status {rights_status!r}; rights must be cleared "
                "before proposal_ready"
            ),
        )
    if not budget_settled:
        return IllustrationProposalGateResult(
            ok=False,
            reason_code="budget_unsettled",
            detail="no settled budget reservation for the job (cost evidence missing)",
        )
    if not has_consistency_report:
        return IllustrationProposalGateResult(
            ok=False,
            reason_code="consistency_missing",
            detail=(
                "no visible consistency report for the candidate (review signal "
                "missing, D-33-04)"
            ),
        )
    if lineage is not None:
        missing = [
            key
            for key in _LINEAGE_HEX64_FIELDS
            if not _is_hex64(str(lineage.get(key) or ""))
        ]
        if not str(lineage.get("source_snapshot_id") or "").strip():
            missing.append("source_snapshot_id")
        try:
            if int(lineage.get("cutoff_chapter", -1)) < 1:
                missing.append("cutoff_chapter")
        except (TypeError, ValueError):
            missing.append("cutoff_chapter")
        if missing:
            return IllustrationProposalGateResult(
                ok=False,
                reason_code="lineage_incomplete",
                detail=f"incomplete source/prompt/model/asset lineage: {sorted(missing)}",
            )
    return IllustrationProposalGateResult(ok=True)


# ---------------------------------------------------------------------------
# Read envelopes (owner-scoped, candidate-only, no canon exposure)
# ---------------------------------------------------------------------------


class IllustrationReviewEventView(StrictIllustrationModel):
    """Append-only review history entry."""

    event_key: str
    action: IllustrationReviewAction
    actor_source: str
    actor: str
    reason: str
    from_approval_state: IllustrationApprovalState
    to_approval_state: IllustrationApprovalState


class IllustrationProposalGateView(StrictIllustrationModel):
    """Approval readiness surfaced on the gallery/envelope with a stable code."""

    ok: bool
    reason_code: str | None = None
    detail: str | None = None


class IllustrationAttemptView(StrictIllustrationModel):
    """One auditable provider call attempt (request/response hash, cost)."""

    id: int
    attempt_number: int
    status: str
    provider_request_id: str | None = None
    request_hash: str
    response_hash: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    cost_usd: Decimal | None = None
    latency_ms: int | None = None
    error_code: str | None = None


class IllustrationBudgetEvidenceView(StrictIllustrationModel):
    """Durable budget/cost evidence for one job (D-33-02, never silent)."""

    settled_calls: int
    settled_cost_usd: Decimal | None = None
    reservation_status: str
    settled_usage: dict[str, Any] = Field(default_factory=dict)
    price_snapshot: dict[str, Any] = Field(default_factory=dict)
    ledger_max_calls: int | None = None
    ledger_max_cost_usd: Decimal | None = None


class IllustrationGalleryItemView(StrictIllustrationModel):
    """One gallery card: candidate asset + job status + review signals."""

    asset: AssetRevisionView
    job: IllustrationJobView
    consistency: ConsistencyReportView | None = None
    review_events: list[IllustrationReviewEventView] = Field(default_factory=list)
    approval_gate: IllustrationProposalGateView | None = None


class IllustrationGalleryResponse(StrictIllustrationModel):
    items: list[IllustrationGalleryItemView]
    total: int


class IllustrationReviewEnvelope(StrictIllustrationModel):
    """Full review envelope: lineage drawer + compare + history + gate."""

    asset: AssetRevisionView
    job: IllustrationJobView
    attempts: list[IllustrationAttemptView] = Field(default_factory=list)
    budget: IllustrationBudgetEvidenceView | None = None
    consistency: ConsistencyReportView | None = None
    review_events: list[IllustrationReviewEventView] = Field(default_factory=list)
    approval_gate: IllustrationProposalGateView | None = None


class IllustrationReviewActionResponse(StrictIllustrationModel):
    """One explicit review action result; the server decides the transition."""

    asset: AssetRevisionView
    envelope: IllustrationReviewEnvelope


class IllustrationReviewNotFound(ValueError):
    """An asset is outside the explicit owner/novel scope (404-equivalent)."""


class IllustrationReviewGateError(ValueError):
    """A fail-closed gate violation while appending a review action."""


# ---------------------------------------------------------------------------
# Server-side review service (gates + append-only + idempotent)
# ---------------------------------------------------------------------------


class IllustrationReviewService:
    """Owner-scoped review seam: gated, append-only, explicit review actions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append_event(
        self,
        *,
        owner_id: int,
        novel_id: int,
        event: IllustrationReviewEventInput,
    ) -> AssetRevision:
        """Append one review action after server-side scope/gate checks.

        A repeated ``event_key`` replays the existing state (no second event, no
        projection change). Approvals re-run the proposal gate against persisted
        rows and fail closed with a stable reason code; reject/supersede/
        needs_relink are recorded with the same durable evidence details so the
        audit trail stays replayable. The asset's ``approval_state`` is the only
        mutable projection (D-33-03).
        """
        self._require_scope(owner_id=owner_id, novel_id=novel_id)
        if event.owner_id != owner_id or event.novel_id != novel_id:
            raise IllustrationReviewGateError(
                "review event scope does not match request scope"
            )

        asset = await self._asset(owner_id, novel_id, event.asset_revision_id)
        if asset is None:
            raise IllustrationReviewNotFound(
                "illustration asset not found in the owner/novel scope"
            )

        existing = await self._event(owner_id, novel_id, asset.id, event.event_key)
        if existing is not None:
            return asset  # idempotent replay: no second event, no state change

        job = await self._job(owner_id, novel_id, asset.job_id)
        if job is None:
            raise IllustrationReviewGateError(
                "asset generation job not found in the owner/novel scope"
            )

        try:
            to_state = approval_state_after(event.from_approval_state, event.action)
        except IllustrationGateError as exc:
            raise IllustrationReviewGateError(str(exc)) from exc

        if asset.approval_state != event.from_approval_state.value:
            raise IllustrationReviewGateError(
                f"from_approval_state {event.from_approval_state.value!r} does not "
                f"match the asset's current approval_state {asset.approval_state!r}"
            )

        gate: IllustrationProposalGateResult | None = None
        if event.action is IllustrationReviewAction.APPROVE:
            gate = await self._proposal_gate(owner_id, novel_id, asset, job)
            if not gate.ok:
                raise IllustrationReviewGateError(
                    f"proposal_ready blocked by {gate.reason_code}: {gate.detail}"
                )

        details = await self._event_details(owner_id, novel_id, asset, job, gate)
        return await self._append_event_row(
            owner_id=owner_id,
            novel_id=novel_id,
            asset=asset,
            event=event,
            to_state=to_state,
            details=details,
        )

    # ------------------------------------------------------------------ gates

    async def _proposal_gate(
        self,
        owner_id: int,
        novel_id: int,
        asset: AssetRevision,
        job: IllustrationJob,
    ) -> IllustrationProposalGateResult:
        """Re-run the fail-closed proposal gate against persisted rows."""
        return evaluate_illustration_proposal_gate(
            job_status=job.status,
            rights_status=asset.rights_status,
            budget_settled=await self._has_settled_budget(job.id),
            has_consistency_report=await self._has_consistency_report(
                owner_id, novel_id, asset.id
            ),
            lineage={
                "scene_spec_hash": asset.scene_spec_hash,
                "prompt_revision_hash": asset.prompt_revision_hash,
                "visual_bible_revision_hash": asset.visual_bible_revision_hash,
                "source_snapshot_id": asset.source_snapshot_id,
                "source_snapshot_hash": asset.source_snapshot_hash,
                "cutoff_chapter": asset.cutoff_chapter,
                "config_hash": asset.config_hash,
            },
        )

    async def _event_details(
        self,
        owner_id: int,
        novel_id: int,
        asset: AssetRevision,
        job: IllustrationJob,
        gate: IllustrationProposalGateResult | None,
    ) -> dict[str, Any]:
        """Explicit audit payload: budget evidence, lineage, rights, gate.

        Budget/cost evidence and the consistency report are persisted
        explicitly (never silently dropped); unknown/missing evidence stays
        explicit in the details payload (must-have: no silent degradation).
        """
        budget = await self._budget_evidence(job.id)
        consistency = await self._latest_report(owner_id, novel_id, asset.id)
        return {
            "budget": (
                None
                if budget is None
                else {
                    "status": "settled",
                    "settled_calls": budget["settled_calls"],
                    "settled_cost_usd": budget["settled_cost_usd"],
                    "reservation_status": budget["reservation_status"],
                    "price_snapshot": budget["price_snapshot"],
                }
            ),
            "lineage": {
                "scene_spec_hash": asset.scene_spec_hash,
                "prompt_revision_id": asset.prompt_revision_id,
                "prompt_revision_hash": asset.prompt_revision_hash,
                "visual_bible_revision_hash": asset.visual_bible_revision_hash,
                "source_snapshot_id": asset.source_snapshot_id,
                "source_snapshot_hash": asset.source_snapshot_hash,
                "cutoff_chapter": asset.cutoff_chapter,
                "config_hash": asset.config_hash,
                "model_lineage": dict(asset.model_lineage or {}),
            },
            "rights": {
                "rights_status": asset.rights_status,
                "approved_by": asset.approved_by,
            },
            "consistency": (
                None
                if consistency is None
                else {
                    "report_key": consistency.report_key,
                    "verdict": consistency.verdict,
                    "fixture_set_hash": consistency.fixture_set_hash,
                }
            ),
            "approval_gate": (
                None
                if gate is None
                else {
                    "ok": gate.ok,
                    "reason_code": gate.reason_code,
                    "detail": gate.detail,
                }
            ),
        }

    # ------------------------------------------------------------ persistence

    async def _append_event_row(
        self,
        *,
        owner_id: int,
        novel_id: int,
        asset: AssetRevision,
        event: IllustrationReviewEventInput,
        to_state: IllustrationApprovalState,
        details: dict[str, Any],
    ) -> AssetRevision:
        """Append one append-only event row; move the approval projection.

        Only ``approval_state`` / ``approved_by`` may move on the asset row
        (the model's append-only guard rejects every other mutation). A
        concurrent duplicate ``event_key`` rolls back and replays the existing
        state.
        """
        row = IllustrationReviewEvent(
            owner_id=owner_id,
            novel_id=novel_id,
            asset_revision_id=asset.id,
            action=event.action.value,
            actor_source=event.actor_source.value,
            actor=event.actor,
            reason=event.reason,
            event_key=event.event_key,
            from_approval_state=event.from_approval_state.value,
            to_approval_state=to_state.value,
            details=details,
        )
        self._session.add(row)
        asset.approval_state = to_state.value
        if event.action is IllustrationReviewAction.APPROVE:
            asset.approved_by = event.actor
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            existing = await self._event(owner_id, novel_id, asset.id, event.event_key)
            if existing is None:
                raise IllustrationReviewGateError(
                    "review event race: existing row not found after rollback"
                ) from None
            return asset
        return asset

    # --------------------------------------------------------------- queries

    async def _asset(
        self, owner_id: int, novel_id: int, asset_id: int
    ) -> AssetRevision | None:
        return await self._session.scalar(
            select(AssetRevision).where(
                AssetRevision.owner_id == owner_id,
                AssetRevision.novel_id == novel_id,
                AssetRevision.id == asset_id,
            )
        )

    async def _job(
        self, owner_id: int, novel_id: int, job_id: int
    ) -> IllustrationJob | None:
        return await self._session.scalar(
            select(IllustrationJob).where(
                IllustrationJob.owner_id == owner_id,
                IllustrationJob.novel_id == novel_id,
                IllustrationJob.id == job_id,
            )
        )

    async def _event(
        self, owner_id: int, novel_id: int, asset_revision_id: int, event_key: str
    ) -> IllustrationReviewEvent | None:
        return await self._session.scalar(
            select(IllustrationReviewEvent).where(
                IllustrationReviewEvent.owner_id == owner_id,
                IllustrationReviewEvent.novel_id == novel_id,
                IllustrationReviewEvent.asset_revision_id == asset_revision_id,
                IllustrationReviewEvent.event_key == event_key,
            )
        )

    async def _has_settled_budget(self, job_id: int) -> bool:
        """Durable budget evidence: at least one settled reservation for the job."""
        row = await self._session.scalar(
            select(IllustrationBudgetReservation.id)
            .where(
                IllustrationBudgetReservation.reservation_key.like(f"job:{job_id}:%"),
                IllustrationBudgetReservation.status == "settled",
            )
            .limit(1)
        )
        return row is not None

    async def _has_consistency_report(
        self, owner_id: int, novel_id: int, asset_revision_id: int
    ) -> bool:
        row = await self._session.scalar(
            select(ConsistencyReport.id)
            .where(
                ConsistencyReport.owner_id == owner_id,
                ConsistencyReport.novel_id == novel_id,
                ConsistencyReport.asset_revision_id == asset_revision_id,
            )
            .limit(1)
        )
        return row is not None

    async def _latest_report(
        self, owner_id: int, novel_id: int, asset_revision_id: int
    ) -> ConsistencyReport | None:
        return await self._session.scalar(
            select(ConsistencyReport)
            .where(
                ConsistencyReport.owner_id == owner_id,
                ConsistencyReport.novel_id == novel_id,
                ConsistencyReport.asset_revision_id == asset_revision_id,
            )
            .order_by(ConsistencyReport.id.desc())
            .limit(1)
        )

    async def _budget_evidence(self, job_id: int) -> dict[str, Any] | None:
        row = await self._session.scalar(
            select(IllustrationBudgetReservation)
            .where(
                IllustrationBudgetReservation.reservation_key.like(f"job:{job_id}:%"),
                IllustrationBudgetReservation.status == "settled",
            )
            .order_by(IllustrationBudgetReservation.id.desc())
            .limit(1)
        )
        if row is None:
            return None
        ledger = await self._session.get(IllustrationBudgetLedger, row.ledger_id)
        return {
            "settled_calls": int(row.calls),
            "settled_cost_usd": str(row.cost_usd),
            "reservation_status": row.status,
            "settled_usage": dict(row.settled_usage or {}),
            "price_snapshot": dict(row.price_snapshot or {}),
            "ledger_max_calls": ledger.max_calls if ledger is not None else None,
            "ledger_max_cost_usd": (
                str(ledger.max_cost_usd) if ledger is not None else None
            ),
        }

    @staticmethod
    def _require_scope(*, owner_id: int, novel_id: int) -> None:
        values = (owner_id, novel_id)
        if any(type(value) is not int or value <= 0 for value in values):
            raise IllustrationReviewGateError(
                "scope identifiers must be explicit positive integers"
            )


# ---------------------------------------------------------------------------
# View builders (owner-scoped, candidate-only)
# ---------------------------------------------------------------------------


def _asset_view(asset: AssetRevision) -> AssetRevisionView:
    return AssetRevisionView(
        id=asset.id,
        owner_id=asset.owner_id,
        novel_id=asset.novel_id,
        job_id=asset.job_id,
        revision_key=asset.revision_key,
        revision_number=asset.revision_number,
        asset_id=asset.asset_id,
        mime_type=asset.mime_type,
        width=asset.width,
        height=asset.height,
        size_bytes=asset.size_bytes,
        bytes_hash=asset.bytes_hash,
        scene_spec_hash=asset.scene_spec_hash,
        prompt_revision_id=asset.prompt_revision_id,
        prompt_revision_hash=asset.prompt_revision_hash,
        visual_bible_revision_hash=asset.visual_bible_revision_hash,
        source_snapshot_id=asset.source_snapshot_id,
        source_snapshot_hash=asset.source_snapshot_hash,
        cutoff_chapter=asset.cutoff_chapter,
        provider=asset.provider,
        provider_model=asset.provider_model,
        provider_request_id=asset.provider_request_id,
        rights_status=asset.rights_status,
        approval_state=asset.approval_state,
    )


def _job_view(job: IllustrationJob) -> IllustrationJobView:
    return IllustrationJobView(
        id=job.id,
        owner_id=job.owner_id,
        novel_id=job.novel_id,
        job_key=job.job_key,
        idempotency_key=job.idempotency_key,
        status=job.status,
        status_reason=job.status_reason,
        error_code=job.error_code,
        retry_count=job.retry_count,
        scene_spec_hash=job.scene_spec_hash,
        prompt_revision_id=job.prompt_revision_id,
        prompt_revision_hash=job.prompt_revision_hash,
        visual_bible_revision_hash=job.visual_bible_revision_hash,
        source_snapshot_id=job.source_snapshot_id,
        source_snapshot_hash=job.source_snapshot_hash,
        cutoff_chapter=job.cutoff_chapter,
        config_hash=job.config_hash,
        price_snapshot=dict(job.price_snapshot or {}),
    )


def _event_view(row: IllustrationReviewEvent) -> IllustrationReviewEventView:
    return IllustrationReviewEventView(
        event_key=row.event_key,
        action=row.action,
        actor_source=row.actor_source,
        actor=row.actor,
        reason=row.reason,
        from_approval_state=row.from_approval_state,
        to_approval_state=row.to_approval_state,
    )


def _gate_view(result: IllustrationProposalGateResult) -> IllustrationProposalGateView:
    return IllustrationProposalGateView(
        ok=result.ok,
        reason_code=result.reason_code,
        detail=result.detail,
    )


def _attempt_view(row: IllustrationAttempt) -> IllustrationAttemptView:
    return IllustrationAttemptView(
        id=row.id,
        attempt_number=row.attempt_number,
        status=row.status,
        provider_request_id=row.provider_request_id,
        request_hash=row.request_hash,
        response_hash=row.response_hash,
        usage=dict(row.usage or {}),
        cost_usd=row.cost_usd,
        latency_ms=row.latency_ms,
        error_code=row.error_code,
    )


def _budget_view(
    evidence: dict[str, Any] | None,
) -> IllustrationBudgetEvidenceView | None:
    if evidence is None:
        return None
    return IllustrationBudgetEvidenceView(
        settled_calls=int(evidence["settled_calls"]),
        settled_cost_usd=(
            Decimal(evidence["settled_cost_usd"])
            if evidence.get("settled_cost_usd") is not None
            else None
        ),
        reservation_status=str(evidence["reservation_status"]),
        settled_usage=dict(evidence.get("settled_usage") or {}),
        price_snapshot=dict(evidence.get("price_snapshot") or {}),
        ledger_max_calls=evidence.get("ledger_max_calls"),
        ledger_max_cost_usd=(
            Decimal(evidence["ledger_max_cost_usd"])
            if evidence.get("ledger_max_cost_usd") is not None
            else None
        ),
    )


async def build_review_envelope(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    asset_id: int,
) -> IllustrationReviewEnvelope:
    """Full owner-scoped review envelope (lineage drawer + compare + history).

    Raises ``IllustrationReviewNotFound`` when the asset is outside the explicit
    owner/novel scope (404-equivalent).
    """
    asset = await session.scalar(
        select(AssetRevision).where(
            AssetRevision.owner_id == owner_id,
            AssetRevision.novel_id == novel_id,
            AssetRevision.id == asset_id,
        )
    )
    if asset is None:
        raise IllustrationReviewNotFound(
            "illustration asset not found in the owner/novel scope"
        )
    job = await session.scalar(
        select(IllustrationJob).where(
            IllustrationJob.owner_id == owner_id,
            IllustrationJob.novel_id == novel_id,
            IllustrationJob.id == asset.job_id,
        )
    )
    if job is None:
        raise IllustrationReviewNotFound(
            "illustration asset job not found in the owner/novel scope"
        )
    attempts = (
        await session.scalars(
            select(IllustrationAttempt)
            .where(IllustrationAttempt.job_id == job.id)
            .order_by(IllustrationAttempt.attempt_number.asc())
        )
    ).all()
    events = (
        await session.scalars(
            select(IllustrationReviewEvent)
            .where(
                IllustrationReviewEvent.owner_id == owner_id,
                IllustrationReviewEvent.novel_id == novel_id,
                IllustrationReviewEvent.asset_revision_id == asset.id,
            )
            .order_by(IllustrationReviewEvent.id.asc())
        )
    ).all()
    service = IllustrationReviewService(session)
    budget = await service._budget_evidence(job.id)
    consistency = await service._latest_report(owner_id, novel_id, asset.id)
    gate: IllustrationProposalGateView | None = None
    if asset.approval_state == IllustrationApprovalState.CANDIDATE.value:
        gate = _gate_view(await service._proposal_gate(owner_id, novel_id, asset, job))
    return IllustrationReviewEnvelope(
        asset=_asset_view(asset),
        job=_job_view(job),
        attempts=[_attempt_view(row) for row in attempts],
        budget=_budget_view(budget),
        consistency=report_view(consistency) if consistency is not None else None,
        review_events=[_event_view(row) for row in events],
        approval_gate=gate,
    )


async def build_gallery(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
) -> IllustrationGalleryResponse:
    """Owner-scoped candidate gallery for the review UI (candidate-only).

    Each card carries the asset view, its durable job view (status/error/retry
    eligibility), the latest consistency evidence and the append-only review
    history. Approval gates are only surfaced for candidates.
    """
    assets = (
        await session.scalars(
            select(AssetRevision)
            .where(
                AssetRevision.owner_id == owner_id,
                AssetRevision.novel_id == novel_id,
            )
            .order_by(AssetRevision.id.desc())
        )
    ).all()
    service = IllustrationReviewService(session)
    items: list[IllustrationGalleryItemView] = []
    for asset in assets:
        job = await session.scalar(
            select(IllustrationJob).where(
                IllustrationJob.owner_id == owner_id,
                IllustrationJob.novel_id == novel_id,
                IllustrationJob.id == asset.job_id,
            )
        )
        if job is None:
            raise IllustrationReviewNotFound(
                "illustration asset job not found in the owner/novel scope"
            )
        events = (
            await session.scalars(
                select(IllustrationReviewEvent)
                .where(
                    IllustrationReviewEvent.owner_id == owner_id,
                    IllustrationReviewEvent.novel_id == novel_id,
                    IllustrationReviewEvent.asset_revision_id == asset.id,
                )
                .order_by(IllustrationReviewEvent.id.asc())
            )
        ).all()
        consistency = await service._latest_report(owner_id, novel_id, asset.id)
        gate: IllustrationProposalGateView | None = None
        if asset.approval_state == IllustrationApprovalState.CANDIDATE.value:
            gate = _gate_view(
                await service._proposal_gate(owner_id, novel_id, asset, job)
            )
        items.append(
            IllustrationGalleryItemView(
                asset=_asset_view(asset),
                job=_job_view(job),
                consistency=(
                    report_view(consistency) if consistency is not None else None
                ),
                review_events=[_event_view(row) for row in events],
                approval_gate=gate,
            )
        )
    return IllustrationGalleryResponse(items=items, total=len(items))


def build_proposal_ref(
    asset: AssetRevision,
) -> FrozenAssetRevisionView:
    """Phase 34 consumer ref for a ``proposal_ready`` asset (never auto-created).

    The ``FrozenAssetRevisionView`` validator fails closed unless the asset is
    actually ``proposal_ready`` with cleared rights — so an unapproved or
    unresolved candidate can never be handed to Phase 34 publish.
    """
    return FrozenAssetRevisionView(
        id=asset.id,
        owner_id=asset.owner_id,
        novel_id=asset.novel_id,
        job_id=asset.job_id,
        revision_key=asset.revision_key,
        revision_number=asset.revision_number,
        asset_id=asset.asset_id,
        storage_key=asset.storage_key,
        mime_type=asset.mime_type,
        width=asset.width,
        height=asset.height,
        size_bytes=asset.size_bytes,
        bytes_hash=asset.bytes_hash,
        scene_spec_hash=asset.scene_spec_hash,
        prompt_revision_hash=asset.prompt_revision_hash,
        visual_bible_revision_hash=asset.visual_bible_revision_hash,
        source_snapshot_id=asset.source_snapshot_id,
        source_snapshot_hash=asset.source_snapshot_hash,
        cutoff_chapter=asset.cutoff_chapter,
        provider=asset.provider,
        provider_model=asset.provider_model,
        provider_request_id=asset.provider_request_id,
        rights_status=asset.rights_status,
        approval_state=asset.approval_state,
        approved_by=asset.approved_by,
    )


# Marker so `_RIGHTS_BLOCKED_FOR_APPROVAL` stays reachable by the gate tests;
# the frozenset documents the fail-closed rights semantics (D-33-03).
__all__ = [
    "IllustrationAttemptView",
    "IllustrationBudgetEvidenceView",
    "IllustrationGalleryItemView",
    "IllustrationGalleryResponse",
    "IllustrationProposalGateResult",
    "IllustrationProposalGateView",
    "IllustrationReviewActionResponse",
    "IllustrationReviewEnvelope",
    "IllustrationReviewEventView",
    "IllustrationReviewGateError",
    "IllustrationReviewNotFound",
    "IllustrationReviewService",
    "build_gallery",
    "build_proposal_ref",
    "build_review_envelope",
    "evaluate_illustration_proposal_gate",
]
