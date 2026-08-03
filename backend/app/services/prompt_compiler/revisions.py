"""PromptRevision append-only review, approve/reject/history and stale/hash gate
(Phase 32-04, REQ-VIS-03).

D-32-01..D-32-04: approval is an explicit, append-only human/machine action
that only moves a PromptRevision candidate's review projection. This module
owns the *review/versioning seam* for compiled prompt candidates:

- ``evaluate_prompt_approval_gate`` — the pure, replayable fail-closed approval
  gate: an approval cannot succeed while the prompt is stale (compiled against
  a superseded Visual Bible revision or source snapshot), any lineage hash is
  malformed, or the stored hashes no longer replay from the candidate's own
  content (deterministic lineage, D-32-03).
- ``PromptRevisionReviewService.append_event`` — server-side gated, append-only,
  idempotent review actions (approve/reject/supersede/needs_relink). A repeated
  ``event_key`` replays the existing event and never appends a second one; the
  revision row's ``review_state`` is only a projection, and approval never
  rewrites the SceneSpec or the original source (D-32-01/D-32-04).
- ``build_review_envelope`` — owner-scoped review envelope: current state,
  staleness marker, append-only event history, hash replay view and the
  approval-gate reason codes.

Budget/cost/usage is explicitly persisted as ``not_applicable`` (Phase 32 never
calls an image provider) so the absence of provider work is never silently
implied (must-have: no silent degradation). Only an approved candidate prompt
may become Phase 33 generation input.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.models.prompt_revision import (
    PromptRevision as PromptRevisionRow,
    PromptRevisionReviewEvent as PromptRevisionReviewEventRow,
)
from app.schemas.scene_spec import (
    PROMPT_ARTIFACT_KIND,
    PROMPT_SCHEMA_VERSION,
    PromptRevisionContract,
    PromptRevisionView,
    SceneSpecGateError,
    SpecActorSource,
    SpecReviewAction,
    SpecReviewEventInput,
    SpecReviewState,
    StrictSceneSpecModel,
    canonical_scene_spec_hash,
    recompute_prompt_hash,
    validate_review_event,
)
from app.services.prompt_compiler.adapters import (
    PromptRevisionNotFound as PromptRevisionNotFoundBase,
    PromptRevisionService,
)


class PromptReviewError(ValueError):
    """Base class for fail-closed prompt review errors."""


class PromptReviewNotFound(PromptReviewError):
    """A revision is outside the explicit owner/novel scope (404-equivalent)."""


class PromptReviewConflict(PromptReviewError):
    """A conflicting review event or a race that cannot replay."""


# ---------------------------------------------------------------------------
# Pure, replayable approval gate (unit-testable without a database)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptApprovalGateResult:
    """Fail-closed approval readiness with a stable machine reason code."""

    ok: bool
    reason_code: str | None = None
    detail: str | None = None


def recompute_input_hash_from_revision(revision: PromptRevisionContract) -> str:
    """Provider-neutral input lineage hash computed from the revision alone.

    ``prompt_input_payload`` in the schema uses ``recompute_scene_spec_hash(spec)``,
    which a valid compiled/edited revision equals by contract
    (``scene_spec_hash == spec.content_hash``, enforced by
    ``validate_prompt_revision_contract``), so the canonical input payload is
    exactly reproducible from the revision's own lineage fields.
    """
    return canonical_scene_spec_hash(
        {
            "artifact_kind": PROMPT_ARTIFACT_KIND,
            "schema_version": PROMPT_SCHEMA_VERSION,
            "owner_id": revision.owner_id,
            "novel_id": revision.novel_id,
            "scene_spec_hash": revision.scene_spec_hash,
            "visual_bible_revision_hash": revision.visual_bible_revision_hash,
            "source_snapshot_id": revision.source_snapshot_id,
            "source_snapshot_hash": revision.source_snapshot_hash,
            "cutoff_chapter": revision.cutoff_chapter,
            "schema_hash": revision.schema_hash,
            "prompt_schema_hash": revision.prompt_schema_hash,
            "compiler_version": revision.compiler_version,
            "config_hash": revision.config_hash,
            "sections": revision.sections,
            "negative_constraints": revision.negative_constraints,
            "uncertainties": revision.uncertainties,
        }
    )


def _malformed_hash(result: PromptApprovalGateResult | None, value: str, name: str):
    if result is not None:
        return result
    if not isinstance(value, str) or len(value) != 64 or not set(value) <= set("0123456789abcdef"):
        return PromptApprovalGateResult(
            ok=False,
            reason_code=f"{name}_malformed",
            detail=f"prompt {name} is missing or malformed",
        )
    return None


def evaluate_prompt_approval_gate(
    *,
    revision: PromptRevisionContract,
    stale: bool,
) -> PromptApprovalGateResult:
    """Fail closed unless the prompt is fresh and its lineage replays.

    - a stale prompt (compiled against a superseded Visual Bible revision or
      source snapshot) can never be approved as Phase 33 input;
    - every lineage hash field must be a well-formed 64-char hex digest;
    - ``prompt_hash`` must replay from the candidate's own content and
      ``input_hash`` must replay from its own lineage (``input_hash`` always
      differs from ``prompt_hash``).
    """
    if stale:
        return PromptApprovalGateResult(
            ok=False,
            reason_code="stale_prompt",
            detail=(
                "prompt was compiled against a superseded Visual Bible revision "
                "or source snapshot; recompile against the current approved state"
            ),
        )
    for name in (
        "scene_spec_hash",
        "visual_bible_revision_hash",
        "source_snapshot_hash",
        "schema_hash",
        "prompt_schema_hash",
        "config_hash",
        "input_hash",
        "prompt_hash",
    ):
        blocked = _malformed_hash(None, getattr(revision, name), name)
        if blocked is not None:
            return blocked

    if recompute_prompt_hash(revision) != revision.prompt_hash:
        return PromptApprovalGateResult(
            ok=False,
            reason_code="prompt_hash_replay",
            detail="prompt prompt_hash does not replay from the candidate content",
        )
    if revision.input_hash == revision.prompt_hash:
        return PromptApprovalGateResult(
            ok=False,
            reason_code="hash_separation",
            detail="prompt input_hash must differ from prompt_hash",
        )
    if recompute_input_hash_from_revision(revision) != revision.input_hash:
        return PromptApprovalGateResult(
            ok=False,
            reason_code="input_hash_replay",
            detail="prompt input_hash does not replay from the candidate lineage",
        )
    return PromptApprovalGateResult(ok=True)


# ---------------------------------------------------------------------------
# Review envelope contracts (immutable history + gate view)
# ---------------------------------------------------------------------------


class PromptReviewEventView(StrictSceneSpecModel):
    """One append-only review event (server-derived to/from states)."""

    action: SpecReviewAction
    actor_source: SpecActorSource
    actor: str
    reason: str
    event_key: str
    from_review_state: SpecReviewState
    to_review_state: SpecReviewState


class PromptApprovalGateView(StrictSceneSpecModel):
    """Approval readiness surfaced on the envelope with a stable reason code."""

    ok: bool
    reason_code: str | None = None
    detail: str | None = None


class PromptRevisionReviewEnvelope(StrictSceneSpecModel):
    """Review envelope: current state, stale marker, history and approval gate."""

    revision: PromptRevisionView
    stale: bool = False
    review_events: list[PromptReviewEventView] = Field(default_factory=list)
    approval_gate: PromptApprovalGateView | None = None


# ---------------------------------------------------------------------------
# Server-side review service (gates + append-only events)
# ---------------------------------------------------------------------------


class PromptRevisionReviewService:
    """Owner-scoped prompt review seam: gated, append-only, explicit actions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._revision_service = PromptRevisionService(session)

    # ------------------------------------------------------------------ append

    async def append_event(
        self,
        *,
        owner_id: int,
        novel_id: int,
        event: SpecReviewEventInput,
    ) -> PromptRevisionReviewEnvelope:
        """Append one review action after server-side scope/gate checks.

        The approval gate fails closed before any row is written: a stale
        prompt or a prompt whose hashes do not replay is blocked with a stable
        reason code. A repeated ``event_key`` replays the existing event and
        never appends a second one (idempotent, D-32-04). Approval only marks
        the PromptRevision as an approved Phase 33 input; the SceneSpec and the
        original source are never rewritten.
        """
        self._require_scope(owner_id=owner_id, novel_id=novel_id)
        if event.owner_id != owner_id or event.novel_id != novel_id:
            raise PromptReviewConflict(
                "review event scope does not match request scope"
            )

        revision = await self._revision(
            owner_id=owner_id, novel_id=novel_id, revision_id=event.revision_id
        )
        if revision is None:
            raise PromptReviewNotFound(
                "prompt revision not found in the explicit owner/novel scope"
            )

        existing = await self._event(
            owner_id=owner_id,
            novel_id=novel_id,
            revision_id=revision.id,
            event_key=event.event_key,
        )
        if existing is not None:
            # Idempotent replay: no second event, no state change.
            return await build_review_envelope(
                self._session,
                owner_id=owner_id,
                novel_id=novel_id,
                revision_id=revision.id,
            )

        if revision.review_state != event.from_review_state.value:
            raise SceneSpecGateError(
                f"review from_review_state {event.from_review_state.value!r} does "
                f"not match the revision's current state {revision.review_state!r}"
            )

        try:
            to_state = validate_review_event(event)
        except SceneSpecGateError as exc:
            raise SceneSpecGateError(str(exc)) from exc

        gate: PromptApprovalGateResult | None = None
        if event.action is SpecReviewAction.APPROVE:
            gate = await self._approval_gate(
                owner_id=owner_id, novel_id=novel_id, revision=revision
            )
            if not gate.ok:
                raise SceneSpecGateError(
                    f"approval blocked by {gate.reason_code}: {gate.detail}"
                )

        details = await self._event_details(
            owner_id=owner_id,
            novel_id=novel_id,
            revision=revision,
            gate=gate,
        )
        await self._append_event_row(
            owner_id=owner_id,
            novel_id=novel_id,
            revision=revision,
            event=event,
            to_state=to_state,
            details=details,
        )
        return await build_review_envelope(
            self._session,
            owner_id=owner_id,
            novel_id=novel_id,
            revision_id=revision.id,
        )

    # ------------------------------------------------------------------ gates

    async def _prompt_is_stale_for_review(
        self, *, owner_id: int, novel_id: int, revision: PromptRevisionRow
    ) -> bool:
        """A prompt is stale when its compiled Visual Bible revision or source
        snapshot no longer matches the novel's current approved state.

        Uses the revision's own lineage columns (works for both persisted and
        inline-edited candidates). If no approved Visual Bible revision exists,
        the prompt is stale by default (fail closed).
        """
        try:
            latest_vb = await self._revision_service._latest_approved_version(
                owner_id=owner_id, novel_id=novel_id
            )
        except PromptRevisionNotFoundBase:
            return True
        current_hash, _ = await self._revision_service._current_snapshot(
            owner_id=owner_id, novel_id=novel_id
        )
        return (
            revision.visual_bible_revision_hash != latest_vb.manifest_hash
            or revision.source_snapshot_hash != current_hash
        )

    async def _approval_gate(
        self,
        *,
        owner_id: int,
        novel_id: int,
        revision: PromptRevisionRow,
    ) -> PromptApprovalGateResult:
        stale = await self._prompt_is_stale_for_review(
            owner_id=owner_id, novel_id=novel_id, revision=revision
        )
        contract = PromptRevisionService._revision_contract_from_row(revision)
        return evaluate_prompt_approval_gate(revision=contract, stale=stale)

    async def _event_details(
        self,
        *,
        owner_id: int,
        novel_id: int,
        revision: PromptRevisionRow,
        gate: PromptApprovalGateResult | None,
    ) -> dict[str, Any]:
        """Explicit audit payload: budget marker, lineage hashes, gate snapshot.

        Phase 32 has no provider calls, so budget/cost/usage is explicitly
        ``not_applicable`` instead of silently missing (must-have: no silent
        degradation). The lineage snapshot makes an approval replayable.
        """
        return {
            "budget": {
                "provider_calls": 0,
                "credits_used": 0,
                "status": "not_applicable",
            },
            "lineage": {
                "scene_spec_hash": revision.scene_spec_hash,
                "visual_bible_revision_hash": revision.visual_bible_revision_hash,
                "source_snapshot_hash": revision.source_snapshot_hash,
                "prompt_hash": revision.prompt_hash,
                "cutoff_chapter": revision.cutoff_chapter,
            },
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
        revision: PromptRevisionRow,
        event: SpecReviewEventInput,
        to_state: SpecReviewState,
        details: dict[str, Any],
    ) -> PromptRevisionRow:
        """Append one event row and project the revision's review state.

        The PromptRevision content row is immutable (D-32-01); only its
        ``review_state`` projection is updated. A concurrent duplicate
        ``event_key`` rolls back and replays the winner.
        """
        row = PromptRevisionReviewEventRow(
            owner_id=owner_id,
            novel_id=novel_id,
            revision_id=revision.id,
            action=event.action.value,
            actor_source=event.actor_source.value,
            actor=event.actor,
            reason=event.reason,
            event_key=event.event_key,
            from_review_state=event.from_review_state.value,
            to_review_state=to_state.value,
            details=details,
        )
        self._session.add(row)
        revision.review_state = to_state.value
        try:
            await self._session.flush()
        except IntegrityError:
            # Concurrent duplicate event_key: roll back and replay the winner.
            await self._session.rollback()
            existing = await self._event(
                owner_id=owner_id,
                novel_id=novel_id,
                revision_id=revision.id,
                event_key=event.event_key,
            )
            if existing is None:
                raise PromptReviewConflict(
                    "review event race: existing row not found after rollback"
                ) from None
        return revision

    # --------------------------------------------------------------- queries

    async def _revision(
        self, *, owner_id: int, novel_id: int, revision_id: int
    ) -> PromptRevisionRow | None:
        return await self._revision_service._revision_by_id(
            owner_id=owner_id, novel_id=novel_id, revision_id=revision_id
        )

    async def _event(
        self,
        *,
        owner_id: int,
        novel_id: int,
        revision_id: int,
        event_key: str,
    ) -> PromptRevisionReviewEventRow | None:
        return await self._session.scalar(
            select(PromptRevisionReviewEventRow).where(
                PromptRevisionReviewEventRow.owner_id == owner_id,
                PromptRevisionReviewEventRow.novel_id == novel_id,
                PromptRevisionReviewEventRow.revision_id == revision_id,
                PromptRevisionReviewEventRow.event_key == event_key,
            )
        )

    async def _events(
        self, *, owner_id: int, novel_id: int, revision_id: int
    ) -> list[PromptRevisionReviewEventRow]:
        rows = (
            await self._session.scalars(
                select(PromptRevisionReviewEventRow)
                .where(
                    PromptRevisionReviewEventRow.owner_id == owner_id,
                    PromptRevisionReviewEventRow.novel_id == novel_id,
                    PromptRevisionReviewEventRow.revision_id == revision_id,
                )
                .order_by(PromptRevisionReviewEventRow.id.asc())
            )
        ).all()
        return list(rows)

    @staticmethod
    def _require_scope(*, owner_id: int, novel_id: int) -> None:
        values = (owner_id, novel_id)
        if any(type(value) is not int or value <= 0 for value in values):
            raise PromptReviewConflict(
                "scope identifiers must be explicit positive integers"
            )


# ---------------------------------------------------------------------------
# Owner-scoped review envelope builder (history + stale + gate)
# ---------------------------------------------------------------------------


async def build_review_envelope(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    revision_id: int,
) -> PromptRevisionReviewEnvelope:
    """Review read envelope for one owned prompt revision; 404-equivalent on
    scope. Surfaces the current review state, the staleness marker, the
    append-only event history and (for an approvable candidate) the live
    approval-gate reason codes."""
    service = PromptRevisionReviewService(session)
    revision = await service._revision(
        owner_id=owner_id, novel_id=novel_id, revision_id=revision_id
    )
    if revision is None:
        raise PromptReviewNotFound(
            "prompt revision not found in the explicit owner/novel scope"
        )
    stale = await service._prompt_is_stale_for_review(
        owner_id=owner_id, novel_id=novel_id, revision=revision
    )
    event_rows = await service._events(
        owner_id=owner_id, novel_id=novel_id, revision_id=revision.id
    )
    approval_gate: PromptApprovalGateView | None = None
    if revision.review_state in (
        SpecReviewState.CANDIDATE.value,
        SpecReviewState.NEEDS_RELINK.value,
    ):
        gate = await service._approval_gate(
            owner_id=owner_id, novel_id=novel_id, revision=revision
        )
        approval_gate = PromptApprovalGateView(
            ok=gate.ok,
            reason_code=gate.reason_code,
            detail=gate.detail,
        )
    return PromptRevisionReviewEnvelope(
        revision=PromptRevisionService._view_from_row(revision),
        stale=stale,
        review_events=[
            PromptReviewEventView(
                action=row.action,
                actor_source=row.actor_source,
                actor=row.actor,
                reason=row.reason,
                event_key=row.event_key,
                from_review_state=row.from_review_state,
                to_review_state=row.to_review_state,
            )
            for row in event_rows
        ],
        approval_gate=approval_gate,
    )
