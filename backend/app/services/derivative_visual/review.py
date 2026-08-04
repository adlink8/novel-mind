"""Deterministic derivative candidate asset review seam (Phase 38-04).

D-38-03 / REQ-FORK-04 / T-38-04-01 / T-38-04-02: a generated derivative visual
asset only changes review state through an explicit, append-only
approve/reject/supersede action. This module is the **independent review seam**
the API review panel calls directly:

- ``review_candidate_asset`` — one deterministic review orchestration: owner-
  scoped load -> ``from_review_state`` validation -> idempotent review event ->
  the same ``DerivativeVisualAssetView`` / ``PublishedDerivativeVisualAsset``
  envelopes 38-03 already exposes. Only an explicit approval ever moves a
  candidate to ``approved``; a ``blocked`` candidate (identity drift /
  undeclared divergence) has an empty legal transition set so approve/reject/
  supersede all fail closed.
- ``load_review_candidate`` / ``list_review_candidates`` — owner-scoped read
  seams for the review panel (any review state; owner/novel must be explicit
  positive integers). A candidate outside the caller's owner/novel scope is an
  identical 404-equivalent (``DerivativeCandidateScopeError``).
- Source-hash immutability (T-38-04-02): this seam never writes an Original
  Visual Bible row and never recomputes a candidate's frozen source/identity
  lineage — those were pinned by the 38-03 store/consistency gates, so a
  mutated source hash can never replay an approval here.

The state machine is NOT re-created here: the transition vocabulary and
``LEGAL_DERIVATIVE_ASSET_TRANSITIONS`` / ``derivative_asset_review_state_after``
from ``schemas/derivative_visual_asset.py`` remain the single source of truth.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.derivative_visual import (
    DerivativeVisualCandidateAsset,
    DerivativeVisualVersion,
)
from app.schemas.derivative_visual_asset import (
    DerivativeAssetReviewEventInput,
    DerivativeVisualAssetState,
    DerivativeVisualAssetView,
    PublishedDerivativeVisualAsset,
    is_legal_derivative_asset_review_action,
)
from app.services.derivative_visual.assets import (
    DerivativeAssetReviewError,
    DerivativeCandidateScopeError,
    apply_derivative_asset_review,
    load_candidate,
)
from app.services.derivative_visual.published_assets import (
    derivative_asset_view,
    published_asset_view,
)


class DerivativeReviewSeamError(ValueError):
    """Fail-closed review orchestration violation (T-38-04-01).

    The client can never bypass the deterministic state machine: an illegal
    action or a ``from_review_state`` that does not match the stored candidate
    state fails closed here before any event is appended.
    """


class DerivativeReviewCandidateNotFound(DerivativeReviewSeamError):
    """A candidate outside the explicit owner/novel scope (404-equivalent)."""


def _require_scope(*, owner_id: int, novel_id: int) -> None:
    values = (owner_id, novel_id)
    if any(type(value) is not int or value <= 0 for value in values):
        raise DerivativeReviewSeamError(
            "scope identifiers must be explicit positive integers"
        )


# ---------------------------------------------------------------------------
# Review orchestration seam (explicit transition, fail-closed)
# ---------------------------------------------------------------------------


async def review_candidate_asset(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    event: DerivativeAssetReviewEventInput,
) -> DerivativeVisualAssetView | PublishedDerivativeVisualAsset:
    """Apply one explicit review action through the deterministic seam.

    Steps (D-38-03):
    1. owner-scoped load — a foreign/missing candidate is an identical
       ``DerivativeCandidateScopeError`` (404-equivalent);
    2. ``from_review_state`` must match the stored candidate state;
    3. the action must be a legal transition for that state — ``blocked`` has
       an empty legal action set, so approval always fails closed;
    4. append the idempotent review event (repeated ``event_key`` replays);
    5. return the 38-03 envelope: ``PublishedDerivativeVisualAsset`` when the
       resulting state is ``approved``, ``DerivativeVisualAssetView`` otherwise.

    Nothing here writes to the Original Visual Bible tables and nothing
    recomputes the frozen source/identity/divergence lineage (T-38-04-02).
    """
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    if event.owner_id != owner_id or event.novel_id != novel_id:
        raise DerivativeReviewSeamError(
            "review event scope does not match request scope"
        )
    try:
        candidate = await load_candidate(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            candidate_id=event.candidate_id,
        )
    except DerivativeCandidateScopeError as exc:
        raise DerivativeReviewCandidateNotFound(str(exc)) from exc

    if candidate.review_state != event.from_review_state.value:
        raise DerivativeReviewSeamError(
            f"review from_review_state {event.from_review_state.value!r} does not "
            f"match the candidate's current state {candidate.review_state!r}"
        )
    if not is_legal_derivative_asset_review_action(
        candidate.review_state, event.action
    ):
        raise DerivativeReviewSeamError(
            f"illegal derivative asset review action {event.action.value!r} from "
            f"state {candidate.review_state!r}"
        )

    try:
        row = await apply_derivative_asset_review(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            event=event,
        )
    except DerivativeAssetReviewError as exc:
        raise DerivativeReviewSeamError(str(exc)) from exc

    if row.review_state == DerivativeVisualAssetState.APPROVED.value:
        return await published_asset_view(db, row)
    return await derivative_asset_view(db, row)


# ---------------------------------------------------------------------------
# Owner-scoped read seams for the review panel
# ---------------------------------------------------------------------------


async def load_review_candidate(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    candidate_id: int,
) -> DerivativeVisualAssetView:
    """One candidate review detail (source refs, scores, divergence, events).

    A candidate outside the explicit owner/novel scope is an identical
    ``DerivativeReviewCandidateNotFound`` (404-equivalent) — no owner leakage.
    """
    try:
        candidate = await load_candidate(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            candidate_id=candidate_id,
        )
    except DerivativeCandidateScopeError as exc:
        raise DerivativeReviewCandidateNotFound(str(exc)) from exc
    return await derivative_asset_view(db, candidate)


async def list_review_candidates(
    db: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    project_id: int | None = None,
    fork_id: int | None = None,
    review_state: DerivativeVisualAssetState | str | None = None,
) -> list[DerivativeVisualAssetView]:
    """List candidates visible in the owner/novel scope (any review state).

    ``project_id``/``fork_id`` narrow the visual-fork lineage; ``review_state``
    narrows the panel queue when supplied. Every query is owner + novel scoped
    and owner/novel must be explicit positive integers (T-38-04-01).
    """
    _require_scope(owner_id=owner_id, novel_id=novel_id)
    query = (
        select(DerivativeVisualCandidateAsset)
        .join(
            DerivativeVisualVersion,
            (DerivativeVisualVersion.owner_id == DerivativeVisualCandidateAsset.owner_id)
            & (
                DerivativeVisualVersion.novel_id
                == DerivativeVisualCandidateAsset.novel_id
            )
            & (
                DerivativeVisualVersion.id
                == DerivativeVisualCandidateAsset.visual_version_id
            ),
        )
        .where(
            DerivativeVisualCandidateAsset.owner_id == owner_id,
            DerivativeVisualCandidateAsset.novel_id == novel_id,
        )
    )
    if project_id is not None:
        query = query.where(DerivativeVisualVersion.project_id == project_id)
    if fork_id is not None:
        query = query.where(DerivativeVisualVersion.fork_id == fork_id)
    if review_state is not None:
        query = query.where(
            DerivativeVisualCandidateAsset.review_state
            == DerivativeVisualAssetState(review_state).value
        )
    query = query.order_by(
        DerivativeVisualCandidateAsset.chapter_number.asc(),
        DerivativeVisualCandidateAsset.id.asc(),
    )
    rows = (await db.scalars(query)).all()
    return [await derivative_asset_view(db, row) for row in rows]


__all__ = [
    "DerivativeReviewCandidateNotFound",
    "DerivativeReviewSeamError",
    "list_review_candidates",
    "load_review_candidate",
    "review_candidate_asset",
]
