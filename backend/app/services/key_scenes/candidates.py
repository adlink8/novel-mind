"""Candidate set construction service (Phase 31-02, REQ-VIS-02/06).

D-31-01..D-31-05: scene candidates are evidence-first, candidate-only, derived
artifacts. This module owns the *candidate set* seam:

- ``CandidateService.generate`` runs the deterministic pipeline over the owning
  novel's persisted chapter hierarchy: load source snapshot → detect scene
  boundaries → server-side spoiler cutoff → multi-signal deterministic scoring
  (``scoring.KeySceneScorer``) → diversity-aware ranking
  (``scoring.rank_with_diversity``) → strict ``SceneCandidateSetContract`` →
  server-side gates (evidence lineage, heuristic-signal isolation, replayable
  manifest hash, approved Visual Bible revision) → append-only persistence with
  idempotent replay.
- ``CandidateService.list_sets`` / ``CandidateService.load_set_view`` are
  owner-scoped read seams (a set outside the caller's scope is indistinguishable
  from "not found").

The REQ-VIS-06 speaker/dialogue heuristic signal travels as diagnostic
candidate metadata only: it is never written into evidence ranges, never
becomes a citation or Canon authority and never justifies approval.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

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
    KEY_SCENE_SCHEMA_VERSION,
    KeySceneGateError,
    KeySceneReviewAction,
    KeySceneReviewState,
    SalienceReason,
    SceneCandidateContract,
    SceneCandidateSetContract,
    SceneCandidateSetView,
    SceneCandidateView,
    SceneCoordinates,
    SceneEvidenceRangeView,
    SceneReviewDecisionView,
    SpeakerDialogueHeuristicSignal,
    candidate_canonical_payload,
    candidate_content_hash,
    canonical_key_scene_hash,
    recompute_manifest_hash,
    set_manifest_payload,
    validate_candidate_set_contract,
)
from app.services.key_scenes.boundaries import (
    SceneBoundary,
    SceneBoundaryService,
    build_candidate,
    build_evidence_range,
    detect_chapter_boundaries,
    filter_by_cutoff,
)
from app.services.key_scenes.scoring import (
    DEFAULT_SCENE_POLICY,
    KeySceneScorer,
    KeySceneScoringPolicy,
    SceneScoreInput,
    ScoredScene,
    policy_hash,
    rank_with_diversity,
)

# Deterministic detector lineage for the combined key-scene detector.
KEY_SCENE_DETECTOR_ID = "key-scene.v1"
KEY_SCENE_DETECTOR_VERSION = "1.0.0"

# Deterministic schema hash binding the strict wire contract (replayable).
KEY_SCENE_SCHEMA_HASH = canonical_key_scene_hash(
    {
        "kind": "key_scene.schema",
        "schema_version": KEY_SCENE_SCHEMA_VERSION,
    }
)


class KeySceneCandidateError(ValueError):
    """Base class for fail-closed key-scene candidate errors."""


class KeySceneCandidateNotFound(KeySceneCandidateError):
    """A candidate set is outside the explicit owner/novel scope (404-equivalent)."""


class KeySceneCandidateConflict(KeySceneCandidateError):
    """A conflicting retry of an existing immutable version_key."""


@dataclass(frozen=True)
class CandidateGenerationInput:
    """Server-side generation request (scope is derived from the caller)."""

    version_key: str
    cutoff_chapter: int
    source_snapshot_id: str | None = None
    coordinates: Mapping[str, SceneCoordinates] = field(default_factory=dict)
    embedding_signals: Mapping[str, float] = field(default_factory=dict)
    arc_impact_signals: Mapping[str, float] = field(default_factory=dict)
    approved_visual_bible_revision_id: int | None = None
    approved_visual_bible_revision_hash: str | None = None
    max_candidates: int | None = None


@dataclass(frozen=True)
class PersistedCandidateSet:
    """Candidate-set write result with the persisted set row."""

    set: SceneCandidateSetRow
    replayed: bool


def _set_idempotency_key(set_: SceneCandidateSetContract) -> str:
    return canonical_key_scene_hash(
        {
            "kind": "key_scene.set",
            "owner_id": set_.owner_id,
            "novel_id": set_.novel_id,
            "version_key": set_.version_key,
            "manifest_hash": set_.manifest_hash,
        }
    )


def derive_candidate_review_states(
    decision_rows: Sequence[SceneReviewDecisionRow],
) -> dict[str, KeySceneReviewState]:
    """Effective per-candidate review state derived from append-only decisions.

    Candidate rows are immutable (D-31-01 append-only event guard), so the
    candidate's effective review state is the ``to_review_state`` of the last
    (highest id) decision targeting that candidate; with no decision it stays
    ``candidate``. Rejected candidates remain auditable and never disappear.
    """
    states: dict[str, KeySceneReviewState] = {}
    for row in decision_rows:
        if row.candidate_key is not None:
            states[row.candidate_key] = KeySceneReviewState(row.to_review_state)
    return states


def _child_idempotency_key(
    *,
    kind: str,
    owner_id: int,
    novel_id: int,
    version_key: str,
    child_key: str,
    payload_hash: str,
) -> str:
    return canonical_key_scene_hash(
        {
            "kind": kind,
            "owner_id": owner_id,
            "novel_id": novel_id,
            "version_key": version_key,
            "key": child_key,
            "payload_hash": payload_hash,
        }
    )


def _scene_boundary_from_scored(
    item: ScoredScene, *, order_index: int
) -> SceneBoundary:
    return SceneBoundary(
        scene_id=item.scene_id,
        chapter_id=item.chapter_id,
        chapter_number=item.chapter_number,
        source_start=item.source_start,
        source_end=item.source_end,
        source_hash=item.source_hash,
        source_snapshot_hash=None,
        content=item.content,
        order_index=order_index,
    )


class CandidateService:
    """Owner-scoped candidate-set construction and read seams."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        policy: KeySceneScoringPolicy = DEFAULT_SCENE_POLICY,
        detector_id: str = KEY_SCENE_DETECTOR_ID,
        detector_version: str = KEY_SCENE_DETECTOR_VERSION,
    ) -> None:
        self._session = session
        self._policy = policy
        self._detector_id = detector_id
        self._detector_version = detector_version
        self._policy_hash_value = policy_hash(policy)

    # ------------------------------------------------------------------ generate

    async def generate(
        self,
        *,
        owner_id: int,
        novel_id: int,
        input_: CandidateGenerationInput,
    ) -> PersistedCandidateSet:
        """Run the full deterministic candidate pipeline and persist the set."""
        boundaries_service = SceneBoundaryService(self._session)
        novel = await boundaries_service.verify_novel_scope(
            owner_id=owner_id, novel_id=novel_id
        )
        if novel is None:
            raise KeySceneCandidateNotFound(
                "novel is not in the explicit owner/novel scope"
            )

        approval = await boundaries_service.verify_visual_bible_approval(
            owner_id=owner_id,
            novel_id=novel_id,
            approved_visual_bible_revision_id=input_.approved_visual_bible_revision_id,
            approved_visual_bible_revision_hash=input_.approved_visual_bible_revision_hash,
        )
        if not approval.ok:
            raise KeySceneGateError(f"{approval.reason_code}: {approval.detail}")

        snapshot_hash, chapters = await boundaries_service.load_source_snapshot(
            owner_id=owner_id, novel_id=novel_id
        )
        if not chapters:
            raise KeySceneGateError(
                "novel has no chapters; cannot generate key-scene candidates"
            )

        boundaries: list[SceneBoundary] = []
        for chapter in chapters:
            outcome = detect_chapter_boundaries(
                novel_id=novel_id,
                chapter_id=chapter.chapter_id,
                chapter_number=chapter.chapter_number,
                content=chapter.content,
                source_snapshot_hash=snapshot_hash,
            )
            kept = filter_by_cutoff(
                outcome.boundaries, cutoff_chapter=input_.cutoff_chapter
            )
            boundaries.extend(kept.kept)

        if not boundaries:
            raise KeySceneGateError("no scene boundaries survive the spoiler cutoff")

        scorer = KeySceneScorer(
            self._policy,
            detector_id=self._detector_id,
            detector_version=self._detector_version,
        )
        scored: list[ScoredScene] = []
        for boundary in boundaries:
            scored.append(
                scorer.score(
                    SceneScoreInput(
                        scene_id=boundary.scene_id,
                        chapter_id=boundary.chapter_id,
                        chapter_number=boundary.chapter_number,
                        source_start=boundary.source_start,
                        source_end=boundary.source_end,
                        source_hash=boundary.source_hash,
                        content=boundary.content,
                        coordinates=input_.coordinates.get(
                            boundary.scene_id, SceneCoordinates()
                        ),
                        embedding_similarity=input_.embedding_signals.get(
                            boundary.scene_id
                        ),
                        arc_impact_score=input_.arc_impact_signals.get(
                            boundary.scene_id
                        ),
                    )
                )
            )

        diversity = rank_with_diversity(scored, policy=self._policy)
        ranked: Sequence[ScoredScene] = diversity.ordered
        if input_.max_candidates is not None and input_.max_candidates < len(ranked):
            ranked = ranked[: input_.max_candidates]

        source_snapshot_id = input_.source_snapshot_id or f"ks-{input_.version_key}"
        candidates: list[SceneCandidateContract] = []
        for order, item in enumerate(ranked):
            boundary = _scene_boundary_from_scored(item, order_index=order)
            evidence = build_evidence_range(
                boundary,
                source_snapshot_id=source_snapshot_id,
                source_snapshot_hash=snapshot_hash,
                cutoff_chapter=input_.cutoff_chapter,
                evidence_key=f"ev-{item.scene_id[-22:]}",
            )
            candidates.append(
                build_candidate(
                    candidate_key=f"ks-{input_.version_key}-{order}",
                    candidate_order=order,
                    boundary=boundary,
                    coordinates=item.coordinates,
                    salience_reasons=list(item.salience_reasons),
                    score_total=item.score_total,
                    score_breakdown=item.score_breakdown,
                    diversity_key=item.diversity_key,
                    detector_id=self._detector_id,
                    detector_version=self._detector_version,
                    policy_hash=self._policy_hash_value,
                    evidence_range=evidence,
                    heuristic_signal=item.heuristic_signal,
                )
            )

        set_contract = SceneCandidateSetContract(
            schema_version=KEY_SCENE_SCHEMA_VERSION,
            artifact_kind="key_scene",
            owner_id=owner_id,
            novel_id=novel_id,
            version_key=input_.version_key,
            revision_number=1,
            parent_set_id=None,
            source_snapshot_id=source_snapshot_id,
            source_snapshot_hash=snapshot_hash,
            cutoff_chapter=input_.cutoff_chapter,
            schema_hash=KEY_SCENE_SCHEMA_HASH,
            policy_hash=self._policy_hash_value,
            detector_id=self._detector_id,
            detector_version=self._detector_version,
            manifest_hash="0" * 64,
            approved_visual_bible_revision_id=input_.approved_visual_bible_revision_id,
            approved_visual_bible_revision_hash=input_.approved_visual_bible_revision_hash,
            candidates=candidates,
            review_state=KeySceneReviewState.CANDIDATE,
        )
        set_contract = set_contract.model_copy(
            update={"manifest_hash": recompute_manifest_hash(set_contract)}
        )

        try:
            validate_candidate_set_contract(set_contract)
        except KeySceneGateError as exc:
            raise KeySceneGateError(str(exc)) from exc

        return await self._persist(
            owner_id=owner_id,
            novel_id=novel_id,
            set_contract=set_contract,
        )

    # ----------------------------------------------------------------- import

    async def import_set(
        self,
        *,
        owner_id: int,
        novel_id: int,
        set_contract: SceneCandidateSetContract,
    ) -> PersistedCandidateSet:
        """backfill 物化入口：导入已由 finalize 校验的候选集契约（candidate-only）。

        与 ``generate`` 的区别：不重跑确定性 pipeline，直接把产物契约持久化为
        candidate 行。仍执行 scope / visual-bible approval / 契约 / manifest
        hash 校验（fail closed），并复用 ``_persist`` 的幂等 replay。
        """
        boundaries_service = SceneBoundaryService(self._session)
        novel = await boundaries_service.verify_novel_scope(
            owner_id=owner_id, novel_id=novel_id
        )
        if novel is None:
            raise KeySceneCandidateNotFound(
                "novel is not in the explicit owner/novel scope"
            )
        if set_contract.owner_id != owner_id or set_contract.novel_id != novel_id:
            raise KeySceneGateError("set scope does not match request scope")

        approval = await boundaries_service.verify_visual_bible_approval(
            owner_id=owner_id,
            novel_id=novel_id,
            approved_visual_bible_revision_id=set_contract.approved_visual_bible_revision_id,
            approved_visual_bible_revision_hash=set_contract.approved_visual_bible_revision_hash,
        )
        if not approval.ok:
            raise KeySceneGateError(f"{approval.reason_code}: {approval.detail}")

        if recompute_manifest_hash(set_contract) != set_contract.manifest_hash:
            raise KeySceneGateError("manifest_hash_mismatch")
        try:
            validate_candidate_set_contract(set_contract)
        except KeySceneGateError as exc:
            raise KeySceneGateError(str(exc)) from exc
        return await self._persist(
            owner_id=owner_id,
            novel_id=novel_id,
            set_contract=set_contract,
        )

    # ----------------------------------------------------------------- persist

    async def _persist(
        self,
        *,
        owner_id: int,
        novel_id: int,
        set_contract: SceneCandidateSetContract,
    ) -> PersistedCandidateSet:
        projection_hash = recompute_manifest_hash(set_contract)
        set_row = SceneCandidateSetRow(
            owner_id=owner_id,
            novel_id=novel_id,
            version_key=set_contract.version_key,
            revision_number=set_contract.revision_number,
            parent_set_id=set_contract.parent_set_id,
            source_snapshot_id=set_contract.source_snapshot_id,
            source_snapshot_hash=set_contract.source_snapshot_hash,
            cutoff_chapter=set_contract.cutoff_chapter,
            review_state=KeySceneReviewState.CANDIDATE.value,
            schema_version=set_contract.schema_version,
            schema_hash=set_contract.schema_hash,
            policy_hash=set_contract.policy_hash,
            detector_id=set_contract.detector_id,
            detector_version=set_contract.detector_version,
            manifest_hash=set_contract.manifest_hash,
            approved_visual_bible_revision_id=set_contract.approved_visual_bible_revision_id,
            approved_visual_bible_revision_hash=set_contract.approved_visual_bible_revision_hash,
            canonical_payload=set_manifest_payload(set_contract),
            canonical_payload_hash=projection_hash,
            idempotency_key=_set_idempotency_key(set_contract),
            projection_hash=projection_hash,
        )
        self._session.add(set_row)
        try:
            await self._session.flush()
        except IntegrityError:
            # Concurrent duplicate version_key: roll back and replay the winner;
            # a conflicting retry still fails closed (never duplicates rows).
            await self._session.rollback()
            existing = await self._session.scalar(
                select(SceneCandidateSetRow).where(
                    SceneCandidateSetRow.owner_id == owner_id,
                    SceneCandidateSetRow.novel_id == novel_id,
                    SceneCandidateSetRow.version_key == set_contract.version_key,
                )
            )
            if existing is None:
                raise KeySceneCandidateConflict(
                    "candidate set race: existing row not found after rollback"
                )
            self._require_identical_set(existing, set_contract)
            return PersistedCandidateSet(set=existing, replayed=True)

        for order, candidate in enumerate(set_contract.candidates):
            payload = candidate_canonical_payload(candidate)
            payload_hash = candidate_content_hash(candidate)
            candidate_row = SceneCandidateRow(
                owner_id=owner_id,
                novel_id=novel_id,
                set_id=set_row.id,
                candidate_key=candidate.candidate_key,
                candidate_order=order,
                scene_id=candidate.scene_id,
                chapter_id=candidate.chapter_id,
                chapter_number=candidate.chapter_number,
                source_start=candidate.source_start,
                source_end=candidate.source_end,
                source_hash=candidate.source_hash,
                coordinates=candidate.coordinates.model_dump(mode="json"),
                spoiler_cutoff=candidate.spoiler_cutoff,
                salience_reasons=[
                    reason.model_dump(mode="json")
                    for reason in candidate.salience_reasons
                ],
                score_total=candidate.score_total,
                score_breakdown=candidate.score_breakdown,
                diversity_key=candidate.diversity_key,
                detector_id=candidate.detector_id,
                detector_version=candidate.detector_version,
                policy_hash=candidate.policy_hash,
                review_state=KeySceneReviewState.CANDIDATE.value,
                heuristic_signal=(
                    None
                    if candidate.heuristic_signal is None
                    else candidate.heuristic_signal.model_dump(mode="json")
                ),
                canonical_payload=payload,
                canonical_payload_hash=payload_hash,
                idempotency_key=_child_idempotency_key(
                    kind="key_scene.candidate",
                    owner_id=owner_id,
                    novel_id=novel_id,
                    version_key=set_contract.version_key,
                    child_key=candidate.candidate_key,
                    payload_hash=payload_hash,
                ),
                projection_hash=projection_hash,
                schema_version=set_contract.schema_version,
            )
            self._session.add(candidate_row)
            await self._session.flush()

            for ref in candidate.evidence_ranges:
                self._session.add(
                    SceneEvidenceRangeRow(
                        owner_id=owner_id,
                        novel_id=novel_id,
                        set_id=set_row.id,
                        candidate_id=candidate_row.id,
                        evidence_key=ref.evidence_key,
                        source_snapshot_id=ref.source_snapshot_id,
                        source_snapshot_hash=ref.source_snapshot_hash,
                        chapter_id=ref.chapter_id,
                        chapter_number=ref.chapter_number,
                        source_start=ref.source_start,
                        source_end=ref.source_end,
                        content_hash=ref.content_hash,
                        excerpt=ref.excerpt,
                        cutoff_chapter=ref.cutoff_chapter,
                        idempotency_key=_child_idempotency_key(
                            kind="key_scene.evidence",
                            owner_id=owner_id,
                            novel_id=novel_id,
                            version_key=set_contract.version_key,
                            child_key=(f"{candidate.candidate_key}:{ref.evidence_key}"),
                            payload_hash=ref.content_hash,
                        ),
                    )
                )
        await self._session.flush()
        return PersistedCandidateSet(set=set_row, replayed=False)

    @staticmethod
    def _require_identical_set(
        existing: SceneCandidateSetRow,
        set_contract: SceneCandidateSetContract,
    ) -> None:
        if (
            existing.canonical_payload_hash != recompute_manifest_hash(set_contract)
            or existing.manifest_hash != set_contract.manifest_hash
            or existing.source_snapshot_hash != set_contract.source_snapshot_hash
            or existing.cutoff_chapter != set_contract.cutoff_chapter
        ):
            raise KeySceneCandidateConflict(
                "conflicting candidate-set retry: version_key already exists "
                "with different immutable content"
            )

    # ----------------------------------------------------------- read seams

    async def list_sets(
        self,
        *,
        owner_id: int,
        novel_id: int,
    ) -> list[SceneCandidateSetView]:
        rows = (
            await self._session.scalars(
                select(SceneCandidateSetRow)
                .where(
                    SceneCandidateSetRow.owner_id == owner_id,
                    SceneCandidateSetRow.novel_id == novel_id,
                )
                .order_by(SceneCandidateSetRow.id.asc())
            )
        ).all()
        return [
            await self.load_set_view(
                owner_id=owner_id, novel_id=novel_id, set_id=row.id
            )
            for row in rows
        ]

    async def load_set_view(
        self,
        *,
        owner_id: int,
        novel_id: int,
        set_id: int,
    ) -> SceneCandidateSetView:
        set_row = await self._session.scalar(
            select(SceneCandidateSetRow).where(
                SceneCandidateSetRow.owner_id == owner_id,
                SceneCandidateSetRow.novel_id == novel_id,
                SceneCandidateSetRow.id == set_id,
            )
        )
        if set_row is None:
            raise KeySceneCandidateNotFound(
                "candidate set not found in the explicit owner/novel scope"
            )

        candidate_rows = (
            await self._session.scalars(
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
            await self._session.scalars(
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

        effective_states = derive_candidate_review_states(decision_rows)

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
                    else SpeakerDialogueHeuristicSignal.model_validate(
                        row.heuristic_signal
                    )
                ),
                # Candidate rows are immutable; the effective review state is
                # derived from the append-only decisions (D-31-04).
                review_state=effective_states.get(
                    row.candidate_key, KeySceneReviewState(row.review_state)
                ),
            )
            for row in candidate_rows
        ]

        decision_views = [
            SceneReviewDecisionView(
                decision_key=row.decision_key,
                action=KeySceneReviewAction(row.action),
                actor_source=row.actor_source,
                actor=row.actor,
                reason=row.reason,
                from_review_state=KeySceneReviewState(row.from_review_state),
                to_review_state=KeySceneReviewState(row.to_review_state),
                candidate_key=row.candidate_key,
            )
            for row in decision_rows
        ]

        return SceneCandidateSetView(
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
            manifest_hash=set_row.manifest_hash,
            approved_visual_bible_revision_id=set_row.approved_visual_bible_revision_id,
            approved_visual_bible_revision_hash=set_row.approved_visual_bible_revision_hash,
            review_state=KeySceneReviewState(set_row.review_state),
            candidates=candidate_views,
            review_decisions=decision_views,
        )


async def list_candidate_sets(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
) -> list[SceneCandidateSetView]:
    return await CandidateService(session).list_sets(
        owner_id=owner_id, novel_id=novel_id
    )


async def load_candidate_set_view(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    set_id: int,
) -> SceneCandidateSetView:
    return await CandidateService(session).load_set_view(
        owner_id=owner_id, novel_id=novel_id, set_id=set_id
    )
