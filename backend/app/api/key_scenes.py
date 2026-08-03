"""Owner-scoped key-scene candidate API (Phase 31-02, REQ-VIS-02/06).

Candidate-only, evidence-first, spoiler-safe Artifact endpoints:

- ``GET  /api/novels/{novel_id}/key-scenes`` — list candidate sets.
- ``POST /api/novels/{novel_id}/key-scenes/generate`` — generate a candidate
  set server-side from the owning novel's persisted chapter hierarchy. Scope,
  source snapshot, spoiler cutoff, evidence/hash lineage, approved Visual
  Bible revision and candidate-only gates all run on the server.
- ``GET  /api/novels/{novel_id}/key-scenes/{set_id}`` — one full candidate
  envelope (ordered candidates + diversity keys + evidence refs + non-authoritative
  ``speaker_dialogue_signal`` metadata + review decisions).

Every route uses ``require_owned_novel``; a set outside the caller's
owner/novel scope is indistinguishable from "not found" (no owner leak). The
server computes rankings — the browser never re-scores — and no route promotes
anything to Canon or rewrites source text (D-31-01).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_owned_novel
from app.core.database import get_db
from app.core.security import require_user
from app.models import Novel, User
from app.schemas.key_scene import (
    FrozenKeySceneSetView,
    KeySceneActorSource,
    KeySceneGateError,
    KeySceneReviewAction,
    KeySceneReviewState,
    StrictKeySceneModel,
    SceneCandidateSetView,
    SceneCoordinates,
    SceneReviewDecisionInput,
)
from app.services.key_scenes.candidates import (
    CandidateGenerationInput,
    CandidateService,
    KeySceneCandidateError,
    KeySceneCandidateNotFound,
    load_candidate_set_view,
    list_candidate_sets,
)
from app.services.key_scenes.review import (
    KeySceneReviewError,
    KeySceneReviewNotFound,
    KeySceneReviewService,
    load_frozen_set_view,
)
from app.services.key_scenes.scoring import DEFAULT_SCENE_POLICY

router = APIRouter(dependencies=[Depends(require_user)])


class KeySceneWireModel(StrictKeySceneModel):
    model_config = ConfigDict(extra="forbid")


class KeySceneGenerateRequest(KeySceneWireModel):
    """Explicit generation request; scope comes from the path, never the body."""

    version_key: str = Field(min_length=1, max_length=120)
    cutoff_chapter: int = Field(ge=1)
    source_snapshot_id: str | None = Field(default=None, min_length=1, max_length=160)
    # Optional per-scene inputs (coordinates are source-verified by callers;
    # embedding/arc signals are advisory inputs among many, never decisive).
    coordinates: dict[str, SceneCoordinates] = Field(default_factory=dict)
    embedding_signals: dict[str, float] = Field(default_factory=dict)
    arc_impact_signals: dict[str, float] = Field(default_factory=dict)
    approved_visual_bible_revision_id: int | None = Field(default=None, gt=0)
    approved_visual_bible_revision_hash: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    max_candidates: int | None = Field(default=None, ge=1, le=256)

    @model_validator(mode="after")
    def validate_signal_ranges(self) -> "KeySceneGenerateRequest":
        for value in self.embedding_signals.values():
            if not (0.0 <= value <= 1.0):
                raise ValueError("embedding_signals values must be in [0, 1]")
        for value in self.arc_impact_signals.values():
            if not (0.0 <= value <= 1.0):
                raise ValueError("arc_impact_signals values must be in [0, 1]")
        if (self.approved_visual_bible_revision_id is None) != (
            self.approved_visual_bible_revision_hash is None
        ):
            raise ValueError(
                "approved_visual_bible_revision_id and "
                "approved_visual_bible_revision_hash must be provided together"
            )
        return self


class KeySceneSetListResponse(KeySceneWireModel):
    items: list[SceneCandidateSetView]
    total: int


class KeySceneGenerateResponse(KeySceneWireModel):
    set: SceneCandidateSetView
    replayed: bool = False


class KeySceneReviewRequest(KeySceneWireModel):
    """One explicit candidate review action; scope comes from the path."""

    decision_key: str = Field(min_length=1, max_length=160)
    action: KeySceneReviewAction
    actor_source: KeySceneActorSource
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)
    from_review_state: KeySceneReviewState
    candidate_key: str = Field(min_length=1, max_length=180)


class KeySceneReviewResponse(KeySceneWireModel):
    set: SceneCandidateSetView


class KeySceneFreezeRequest(KeySceneWireModel):
    """Explicit set freeze; the decision key is server-derived (idempotent)."""

    actor_source: KeySceneActorSource = KeySceneActorSource.HUMAN
    actor: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1000)


class KeySceneFreezeResponse(KeySceneWireModel):
    set: SceneCandidateSetView
    frozen: FrozenKeySceneSetView


def _not_found() -> HTTPException:
    # Identical to a missing novel so cross-owner probes cannot learn anything.
    return HTTPException(status_code=404, detail="小说不存在")


# ---------------------------------------------------------------------------
# Read routes (candidate-only, spoiler-safe)
# ---------------------------------------------------------------------------


@router.get("/{novel_id}/key-scenes", response_model=KeySceneSetListResponse)
async def get_key_scene_sets(
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """List every candidate set for the owned novel (oldest first)."""
    items = await list_candidate_sets(
        db, owner_id=current_user.id, novel_id=novel.id
    )
    return KeySceneSetListResponse(items=items, total=len(items))


@router.get(
    "/{novel_id}/key-scenes/{set_id}",
    response_model=SceneCandidateSetView,
)
async def get_key_scene_set(
    set_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Full candidate envelope: ordered candidates, evidence, reasons, heuristic."""
    try:
        return await load_candidate_set_view(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            set_id=set_id,
        )
    except KeySceneCandidateNotFound:
        raise _not_found() from None


# ---------------------------------------------------------------------------
# Generate route (server-computed, candidate-only)
# ---------------------------------------------------------------------------


@router.post(
    "/{novel_id}/key-scenes/generate",
    response_model=KeySceneGenerateResponse,
    status_code=201,
)
async def generate_key_scene_set(
    payload: KeySceneGenerateRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Generate a candidate set from the owning novel's source (server-side)."""
    # Capture the scope as plain ints before any write seam runs: an idempotent
    # replay rolls the session back internally, which would expire ORM objects
    # and make later attribute access lazy-load outside a greenlet context.
    owner_id = current_user.id
    novel_id = novel.id
    try:
        persisted = await CandidateService(db, policy=DEFAULT_SCENE_POLICY).generate(
            owner_id=owner_id,
            novel_id=novel_id,
            input_=CandidateGenerationInput(
                version_key=payload.version_key,
                cutoff_chapter=payload.cutoff_chapter,
                source_snapshot_id=payload.source_snapshot_id,
                coordinates=payload.coordinates,
                embedding_signals=payload.embedding_signals,
                arc_impact_signals=payload.arc_impact_signals,
                approved_visual_bible_revision_id=(
                    payload.approved_visual_bible_revision_id
                ),
                approved_visual_bible_revision_hash=(
                    payload.approved_visual_bible_revision_hash
                ),
                max_candidates=payload.max_candidates,
            ),
        )
    except KeySceneCandidateNotFound:
        raise _not_found() from None
    except (KeySceneCandidateError, KeySceneGateError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    set_id = persisted.set.id
    view = await load_candidate_set_view(
        db,
        owner_id=owner_id,
        novel_id=novel_id,
        set_id=set_id,
    )
    return KeySceneGenerateResponse(set=view, replayed=persisted.replayed)


# ---------------------------------------------------------------------------
# Review / freeze routes (explicit human action, append-only, idempotent)
# ---------------------------------------------------------------------------


@router.post(
    "/{novel_id}/key-scenes/{set_id}/review",
    response_model=KeySceneReviewResponse,
)
async def review_key_scene_candidate(
    set_id: int,
    payload: KeySceneReviewRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Append one explicit candidate review action (approve/reject/needs_relink).

    The server re-verifies owner/novel/set scope, candidate membership, the
    decision's ``from_review_state``, the legal transition and (for approvals)
    the persisted evidence gate. A repeated ``decision_key`` replays without a
    second decision. Rejected candidates remain in the append-only audit
    history and never become canon (D-31-04).
    """
    owner_id = current_user.id
    novel_id = novel.id
    decision = SceneReviewDecisionInput(
        owner_id=owner_id,
        novel_id=novel_id,
        set_id=set_id,
        decision_key=payload.decision_key,
        action=payload.action,
        actor_source=payload.actor_source,
        actor=payload.actor,
        reason=payload.reason,
        from_review_state=payload.from_review_state,
        candidate_key=payload.candidate_key,
    )
    review = KeySceneReviewService(db)
    try:
        await review.append_decision(
            owner_id=owner_id,
            novel_id=novel_id,
            decision=decision,
        )
    except (KeySceneReviewNotFound, KeySceneCandidateNotFound):
        raise _not_found() from None
    except (KeySceneGateError, KeySceneReviewError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        view = await load_candidate_set_view(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            set_id=set_id,
        )
    except KeySceneCandidateNotFound:
        raise _not_found() from None
    return KeySceneReviewResponse(set=view)


@router.post(
    "/{novel_id}/key-scenes/{set_id}/freeze",
    response_model=KeySceneFreezeResponse,
)
async def freeze_key_scene_set(
    set_id: int,
    payload: KeySceneFreezeRequest,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Freeze the key-scene set: explicit set-level approval with the freeze gate.

    The gate requires at least one approved candidate and re-verifies every
    approved candidate's persisted evidence lineage before the set moves to
    ``approved``. The frozen manifest is recomputed over the approved subset
    only — rejected/unresolved candidates never enter downstream Phase 32.
    """
    owner_id = current_user.id
    novel_id = novel.id
    review = KeySceneReviewService(db)
    try:
        await review.freeze(
            owner_id=owner_id,
            novel_id=novel_id,
            set_id=set_id,
            actor=payload.actor,
            reason=payload.reason,
        )
    except KeySceneReviewNotFound:
        raise _not_found() from None
    except (KeySceneGateError, KeySceneReviewError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        view = await load_candidate_set_view(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            set_id=set_id,
        )
        frozen = await load_frozen_set_view(
            db,
            owner_id=owner_id,
            novel_id=novel_id,
            set_id=set_id,
        )
    except (KeySceneCandidateNotFound, KeySceneReviewNotFound):
        raise _not_found() from None
    return KeySceneFreezeResponse(set=view, frozen=frozen)


@router.get(
    "/{novel_id}/key-scenes/{set_id}/frozen",
    response_model=FrozenKeySceneSetView,
)
async def get_frozen_key_scene_set(
    set_id: int,
    novel: Novel = Depends(require_owned_novel),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_user),
):
    """Read the frozen candidate subset (approved candidates + frozen manifest)."""
    try:
        return await load_frozen_set_view(
            db,
            owner_id=current_user.id,
            novel_id=novel.id,
            set_id=set_id,
        )
    except KeySceneReviewNotFound:
        raise _not_found() from None
