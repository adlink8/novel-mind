"""Explicit human review and frozen key-scene set (Phase 31-03, REQ-VIS-02).

D-31-04: human review creates an append-only decision and a frozen key-scene
set; rejected candidates remain auditable. This module owns the *review seam*:

- ``evaluate_candidate_approval_gate`` — the pure, replayable fail-closed
  approval gate: a candidate approval cannot succeed while the candidate has no
  persisted evidence range, or its persisted evidence drifts from the set
  source snapshot / spoiler cutoff.
- ``KeySceneReviewService.append_decision`` — server-side gated, append-only,
  idempotent candidate review (``approve`` / ``reject`` / ``needs_relink`` /
  ``supersede``). A repeated ``decision_key`` replays the existing decision and
  never appends a second row; rejected candidates stay in the audit history and
  no content row is mutated.
- ``KeySceneReviewService.freeze`` — explicit set-level freeze (set ``approve``)
  with a server-side freeze gate. The gate requires at least one approved
  candidate and re-verifies every approved candidate's persisted evidence
  lineage before the set may freeze. The produced frozen manifest is
  recomputable and contains ONLY approved candidates; rejected/unresolved
  candidates never enter it and never reach downstream readers/export.
- ``build_frozen_set_view`` / ``load_frozen_set_view`` — owner-scoped read of
  the frozen candidate subset plus its recomputed frozen manifest hash.

No code here re-scores candidates, re-proposes scenes or touches source/canon/
active reader state (D-31-01): the model proposal and deterministic
score/diversity/spoiler validation stay separate from explicit user choice.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.models.key_scene import (
    SceneCandidate as SceneCandidateRow,
    SceneCandidateSet as SceneCandidateSetRow,
    SceneEvidenceRange as SceneEvidenceRangeRow,
    SceneReviewDecision as SceneReviewDecisionRow,
)
from app.schemas.key_scene import (
    FrozenKeySceneSetView,
    KeySceneGateError,
    KeySceneReviewAction,
    KeySceneReviewState,
    SceneCandidateView,
    SceneCoordinates,
    SceneEvidenceRangeView,
    SceneReviewDecisionInput,
    SceneReviewDecisionView,
    SalienceReason,
    SpeakerDialogueHeuristicSignal,
    canonical_key_scene_hash,
    review_state_after,
    validate_review_decision,
)
from app.services.key_scenes.candidates import derive_candidate_review_states

# A candidate whose only strength is the advisory speaker/dialogue heuristic can
# never be approved as if the heuristic were evidence; approval still requires
# persisted evidence (the candidate's own evidence ranges are the citation
# authority). Freezing never depends on the heuristic signal.
FROZEN_MANIFEST_KIND = "key_scene.frozen"


# ---------------------------------------------------------------------------
# Pure, replayable gates (unit-testable without a database)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateApprovalGateResult:
    """Fail-closed approval readiness with a stable machine reason code."""

    ok: bool
    reason_code: str | None = None
    detail: str | None = None


def evaluate_candidate_approval_gate(
    *,
    evidence_rows: Sequence[Mapping[str, object]],
    source_snapshot_hash: str,
    cutoff_chapter: int,
) -> CandidateApprovalGateResult:
    """Fail closed unless the candidate has valid persisted evidence.

    Evidence is the only citation authority (D-31-02/05): an approval cannot
    succeed while the candidate carries no evidence range, or any range drifts
    from the set snapshot / cutoff. A missing hash never silently becomes a
    pass (fail closed, no silent degradation).
    """
    if not evidence_rows:
        return CandidateApprovalGateResult(
            ok=False,
            reason_code="evidence_missing",
            detail="candidate has no persisted evidence range",
        )
    for row in evidence_rows:
        if row.get("source_snapshot_hash") != source_snapshot_hash:
            return CandidateApprovalGateResult(
                ok=False,
                reason_code="evidence_snapshot_mismatch",
                detail="candidate evidence source_snapshot_hash does not match the set",
            )
        if int(row.get("cutoff_chapter", -1)) != cutoff_chapter:
            return CandidateApprovalGateResult(
                ok=False,
                reason_code="evidence_cutoff_mismatch",
                detail="candidate evidence cutoff_chapter does not match the set",
            )
        chapter_number = int(row.get("chapter_number", cutoff_chapter + 1))
        if chapter_number > cutoff_chapter:
            return CandidateApprovalGateResult(
                ok=False,
                reason_code="evidence_beyond_cutoff",
                detail=(
                    "candidate evidence chapter_number exceeds the set spoiler cutoff"
                ),
            )
        content_hash = str(row.get("content_hash") or "")
        if len(content_hash) != 64:
            return CandidateApprovalGateResult(
                ok=False,
                reason_code="evidence_content_hash",
                detail="candidate evidence content_hash is missing or malformed",
            )
    return CandidateApprovalGateResult(ok=True)


@dataclass(frozen=True)
class FreezeGateResult:
    """Fail-closed freeze readiness for one set."""

    ok: bool
    reason_code: str | None = None
    detail: str | None = None


def evaluate_freeze_gate(
    *,
    approved_count: int,
    approved_evidence: Mapping[str, CandidateApprovalGateResult],
) -> FreezeGateResult:
    """A set may freeze only with an explicit, evidence-verified approval set.

    - at least one candidate must be ``approved`` (an empty frozen set would
      silently break the candidate-set invariant);
    - every approved candidate must pass the evidence approval gate; any drift
      fails closed with the underlying reason code.
    """
    if approved_count <= 0:
        return FreezeGateResult(
            ok=False,
            reason_code="no_approved_candidates",
            detail="freeze requires at least one approved candidate",
        )
    for candidate_key, gate in approved_evidence.items():
        if not gate.ok:
            return FreezeGateResult(
                ok=False,
                reason_code=gate.reason_code or "evidence_unresolved",
                detail=f"candidate {candidate_key!r}: {gate.detail}",
            )
    return FreezeGateResult(ok=True)


# ---------------------------------------------------------------------------
# Frozen manifest computation (recomputable, approved-candidates only)
# ---------------------------------------------------------------------------


def frozen_manifest_payload(
    *,
    set_row: SceneCandidateSetRow,
    approved_candidates: Sequence[SceneCandidateRow],
) -> dict:
    """Canonical frozen-manifest payload over the approved candidate subset.

    Uses each persisted candidate's canonical payload so the frozen hash is
    byte-replayable from the same content the set was generated with. The
    approved subset is the ONLY content that may enter downstream Phase 32.
    """
    return {
        "kind": FROZEN_MANIFEST_KIND,
        "schema_version": set_row.schema_version,
        "owner_id": set_row.owner_id,
        "novel_id": set_row.novel_id,
        "version_key": set_row.version_key,
        "revision_number": set_row.revision_number,
        "parent_set_id": set_row.parent_set_id,
        "source_snapshot_id": set_row.source_snapshot_id,
        "source_snapshot_hash": set_row.source_snapshot_hash,
        "cutoff_chapter": set_row.cutoff_chapter,
        "schema_hash": set_row.schema_hash,
        "policy_hash": set_row.policy_hash,
        "detector_id": set_row.detector_id,
        "detector_version": set_row.detector_version,
        "approved_visual_bible_revision_id": set_row.approved_visual_bible_revision_id,
        "approved_visual_bible_revision_hash": set_row.approved_visual_bible_revision_hash,
        "candidates": [
            candidate.canonical_payload for candidate in approved_candidates
        ],
    }


def recompute_frozen_manifest_hash(
    *,
    set_row: SceneCandidateSetRow,
    approved_candidates: Sequence[SceneCandidateRow],
) -> str:
    """SHA-256 over the approved candidate subset (deterministic, replayable)."""
    return canonical_key_scene_hash(
        frozen_manifest_payload(
            set_row=set_row, approved_candidates=approved_candidates
        )
    )


def _freeze_decision_key(*, owner_id: int, novel_id: int, set_id: int) -> str:
    """Deterministic idempotency key for one set freeze (re-freeze replays)."""
    return canonical_key_scene_hash(
        {
            "kind": "key_scene.freeze",
            "owner_id": owner_id,
            "novel_id": novel_id,
            "set_id": set_id,
        }
    )


# ---------------------------------------------------------------------------
# Owner-scoped review service
# ---------------------------------------------------------------------------


class KeySceneReviewError(ValueError):
    """Base class for fail-closed key-scene review errors."""


class KeySceneReviewNotFound(KeySceneReviewError):
    """A set/candidate is outside the explicit owner/novel scope (404-equivalent)."""


class KeySceneReviewConflict(KeySceneReviewError):
    """A conflicting review decision or a race that cannot replay."""


class KeySceneReviewService:
    """Owner-scoped review seam: gated, append-only, explicit review actions."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ append

    async def append_decision(
        self,
        *,
        owner_id: int,
        novel_id: int,
        decision: SceneReviewDecisionInput,
    ) -> SceneCandidateSetRow:
        """Append one explicit candidate review decision (append-only, idempotent).

        Candidate-level only: ``decision.candidate_key`` is required. The
        server re-verifies owner/novel/set scope, the candidate belongs to the
        set, the decision's ``from_review_state`` matches the candidate's
        current state, the transition is legal, and (for approvals) the
        persisted evidence gate holds. A repeated ``decision_key`` replays the
        existing decision and never appends a second row (D-31-04).
        """
        self._require_scope(owner_id=owner_id, novel_id=novel_id)
        if decision.owner_id != owner_id or decision.novel_id != novel_id:
            raise KeySceneReviewConflict(
                "review decision scope does not match request scope"
            )
        if decision.candidate_key is None:
            raise KeySceneReviewConflict(
                "set-level decisions are made through freeze; "
                "candidate review requires a candidate_key"
            )

        set_row = await self._set(
            owner_id=owner_id, novel_id=novel_id, set_id=decision.set_id
        )
        if set_row is None:
            raise KeySceneReviewNotFound(
                "candidate set not found in the explicit owner/novel scope"
            )

        if set_row.review_state in (
            KeySceneReviewState.APPROVED.value,
            KeySceneReviewState.SUPERSEDED.value,
        ):
            raise KeySceneGateError(
                f"set is {set_row.review_state!r} (frozen); candidate review "
                "requires a new set revision"
            )

        existing = await self._decision(
            owner_id=owner_id,
            novel_id=novel_id,
            set_id=set_row.id,
            decision_key=decision.decision_key,
        )
        if existing is not None:
            return set_row  # idempotent replay: no second decision, no state change

        candidate = await self._candidate(
            owner_id=owner_id,
            novel_id=novel_id,
            set_id=set_row.id,
            candidate_key=decision.candidate_key,
        )
        if candidate is None:
            raise KeySceneReviewNotFound(
                f"candidate {decision.candidate_key!r} is not in the set"
            )

        try:
            to_state = validate_review_decision(decision)
        except KeySceneGateError as exc:
            raise KeySceneGateError(str(exc)) from exc
        effective_states = await self._candidate_effective_states(
            owner_id=owner_id, novel_id=novel_id, set_id=set_row.id
        )
        effective = effective_states.get(
            decision.candidate_key, KeySceneReviewState(candidate.review_state)
        )
        if effective != decision.from_review_state:
            raise KeySceneGateError(
                f"review from_review_state {decision.from_review_state.value!r} "
                f"does not match the candidate's current effective state "
                f"{effective.value!r}"
            )

        gate: CandidateApprovalGateResult | None = None
        if decision.action is KeySceneReviewAction.APPROVE:
            gate = await self._candidate_approval_gate(
                owner_id=owner_id,
                novel_id=novel_id,
                set_id=set_row.id,
                candidate_id=candidate.id,
                source_snapshot_hash=set_row.source_snapshot_hash,
                cutoff_chapter=set_row.cutoff_chapter,
            )
            if not gate.ok:
                raise KeySceneGateError(
                    f"approval blocked by {gate.reason_code}: {gate.detail}"
                )

        details = {
            "budget": {
                "provider_calls": 0,
                "credits_used": 0,
                "status": "not_applicable",
            },
            "lineage": {
                "source_snapshot_hash": set_row.source_snapshot_hash,
                "manifest_hash": set_row.manifest_hash,
                "cutoff_chapter": set_row.cutoff_chapter,
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
        return await self._append_decision_row(
            owner_id=owner_id,
            novel_id=novel_id,
            set_row=set_row,
            decision=decision,
            to_state=to_state,
            details=details,
        )

    # ------------------------------------------------------------------ freeze

    async def freeze(
        self,
        *,
        owner_id: int,
        novel_id: int,
        set_id: int,
        actor: str,
        reason: str,
    ) -> tuple[SceneCandidateSetRow, FrozenKeySceneSetView]:
        """Freeze the set: explicit set-level approval with the freeze gate.

        Appends one append-only set decision (derived deterministic key, so a
        re-freeze replays) and moves the set projection to ``approved``. The
        frozen manifest is recomputed from the approved candidates only.
        """
        self._require_scope(owner_id=owner_id, novel_id=novel_id)
        set_row = await self._set(owner_id=owner_id, novel_id=novel_id, set_id=set_id)
        if set_row is None:
            raise KeySceneReviewNotFound(
                "candidate set not found in the explicit owner/novel scope"
            )

        decision_key = _freeze_decision_key(
            owner_id=owner_id, novel_id=novel_id, set_id=set_row.id
        )
        existing = await self._decision(
            owner_id=owner_id,
            novel_id=novel_id,
            set_id=set_row.id,
            decision_key=decision_key,
        )
        if existing is not None:
            return set_row, await build_frozen_set_view(
                self._session,
                owner_id=owner_id,
                novel_id=novel_id,
                set_id=set_row.id,
            )

        if set_row.review_state not in (
            KeySceneReviewState.CANDIDATE.value,
            KeySceneReviewState.NEEDS_RELINK.value,
        ):
            raise KeySceneGateError(
                f"set is {set_row.review_state!r}; freeze is only legal from a "
                "candidate/needs_relink set"
            )

        candidates = (
            await self._session.scalars(
                select(SceneCandidateRow)
                .where(
                    SceneCandidateRow.owner_id == owner_id,
                    SceneCandidateRow.novel_id == novel_id,
                    SceneCandidateRow.set_id == set_row.id,
                )
                .order_by(SceneCandidateRow.candidate_order.asc())
            )
        ).all()
        decision_rows = (
            await self._session.scalars(
                select(SceneReviewDecisionRow)
                .where(
                    SceneReviewDecisionRow.owner_id == owner_id,
                    SceneReviewDecisionRow.novel_id == novel_id,
                    SceneReviewDecisionRow.set_id == set_row.id,
                )
                .order_by(SceneReviewDecisionRow.id.asc())
            )
        ).all()
        # Candidate rows are immutable; the effective approved set is derived
        # from the append-only decisions (D-31-04).
        effective_states = derive_candidate_review_states(decision_rows)
        approved = [
            c
            for c in candidates
            if effective_states.get(
                c.candidate_key, KeySceneReviewState(c.review_state)
            )
            is KeySceneReviewState.APPROVED
        ]

        approved_evidence: dict[str, CandidateApprovalGateResult] = {}
        for candidate in approved:
            approved_evidence[
                candidate.candidate_key
            ] = await self._candidate_approval_gate(
                owner_id=owner_id,
                novel_id=novel_id,
                set_id=set_row.id,
                candidate_id=candidate.id,
                source_snapshot_hash=set_row.source_snapshot_hash,
                cutoff_chapter=set_row.cutoff_chapter,
            )
        gate = evaluate_freeze_gate(
            approved_count=len(approved),
            approved_evidence=approved_evidence,
        )
        if not gate.ok:
            raise KeySceneGateError(
                f"freeze blocked by {gate.reason_code}: {gate.detail}"
            )

        decision = SceneReviewDecisionInput(
            owner_id=owner_id,
            novel_id=novel_id,
            set_id=set_row.id,
            decision_key=decision_key,
            action=KeySceneReviewAction.APPROVE,
            actor_source="human",
            actor=actor,
            reason=reason,
            from_review_state=KeySceneReviewState(set_row.review_state),
            candidate_key=None,
        )
        to_state = review_state_after(set_row.review_state, decision.action)
        details = {
            "budget": {
                "provider_calls": 0,
                "credits_used": 0,
                "status": "not_applicable",
            },
            "lineage": {
                "source_snapshot_hash": set_row.source_snapshot_hash,
                "manifest_hash": set_row.manifest_hash,
                "cutoff_chapter": set_row.cutoff_chapter,
            },
            "freeze_gate": {
                "ok": gate.ok,
                "reason_code": gate.reason_code,
                "approved_count": len(approved),
            },
        }
        await self._append_decision_row(
            owner_id=owner_id,
            novel_id=novel_id,
            set_row=set_row,
            decision=decision,
            to_state=to_state,
            details=details,
            set_target_state=to_state.value,
        )
        return set_row, await build_frozen_set_view(
            self._session,
            owner_id=owner_id,
            novel_id=novel_id,
            set_id=set_row.id,
        )

    # ------------------------------------------------------------------ gates

    async def _candidate_approval_gate(
        self,
        *,
        owner_id: int,
        novel_id: int,
        set_id: int,
        candidate_id: int,
        source_snapshot_hash: str,
        cutoff_chapter: int,
    ) -> CandidateApprovalGateResult:
        """Re-run the fail-closed evidence gate against persisted rows."""
        rows = (
            await self._session.execute(
                select(
                    SceneEvidenceRangeRow.evidence_key,
                    SceneEvidenceRangeRow.source_snapshot_hash,
                    SceneEvidenceRangeRow.chapter_number,
                    SceneEvidenceRangeRow.cutoff_chapter,
                    SceneEvidenceRangeRow.content_hash,
                ).where(
                    SceneEvidenceRangeRow.owner_id == owner_id,
                    SceneEvidenceRangeRow.novel_id == novel_id,
                    SceneEvidenceRangeRow.set_id == set_id,
                    SceneEvidenceRangeRow.candidate_id == candidate_id,
                )
            )
        ).all()
        return evaluate_candidate_approval_gate(
            evidence_rows=[
                {
                    "evidence_key": key,
                    "source_snapshot_hash": snapshot_hash,
                    "chapter_number": chapter_number,
                    "cutoff_chapter": cutoff_chapter,
                    "content_hash": content_hash,
                }
                for key, snapshot_hash, chapter_number, cutoff_chapter, content_hash in rows
            ],
            source_snapshot_hash=source_snapshot_hash,
            cutoff_chapter=cutoff_chapter,
        )

    # ------------------------------------------------------------ persistence

    async def _append_decision_row(
        self,
        *,
        owner_id: int,
        novel_id: int,
        set_row: SceneCandidateSetRow,
        decision: SceneReviewDecisionInput,
        to_state: KeySceneReviewState,
        details: dict,
        set_target_state: str | None = None,
    ) -> SceneCandidateSetRow:
        """Append one decision row; only the set projection may be updated.

        Candidate rows are immutable (D-31-01 append-only guard); their
        effective review state is derived from the decision history instead.
        A freeze updates the set row's review-state projection only.
        """
        row = SceneReviewDecisionRow(
            owner_id=owner_id,
            novel_id=novel_id,
            set_id=set_row.id,
            decision_key=decision.decision_key,
            action=decision.action.value,
            actor_source=decision.actor_source.value,
            actor=decision.actor,
            reason=decision.reason,
            from_review_state=decision.from_review_state.value,
            to_review_state=to_state.value,
            candidate_key=decision.candidate_key,
            details=details,
        )
        self._session.add(row)
        if set_target_state is not None:
            set_row.review_state = set_target_state
        try:
            await self._session.flush()
        except IntegrityError:
            # Concurrent duplicate decision_key: roll back and replay the winner.
            await self._session.rollback()
            existing = await self._decision(
                owner_id=owner_id,
                novel_id=novel_id,
                set_id=set_row.id,
                decision_key=decision.decision_key,
            )
            if existing is None:
                raise KeySceneReviewConflict(
                    "review decision race: existing row not found after rollback"
                )
            return set_row
        return set_row

    # --------------------------------------------------------------- queries

    async def _set(
        self, *, owner_id: int, novel_id: int, set_id: int
    ) -> SceneCandidateSetRow | None:
        return await self._session.scalar(
            select(SceneCandidateSetRow).where(
                SceneCandidateSetRow.owner_id == owner_id,
                SceneCandidateSetRow.novel_id == novel_id,
                SceneCandidateSetRow.id == set_id,
            )
        )

    async def _candidate(
        self,
        *,
        owner_id: int,
        novel_id: int,
        set_id: int,
        candidate_key: str,
    ) -> SceneCandidateRow | None:
        return await self._session.scalar(
            select(SceneCandidateRow).where(
                SceneCandidateRow.owner_id == owner_id,
                SceneCandidateRow.novel_id == novel_id,
                SceneCandidateRow.set_id == set_id,
                SceneCandidateRow.candidate_key == candidate_key,
            )
        )

    async def _decision(
        self,
        *,
        owner_id: int,
        novel_id: int,
        set_id: int,
        decision_key: str,
    ) -> SceneReviewDecisionRow | None:
        return await self._session.scalar(
            select(SceneReviewDecisionRow).where(
                SceneReviewDecisionRow.owner_id == owner_id,
                SceneReviewDecisionRow.novel_id == novel_id,
                SceneReviewDecisionRow.set_id == set_id,
                SceneReviewDecisionRow.decision_key == decision_key,
            )
        )

    async def _candidate_effective_states(
        self,
        *,
        owner_id: int,
        novel_id: int,
        set_id: int,
    ) -> dict[str, KeySceneReviewState]:
        """Effective per-candidate states derived from the set's decisions."""
        decision_rows = (
            await self._session.scalars(
                select(SceneReviewDecisionRow)
                .where(
                    SceneReviewDecisionRow.owner_id == owner_id,
                    SceneReviewDecisionRow.novel_id == novel_id,
                    SceneReviewDecisionRow.set_id == set_id,
                )
                .order_by(SceneReviewDecisionRow.id.asc())
            )
        ).all()
        return derive_candidate_review_states(decision_rows)

    @staticmethod
    def _require_scope(*, owner_id: int, novel_id: int) -> None:
        values = (owner_id, novel_id)
        if any(type(value) is not int or value <= 0 for value in values):
            raise KeySceneReviewConflict(
                "scope identifiers must be explicit positive integers"
            )


# ---------------------------------------------------------------------------
# Owner-scoped frozen set view builder
# ---------------------------------------------------------------------------


async def build_frozen_set_view(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    set_id: int,
) -> FrozenKeySceneSetView | None:
    """Frozen set read envelope: approved candidates only + frozen manifest.

    Returns ``None`` when the set is outside the explicit owner/novel scope so
    cross-owner probes are indistinguishable from "not found". The view carries
    the full set lineage plus the recomputed frozen manifest hash; downstream
    Phase 32 consumes this approved subset.
    """
    set_row = await session.scalar(
        select(SceneCandidateSetRow).where(
            SceneCandidateSetRow.owner_id == owner_id,
            SceneCandidateSetRow.novel_id == novel_id,
            SceneCandidateSetRow.id == set_id,
        )
    )
    if set_row is None:
        return None

    candidate_rows = (
        await session.scalars(
            select(SceneCandidateRow)
            .where(
                SceneCandidateRow.owner_id == owner_id,
                SceneCandidateRow.novel_id == novel_id,
                SceneCandidateRow.set_id == set_id,
            )
            .order_by(SceneCandidateRow.candidate_order.asc())
        )
    ).all()
    evidence_rows = (
        await session.scalars(
            select(SceneEvidenceRangeRow)
            .where(
                SceneEvidenceRangeRow.owner_id == owner_id,
                SceneEvidenceRangeRow.novel_id == novel_id,
                SceneEvidenceRangeRow.set_id == set_id,
            )
            .order_by(SceneEvidenceRangeRow.id.asc())
        )
    ).all()
    decision_rows = (
        await session.scalars(
            select(SceneReviewDecisionRow)
            .where(
                SceneReviewDecisionRow.owner_id == owner_id,
                SceneReviewDecisionRow.novel_id == novel_id,
                SceneReviewDecisionRow.set_id == set_id,
            )
            .order_by(SceneReviewDecisionRow.id.asc())
        )
    ).all()

    evidence_by_candidate: dict[int, list[SceneEvidenceRangeView]] = {}
    for row in evidence_rows:
        evidence_by_candidate.setdefault(row.candidate_id, []).append(
            SceneEvidenceRangeView(
                evidence_key=row.evidence_key,
                source_snapshot_id=row.source_snapshot_id,
                source_snapshot_hash=row.source_snapshot_hash,
                chapter_id=row.chapter_id,
                chapter_number=row.chapter_number,
                source_start=row.source_start,
                source_end=row.source_end,
                content_hash=row.content_hash,
                excerpt=row.excerpt,
                cutoff_chapter=row.cutoff_chapter,
            )
        )

    # Candidate rows are immutable; the effective approved subset is derived
    # from the append-only decisions (D-31-04).
    effective_states = derive_candidate_review_states(decision_rows)
    approved = [
        row
        for row in candidate_rows
        if effective_states.get(
            row.candidate_key, KeySceneReviewState(row.review_state)
        )
        is KeySceneReviewState.APPROVED
    ]
    candidate_views = [
        SceneCandidateView(
            candidate_key=row.candidate_key,
            candidate_order=row.candidate_order,
            scene_id=row.scene_id,
            chapter_id=row.chapter_id,
            chapter_number=row.chapter_number,
            source_start=row.source_start,
            source_end=row.source_end,
            source_hash=row.source_hash,
            coordinates=SceneCoordinates.model_validate(row.coordinates),
            spoiler_cutoff=row.spoiler_cutoff,
            salience_reasons=[
                SalienceReason.model_validate(reason)
                for reason in (row.salience_reasons or [])
            ],
            score_total=row.score_total,
            score_breakdown=row.score_breakdown or {},
            diversity_key=row.diversity_key,
            detector_id=row.detector_id,
            detector_version=row.detector_version,
            policy_hash=row.policy_hash,
            evidence_ranges=evidence_by_candidate.get(row.id, []),
            heuristic_signal=(
                None
                if row.heuristic_signal is None
                else SpeakerDialogueHeuristicSignal.model_validate(row.heuristic_signal)
            ),
            review_state=effective_states.get(
                row.candidate_key, KeySceneReviewState(row.review_state)
            ),
        )
        for row in approved
    ]

    return FrozenKeySceneSetView(
        id=set_row.id,
        owner_id=set_row.owner_id,
        novel_id=set_row.novel_id,
        version_key=set_row.version_key,
        revision_number=set_row.revision_number,
        parent_set_id=set_row.parent_set_id,
        source_snapshot_id=set_row.source_snapshot_id,
        source_snapshot_hash=set_row.source_snapshot_hash,
        cutoff_chapter=set_row.cutoff_chapter,
        schema_version=set_row.schema_version,
        schema_hash=set_row.schema_hash,
        policy_hash=set_row.policy_hash,
        detector_id=set_row.detector_id,
        detector_version=set_row.detector_version,
        manifest_hash=recompute_frozen_manifest_hash(
            set_row=set_row, approved_candidates=approved
        ),
        approved_visual_bible_revision_id=set_row.approved_visual_bible_revision_id,
        approved_visual_bible_revision_hash=set_row.approved_visual_bible_revision_hash,
        review_state=KeySceneReviewState(set_row.review_state),
        candidates=candidate_views,
        review_decisions=[
            SceneReviewDecisionView(
                decision_key=row.decision_key,
                action=row.action,
                actor_source=row.actor_source,
                actor=row.actor,
                reason=row.reason,
                from_review_state=KeySceneReviewState(row.from_review_state),
                to_review_state=KeySceneReviewState(row.to_review_state),
                candidate_key=row.candidate_key,
            )
            for row in decision_rows
        ],
    )


async def load_frozen_set_view(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    set_id: int,
) -> FrozenKeySceneSetView:
    """Owner-scoped frozen set read; 404-equivalent on scope."""
    view = await build_frozen_set_view(
        session, owner_id=owner_id, novel_id=novel_id, set_id=set_id
    )
    if view is None:
        raise KeySceneReviewNotFound(
            "candidate set not found in the explicit owner/novel scope"
        )
    return view
