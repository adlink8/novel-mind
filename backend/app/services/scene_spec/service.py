"""Owner-scoped SceneSpec service seam (preview / create / read / diff).

Extracted from the scene-spec compiler (Phase 32-02, REQ-VIS-03): this module
owns the server-side gates — candidate-only frozen sets, approved Visual Bible
revision revalidation, snapshot/cutoff lineage, append-only persistence with
idempotent replay, stale-spec detection when the Visual Bible or source
snapshot drifts, and deterministic recompile diffs. preview never writes and
never calls a provider (Phase 32-04 boundary).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.key_scene import (
    SceneCandidate as SceneCandidateRow,
    SceneCandidateSet as SceneCandidateSetRow,
    SceneEvidenceRange as SceneEvidenceRangeRow,
)
from app.models.novel import Novel
from app.models.scene_spec import (
    SceneSpecDetail as SceneSpecDetailRow,
    SceneSpecEvidenceRef as SceneSpecEvidenceRefRow,
    SceneSpecNegativeConstraint as SceneSpecNegativeConstraintRow,
    SceneSpecUncertainty as SceneSpecUncertaintyRow,
    SceneSpecVersion as SceneSpecVersionRow,
)
from app.models.visual_bible import (
    VisualBibleVersion as VisualBibleVersionRow,
)
from app.schemas.key_scene import (
    SceneCandidateContract,
    SceneEvidenceRange,
    candidate_content_hash,
)
from app.schemas.scene_spec import (
    NegativeConstraint,
    SceneDetail,
    SceneSpecContract,
    SceneSpecView,
    SceneUncertainty,
    SpecEvidenceRef,
    SpecReviewState,
    VisualBibleRef,
    build_prompt_sections,
    canonical_scene_spec_hash,
    scene_spec_content_payload,
)
from app.schemas.visual_bible import VisualReviewState

from .compiler import (
    SCENE_SPEC_DEFAULT_POLICY_HASH,
    CompileUnresolved,
    SceneSpecCompileError,
    SceneSpecCompileInput,
    compile_scene_spec,
)
from .errors import SceneSpecConflict, SceneSpecNotFound, SceneSpecServiceError
from .visual_bible_loader import load_visual_bible_contract


# ---------------------------------------------------------------------------
# Owner-scoped service seam (preview / create / read / diff)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SceneSpecPreviewRequest:
    """Server-side preview/create request; scope comes from the caller path."""

    spec_key: str
    candidate_set_id: int
    candidate_key: str
    visual_bible_version_id: int
    source_snapshot_id: str
    revision_number: int = 1
    policy_hash: str = SCENE_SPEC_DEFAULT_POLICY_HASH
    config_hash: str | None = None


@dataclass(frozen=True)
class SceneSpecPreviewResult:
    """Preview outcome: no persistence and no provider call (Phase 32-04)."""

    spec: SceneSpecContract
    view: SceneSpecView
    unresolved: tuple[CompileUnresolved, ...] = ()
    provider_calls: int = 0


@dataclass(frozen=True)
class PersistedSceneSpec:
    """Create outcome: the persisted version row plus replay flag."""

    version: SceneSpecVersionRow
    view: SceneSpecView
    replayed: bool = False


@dataclass(frozen=True)
class SceneSpecDiffSection:
    """One canonical section whose rendering changed between two compiles."""

    section_key: str
    original: str | None = None
    current: str | None = None


@dataclass(frozen=True)
class SceneSpecDiffResult:
    """Deterministic recompile diff + stale marker (D-32-03)."""

    original_spec_hash: str
    current_spec_hash: str
    stale: bool
    same: bool
    changed_sections: tuple[SceneSpecDiffSection, ...] = ()


def _reconstruct_candidate(
    set_row: SceneCandidateSetRow,
    candidate_row: SceneCandidateRow,
    evidence_rows: Sequence[SceneEvidenceRangeRow],
) -> SceneCandidateContract:
    """Reconstruct the immutable SceneCandidateContract from persisted rows."""
    refs: list[SceneEvidenceRange] = []
    for row in evidence_rows:
        if row.candidate_id != candidate_row.id:
            continue
        refs.append(
            SceneEvidenceRange(
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
    return SceneCandidateContract(
        candidate_key=candidate_row.candidate_key,
        candidate_order=candidate_row.candidate_order,
        scene_id=candidate_row.scene_id,
        chapter_id=candidate_row.chapter_id,
        chapter_number=candidate_row.chapter_number,
        source_start=candidate_row.source_start,
        source_end=candidate_row.source_end,
        source_hash=candidate_row.source_hash,
        coordinates=candidate_row.coordinates,
        spoiler_cutoff=candidate_row.spoiler_cutoff,
        salience_reasons=candidate_row.salience_reasons or [],
        score_total=candidate_row.score_total,
        score_breakdown=candidate_row.score_breakdown or {},
        diversity_key=candidate_row.diversity_key,
        detector_id=candidate_row.detector_id,
        detector_version=candidate_row.detector_version,
        policy_hash=candidate_row.policy_hash,
        evidence_ranges=refs,
        heuristic_signal=candidate_row.heuristic_signal,
        review_state=candidate_row.review_state,
    )


class SceneSpecService:
    """Owner-scoped SceneSpec read/preview/create/diff seam."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------ compile seam

    async def compile_input(
        self,
        *,
        owner_id: int,
        novel_id: int,
        request: SceneSpecPreviewRequest,
    ) -> SceneSpecCompileInput:
        """Server-side revalidation before the pure compiler runs.

        Verifies, for the requesting owner:
        - novel ownership, the frozen candidate set and the approved candidate,
        - the approved Visual Bible revision the set was frozen against,
        - snapshot/cutoff lineage consistency between the set and the revision.
        """
        novel = await self._session.scalar(
            select(Novel).where(Novel.id == novel_id, Novel.owner_id == owner_id)
        )
        if novel is None:
            raise SceneSpecNotFound("novel is not in the explicit owner/novel scope")

        set_row = await self._session.scalar(
            select(SceneCandidateSetRow).where(
                SceneCandidateSetRow.owner_id == owner_id,
                SceneCandidateSetRow.novel_id == novel_id,
                SceneCandidateSetRow.id == request.candidate_set_id,
            )
        )
        if set_row is None or set_row.review_state != VisualReviewState.APPROVED.value:
            raise SceneSpecNotFound(
                "frozen candidate set not found in the explicit owner/novel scope"
            )

        candidate_row = await self._session.scalar(
            select(SceneCandidateRow).where(
                SceneCandidateRow.owner_id == owner_id,
                SceneCandidateRow.novel_id == novel_id,
                SceneCandidateRow.set_id == set_row.id,
                SceneCandidateRow.candidate_key == request.candidate_key,
            )
        )
        if candidate_row is None:
            raise SceneSpecNotFound(
                f"candidate {request.candidate_key!r} is not in the frozen set"
            )

        evidence_rows = (
            await self._session.scalars(
                select(SceneEvidenceRangeRow).where(
                    SceneEvidenceRangeRow.owner_id == owner_id,
                    SceneEvidenceRangeRow.novel_id == novel_id,
                    SceneEvidenceRangeRow.set_id == set_row.id,
                )
            )
        ).all()

        vb_version = await self._session.scalar(
            select(VisualBibleVersionRow).where(
                VisualBibleVersionRow.owner_id == owner_id,
                VisualBibleVersionRow.novel_id == novel_id,
                VisualBibleVersionRow.id == request.visual_bible_version_id,
            )
        )
        if (
            vb_version is None
            or vb_version.review_state != VisualReviewState.APPROVED.value
        ):
            raise SceneSpecNotFound(
                "approved Visual Bible revision not found in the explicit owner/novel scope"
            )
        if (
            set_row.approved_visual_bible_revision_id != vb_version.id
            or set_row.approved_visual_bible_revision_hash != vb_version.manifest_hash
        ):
            raise SceneSpecServiceError(
                "candidate set was not frozen against this Visual Bible revision; "
                "re-freeze the set against the current approved revision"
            )

        candidate = _reconstruct_candidate(set_row, candidate_row, evidence_rows)
        visual_bible = await load_visual_bible_contract(self._session, vb_version)

        return SceneSpecCompileInput(
            owner_id=owner_id,
            novel_id=novel_id,
            spec_key=request.spec_key,
            revision_number=request.revision_number,
            candidate=candidate,
            scene_candidate_hash=candidate_content_hash(candidate),
            scene_candidate_id=candidate_row.id,
            visual_bible=visual_bible,
            visual_bible_revision_hash=vb_version.manifest_hash,
            visual_bible_revision_id=vb_version.id,
            source_snapshot_id=request.source_snapshot_id,
            source_snapshot_hash=set_row.source_snapshot_hash,
            cutoff_chapter=set_row.cutoff_chapter,
            policy_hash=request.policy_hash,
            config_hash=request.config_hash,
        )

    # ---------------------------------------------------------------- preview

    async def preview(
        self,
        *,
        owner_id: int,
        novel_id: int,
        request: SceneSpecPreviewRequest,
    ) -> SceneSpecPreviewResult:
        """Compile a preview without persisting anything and without a provider."""
        compile_input = await self.compile_input(
            owner_id=owner_id, novel_id=novel_id, request=request
        )
        compiled = compile_scene_spec(compile_input)
        spec = compiled.spec
        # No persistence; the view is built from the contract.
        return SceneSpecPreviewResult(
            spec=spec,
            view=self._view_from_contract(spec),
            unresolved=compiled.unresolved,
            provider_calls=0,
        )

    # ------------------------------------------------------------------ create

    async def create(
        self,
        *,
        owner_id: int,
        novel_id: int,
        request: SceneSpecPreviewRequest,
    ) -> PersistedSceneSpec:
        """Compile and persist one immutable candidate spec (append-only, replay)."""
        compile_input = await self.compile_input(
            owner_id=owner_id, novel_id=novel_id, request=request
        )
        compiled = compile_scene_spec(compile_input)
        spec = compiled.spec

        existing = await self._spec(
            owner_id=owner_id, novel_id=novel_id, spec_key=spec.spec_key
        )
        if existing is not None:
            if existing.content_hash == spec.content_hash:
                return PersistedSceneSpec(
                    version=existing,
                    view=await self._view_from_rows(
                        owner_id=owner_id, novel_id=novel_id, spec=existing
                    ),
                    replayed=True,
                )
            raise SceneSpecConflict(
                f"conflicting spec retry: spec_key {spec.spec_key!r} already exists "
                "with different immutable content"
            )

        projection_hash = spec.content_hash
        version_row = SceneSpecVersionRow(
            owner_id=owner_id,
            novel_id=novel_id,
            spec_key=spec.spec_key,
            revision_number=spec.revision_number,
            scene_candidate_id=spec.scene_candidate_id,
            scene_candidate_hash=spec.scene_candidate_hash,
            visual_bible_revision_id=spec.visual_bible_revision_id,
            visual_bible_revision_hash=spec.visual_bible_revision_hash,
            source_snapshot_id=spec.source_snapshot_id,
            source_snapshot_hash=spec.source_snapshot_hash,
            cutoff_chapter=spec.cutoff_chapter,
            review_state=SpecReviewState.CANDIDATE.value,
            schema_version=spec.schema_version,
            schema_hash=spec.schema_hash,
            compiler_id=spec.compiler_id,
            compiler_version=spec.compiler_version,
            policy_hash=spec.policy_hash,
            config_hash=spec.config_hash,
            content_hash=spec.content_hash,
            canonical_payload=scene_spec_content_payload(spec),
            canonical_payload_hash=projection_hash,
            idempotency_key=self._version_idempotency_key(spec),
            projection_hash=projection_hash,
        )
        self._session.add(version_row)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            existing = await self._spec(
                owner_id=owner_id, novel_id=novel_id, spec_key=spec.spec_key
            )
            if existing is None:
                raise SceneSpecConflict(
                    "scene spec race: existing row not found after rollback"
                )
            if existing.content_hash != spec.content_hash:
                raise SceneSpecConflict(
                    f"conflicting spec retry: spec_key {spec.spec_key!r} already "
                    "exists with different immutable content"
                )
            return PersistedSceneSpec(
                version=existing,
                view=await self._view_from_rows(
                    owner_id=owner_id, novel_id=novel_id, spec=existing
                ),
                replayed=True,
            )

        await self._persist_content(
            owner_id=owner_id, novel_id=novel_id, spec=spec, version_row=version_row
        )
        await self._session.flush()
        return PersistedSceneSpec(
            version=version_row,
            view=await self._view_from_rows(
                owner_id=owner_id, novel_id=novel_id, spec=version_row
            ),
            replayed=False,
        )

    # ------------------------------------------------------------ read seams

    async def list(self, *, owner_id: int, novel_id: int) -> list[SceneSpecView]:
        rows = (
            await self._session.scalars(
                select(SceneSpecVersionRow)
                .where(
                    SceneSpecVersionRow.owner_id == owner_id,
                    SceneSpecVersionRow.novel_id == novel_id,
                )
                .order_by(SceneSpecVersionRow.id.asc())
            )
        ).all()
        return [
            await self._view_from_rows(owner_id=owner_id, novel_id=novel_id, spec=row)
            for row in rows
        ]

    async def load(
        self, *, owner_id: int, novel_id: int, spec_id: int
    ) -> tuple[SceneSpecView, bool]:
        """Return (view, stale). ``stale`` means the Visual Bible revision or the
        source snapshot the spec was compiled against no longer matches the
        novel's current approved revision / snapshot (D-32-03)."""
        spec = await self._spec_by_id(
            owner_id=owner_id, novel_id=novel_id, spec_id=spec_id
        )
        if spec is None:
            raise SceneSpecNotFound(
                "scene spec not found in the explicit owner/novel scope"
            )
        view = await self._view_from_rows(
            owner_id=owner_id, novel_id=novel_id, spec=spec
        )
        stale = await self._is_stale(owner_id=owner_id, novel_id=novel_id, spec=spec)
        return view, stale

    async def diff(
        self, *, owner_id: int, novel_id: int, spec_id: int
    ) -> SceneSpecDiffResult:
        """Recompile the same candidate against the current approved revision and
        diff the deterministic canonical sections. A changed Visual Bible or
        source snapshot marks the stored spec stale and shows the drift."""
        spec = await self._spec_by_id(
            owner_id=owner_id, novel_id=novel_id, spec_id=spec_id
        )
        if spec is None:
            raise SceneSpecNotFound(
                "scene spec not found in the explicit owner/novel scope"
            )

        current_hash, _ = await self._current_snapshot(
            owner_id=owner_id, novel_id=novel_id
        )
        latest_vb = await self._latest_approved_version(
            owner_id=owner_id, novel_id=novel_id
        )
        stale = (
            spec.visual_bible_revision_hash != latest_vb.manifest_hash
            or spec.source_snapshot_hash != current_hash
        )
        original_sections = self._sections_from_payload(spec.canonical_payload)

        if (
            latest_vb.id == spec.visual_bible_revision_id
            and current_hash == spec.source_snapshot_hash
        ):
            return SceneSpecDiffResult(
                original_spec_hash=spec.content_hash,
                current_spec_hash=spec.content_hash,
                stale=False,
                same=True,
                changed_sections=(),
            )

        # Re-run the frozen candidate compile against the current revision.
        if spec.scene_candidate_id is None:
            return SceneSpecDiffResult(
                original_spec_hash=spec.content_hash,
                current_spec_hash=spec.content_hash,
                stale=stale,
                same=False,
                changed_sections=(),
            )
        candidate_row = await self._session.scalar(
            select(SceneCandidateRow).where(
                SceneCandidateRow.owner_id == owner_id,
                SceneCandidateRow.novel_id == novel_id,
                SceneCandidateRow.id == spec.scene_candidate_id,
            )
        )
        if candidate_row is None:
            return SceneSpecDiffResult(
                original_spec_hash=spec.content_hash,
                current_spec_hash=spec.content_hash,
                stale=stale,
                same=False,
                changed_sections=(),
            )
        set_row = await self._session.scalar(
            select(SceneCandidateSetRow).where(
                SceneCandidateSetRow.owner_id == owner_id,
                SceneCandidateSetRow.novel_id == novel_id,
                SceneCandidateSetRow.id == candidate_row.set_id,
            )
        )
        if set_row is None:
            return SceneSpecDiffResult(
                original_spec_hash=spec.content_hash,
                current_spec_hash=spec.content_hash,
                stale=stale,
                same=False,
                changed_sections=(),
            )
        evidence_rows = (
            await self._session.scalars(
                select(SceneEvidenceRangeRow).where(
                    SceneEvidenceRangeRow.owner_id == owner_id,
                    SceneEvidenceRangeRow.novel_id == novel_id,
                    SceneEvidenceRangeRow.set_id == set_row.id,
                )
            )
        ).all()
        candidate = _reconstruct_candidate(set_row, candidate_row, evidence_rows)
        visual_bible = await load_visual_bible_contract(self._session, latest_vb)
        diff_input = SceneSpecCompileInput(
            owner_id=owner_id,
            novel_id=novel_id,
            spec_key=spec.spec_key,
            revision_number=spec.revision_number,
            candidate=candidate,
            scene_candidate_hash=candidate_content_hash(candidate),
            scene_candidate_id=candidate_row.id,
            visual_bible=visual_bible,
            visual_bible_revision_hash=latest_vb.manifest_hash,
            visual_bible_revision_id=latest_vb.id,
            source_snapshot_id=set_row.source_snapshot_id,
            source_snapshot_hash=set_row.source_snapshot_hash,
            cutoff_chapter=set_row.cutoff_chapter,
            policy_hash=spec.policy_hash,
            config_hash=spec.config_hash,
        )
        try:
            current = compile_scene_spec(diff_input).spec
        except (SceneSpecCompileError,):
            return SceneSpecDiffResult(
                original_spec_hash=spec.content_hash,
                current_spec_hash=spec.content_hash,
                stale=stale,
                same=False,
                changed_sections=(),
            )

        current_sections = build_prompt_sections(current)
        changed: list[SceneSpecDiffSection] = []
        all_keys = sorted(set(original_sections) | set(current_sections))
        for key in all_keys:
            if original_sections.get(key) != current_sections.get(key):
                changed.append(
                    SceneSpecDiffSection(
                        section_key=key,
                        original=original_sections.get(key),
                        current=current_sections.get(key),
                    )
                )
        return SceneSpecDiffResult(
            original_spec_hash=spec.content_hash,
            current_spec_hash=current.content_hash,
            stale=stale,
            same=not changed,
            changed_sections=tuple(changed),
        )

    # -------------------------------------------------------------- persistence

    async def _persist_content(
        self,
        *,
        owner_id: int,
        novel_id: int,
        spec: SceneSpecContract,
        version_row: SceneSpecVersionRow,
    ) -> None:
        from app.schemas.scene_spec import (
            constraint_canonical_payload,
            detail_canonical_payload,
        )

        detail_rows: dict[str, SceneSpecDetailRow] = {}
        for detail in spec.details:
            payload = detail_canonical_payload(detail)
            payload_hash = canonical_scene_spec_hash(payload)
            row = SceneSpecDetailRow(
                owner_id=owner_id,
                novel_id=novel_id,
                spec_id=version_row.id,
                detail_key=detail.detail_key,
                kind=detail.kind.value,
                source=detail.source.value,
                text=detail.text,
                author=detail.author,
                rationale=detail.rationale,
                spoiler_cutoff=detail.spoiler_cutoff,
                canonical_payload=payload,
                canonical_payload_hash=payload_hash,
                idempotency_key=canonical_scene_spec_hash(
                    {
                        "kind": "scene_spec.detail",
                        "owner_id": owner_id,
                        "novel_id": novel_id,
                        "spec_key": spec.spec_key,
                        "detail_key": detail.detail_key,
                        "payload_hash": payload_hash,
                    }
                ),
                projection_hash=spec.content_hash,
                schema_version=spec.schema_version,
            )
            self._session.add(row)
            await self._session.flush()
            detail_rows[detail.detail_key] = row
            for ref in detail.evidence_refs:
                self._session.add(
                    self._evidence_row(
                        owner_id=owner_id,
                        novel_id=novel_id,
                        version_row=version_row,
                        evidence_key=ref.evidence_key,
                        ref=ref,
                        spec_key=spec.spec_key,
                        detail_id=row.id,
                        constraint_id=None,
                    )
                )

        constraint_rows: dict[str, SceneSpecNegativeConstraintRow] = {}
        for constraint in spec.negative_constraints:
            payload = constraint_canonical_payload(constraint)
            payload_hash = canonical_scene_spec_hash(payload)
            row = SceneSpecNegativeConstraintRow(
                owner_id=owner_id,
                novel_id=novel_id,
                spec_id=version_row.id,
                constraint_key=constraint.constraint_key,
                scope=constraint.scope.value,
                source=constraint.source.value,
                text=constraint.text,
                author=constraint.author,
                rationale=constraint.rationale,
                spoiler_cutoff=constraint.spoiler_cutoff,
                canonical_payload=payload,
                canonical_payload_hash=payload_hash,
                idempotency_key=canonical_scene_spec_hash(
                    {
                        "kind": "scene_spec.constraint",
                        "owner_id": owner_id,
                        "novel_id": novel_id,
                        "spec_key": spec.spec_key,
                        "constraint_key": constraint.constraint_key,
                        "payload_hash": payload_hash,
                    }
                ),
                projection_hash=spec.content_hash,
                schema_version=spec.schema_version,
            )
            self._session.add(row)
            await self._session.flush()
            constraint_rows[constraint.constraint_key] = row
            for ref in constraint.evidence_refs:
                self._session.add(
                    self._evidence_row(
                        owner_id=owner_id,
                        novel_id=novel_id,
                        version_row=version_row,
                        evidence_key=ref.evidence_key,
                        ref=ref,
                        spec_key=spec.spec_key,
                        detail_id=None,
                        constraint_id=row.id,
                    )
                )

        for uncertainty in spec.uncertainties:
            self._session.add(
                SceneSpecUncertaintyRow(
                    owner_id=owner_id,
                    novel_id=novel_id,
                    spec_id=version_row.id,
                    uncertainty_key=uncertainty.uncertainty_key,
                    reason=uncertainty.reason.value,
                    detail=uncertainty.detail,
                    idempotency_key=canonical_scene_spec_hash(
                        {
                            "kind": "scene_spec.uncertainty",
                            "owner_id": owner_id,
                            "novel_id": novel_id,
                            "spec_key": spec.spec_key,
                            "uncertainty_key": uncertainty.uncertainty_key,
                        }
                    ),
                )
            )

    def _evidence_row(
        self,
        *,
        owner_id: int,
        novel_id: int,
        version_row: SceneSpecVersionRow,
        evidence_key: str,
        ref: SpecEvidenceRef,
        spec_key: str,
        detail_id: int | None,
        constraint_id: int | None,
    ) -> SceneSpecEvidenceRefRow:
        return SceneSpecEvidenceRefRow(
            owner_id=owner_id,
            novel_id=novel_id,
            spec_id=version_row.id,
            detail_id=detail_id,
            constraint_id=constraint_id,
            evidence_key=evidence_key,
            source_snapshot_id=ref.source_snapshot_id,
            source_snapshot_hash=ref.source_snapshot_hash,
            chapter_id=ref.chapter_id,
            chapter_number=ref.chapter_number,
            source_start=ref.source_start,
            source_end=ref.source_end,
            content_hash=ref.content_hash,
            excerpt=ref.excerpt,
            cutoff_chapter=ref.cutoff_chapter,
            idempotency_key=canonical_scene_spec_hash(
                {
                    "kind": "scene_spec.evidence",
                    "owner_id": owner_id,
                    "novel_id": novel_id,
                    "spec_key": spec_key,
                    "evidence_key": evidence_key,
                }
            ),
        )

    # ----------------------------------------------------------------- views

    async def _view_from_rows(
        self,
        *,
        owner_id: int,
        novel_id: int,
        spec: SceneSpecVersionRow,
    ) -> SceneSpecView:
        from app.schemas.scene_spec import (
            NegativeConstraintView,
            SceneDetailView,
            SceneUncertaintyView,
        )

        payload = spec.canonical_payload
        details = payload.get("details") or []
        constraints = payload.get("negative_constraints") or []
        uncertainties = payload.get("uncertainties") or []
        return SceneSpecView(
            id=spec.id,
            owner_id=spec.owner_id,
            novel_id=spec.novel_id,
            spec_key=spec.spec_key,
            revision_number=spec.revision_number,
            scene_candidate_hash=spec.scene_candidate_hash,
            scene_candidate_id=spec.scene_candidate_id,
            visual_bible_revision_hash=spec.visual_bible_revision_hash,
            visual_bible_revision_id=spec.visual_bible_revision_id,
            source_snapshot_id=spec.source_snapshot_id,
            source_snapshot_hash=spec.source_snapshot_hash,
            cutoff_chapter=spec.cutoff_chapter,
            schema_version=spec.schema_version,
            schema_hash=spec.schema_hash,
            compiler_id=spec.compiler_id,
            compiler_version=spec.compiler_version,
            policy_hash=spec.policy_hash,
            content_hash=spec.content_hash,
            review_state=spec.review_state,
            details=[
                SceneDetailView(
                    detail_key=item["detail_key"],
                    kind=item["kind"],
                    source=item["source"],
                    text=item["text"],
                    author=item.get("author"),
                    rationale=item.get("rationale"),
                    spoiler_cutoff=item["spoiler_cutoff"],
                    evidence_keys=list(item.get("evidence_keys") or []),
                    visual_bible_stable_ids=[
                        ref["stable_id"]
                        for ref in (item.get("visual_bible_refs") or [])
                    ],
                )
                for item in details
            ],
            negative_constraints=[
                NegativeConstraintView(
                    constraint_key=item["constraint_key"],
                    scope=item["scope"],
                    source=item["source"],
                    text=item["text"],
                    author=item.get("author"),
                    rationale=item.get("rationale"),
                    spoiler_cutoff=item["spoiler_cutoff"],
                )
                for item in constraints
            ],
            uncertainties=[
                SceneUncertaintyView(
                    uncertainty_key=item["uncertainty_key"],
                    reason=item["reason"],
                    detail=item["detail"],
                )
                for item in uncertainties
            ],
        )

    @staticmethod
    def _view_from_contract(spec: SceneSpecContract) -> SceneSpecView:
        from app.schemas.scene_spec import (
            NegativeConstraintView,
            SceneDetailView,
            SceneUncertaintyView,
        )

        return SceneSpecView(
            id=0,
            owner_id=spec.owner_id,
            novel_id=spec.novel_id,
            spec_key=spec.spec_key,
            revision_number=spec.revision_number,
            scene_candidate_hash=spec.scene_candidate_hash,
            scene_candidate_id=spec.scene_candidate_id,
            visual_bible_revision_hash=spec.visual_bible_revision_hash,
            visual_bible_revision_id=spec.visual_bible_revision_id,
            source_snapshot_id=spec.source_snapshot_id,
            source_snapshot_hash=spec.source_snapshot_hash,
            cutoff_chapter=spec.cutoff_chapter,
            schema_version=spec.schema_version,
            schema_hash=spec.schema_hash,
            compiler_id=spec.compiler_id,
            compiler_version=spec.compiler_version,
            policy_hash=spec.policy_hash,
            content_hash=spec.content_hash,
            review_state=spec.review_state,
            details=[
                SceneDetailView(
                    detail_key=detail.detail_key,
                    kind=detail.kind,
                    source=detail.source,
                    text=detail.text,
                    author=detail.author,
                    rationale=detail.rationale,
                    spoiler_cutoff=detail.spoiler_cutoff,
                    evidence_keys=[ref.evidence_key for ref in detail.evidence_refs],
                    visual_bible_stable_ids=[
                        ref.stable_id for ref in detail.visual_bible_refs
                    ],
                )
                for detail in spec.details
            ],
            negative_constraints=[
                NegativeConstraintView(
                    constraint_key=c.constraint_key,
                    scope=c.scope,
                    source=c.source,
                    text=c.text,
                    author=c.author,
                    rationale=c.rationale,
                    spoiler_cutoff=c.spoiler_cutoff,
                )
                for c in spec.negative_constraints
            ],
            uncertainties=[
                SceneUncertaintyView(
                    uncertainty_key=u.uncertainty_key,
                    reason=u.reason,
                    detail=u.detail,
                )
                for u in spec.uncertainties
            ],
        )

    @staticmethod
    def _sections_from_payload(payload: Mapping[str, Any]) -> dict[str, str]:
        from app.schemas.scene_spec import build_prompt_sections

        details = [
            SceneDetail(
                detail_key=item["detail_key"],
                kind=item["kind"],
                source=item["source"],
                text=item["text"],
                author=item.get("author"),
                rationale=item.get("rationale"),
                evidence_refs=[
                    SpecEvidenceRef(
                        evidence_key=key,
                        source_snapshot_id=payload.get("source_snapshot_id") or "ss",
                        source_snapshot_hash="0" * 64,
                        chapter_id=1,
                        chapter_number=1,
                        source_start=0,
                        source_end=1,
                        content_hash="0" * 64,
                        cutoff_chapter=1,
                    )
                    for key in (item.get("evidence_keys") or [])
                ],
                visual_bible_refs=[
                    VisualBibleRef(
                        stable_id=ref["stable_id"],
                        claim_key=ref.get("claim_key"),
                        revision_hash="0" * 64,
                    )
                    for ref in (item.get("visual_bible_refs") or [])
                ],
                spoiler_cutoff=item["spoiler_cutoff"],
            )
            for item in (payload.get("details") or [])
        ]
        spec = SceneSpecContract(
            schema_version=payload["schema_version"],
            artifact_kind="scene_spec",
            owner_id=payload["owner_id"],
            novel_id=payload["novel_id"],
            spec_key=payload["spec_key"],
            revision_number=payload["revision_number"],
            scene_candidate_hash=payload["scene_candidate_hash"],
            visual_bible_revision_hash=payload["visual_bible_revision_hash"],
            source_snapshot_id=payload["source_snapshot_id"],
            source_snapshot_hash=payload["source_snapshot_hash"],
            cutoff_chapter=payload["cutoff_chapter"],
            schema_hash=payload["schema_hash"],
            compiler_id=payload["compiler_id"],
            compiler_version=payload["compiler_version"],
            policy_hash=payload["policy_hash"],
            config_hash=payload.get("config_hash"),
            content_hash="0" * 64,
            details=details,
            negative_constraints=[
                NegativeConstraint(
                    constraint_key=item["constraint_key"],
                    scope=item["scope"],
                    source=item["source"],
                    text=item["text"],
                    author=item.get("author"),
                    rationale=item.get("rationale"),
                    visual_bible_refs=[
                        VisualBibleRef(
                            stable_id=ref["stable_id"],
                            claim_key=ref.get("claim_key"),
                            revision_hash="0" * 64,
                        )
                        for ref in (item.get("visual_bible_refs") or [])
                    ],
                    spoiler_cutoff=item["spoiler_cutoff"],
                )
                for item in (payload.get("negative_constraints") or [])
            ],
            uncertainties=[
                SceneUncertainty(
                    uncertainty_key=item["uncertainty_key"],
                    reason=item["reason"],
                    detail=item["detail"],
                )
                for item in (payload.get("uncertainties") or [])
            ],
        )
        return build_prompt_sections(spec)

    # ------------------------------------------------------------------ stale

    async def _is_stale(
        self,
        *,
        owner_id: int,
        novel_id: int,
        spec: SceneSpecVersionRow,
    ) -> bool:
        current_hash, _ = await self._current_snapshot(
            owner_id=owner_id, novel_id=novel_id
        )
        latest_vb = await self._latest_approved_version(
            owner_id=owner_id, novel_id=novel_id
        )
        return (
            spec.visual_bible_revision_hash != latest_vb.manifest_hash
            or spec.source_snapshot_hash != current_hash
        )

    async def _current_snapshot(
        self, *, owner_id: int, novel_id: int
    ) -> tuple[str, int]:
        """Fresh source snapshot address of the owning novel's chapter set."""
        from app.services.key_scenes.boundaries import SceneBoundaryService

        service = SceneBoundaryService(self._session)
        snapshot_hash, _chapters = await service.load_source_snapshot(
            owner_id=owner_id, novel_id=novel_id
        )
        return snapshot_hash, novel_id

    async def _latest_approved_version(
        self, *, owner_id: int, novel_id: int
    ) -> VisualBibleVersionRow:
        row = await self._session.scalar(
            select(VisualBibleVersionRow)
            .where(
                VisualBibleVersionRow.owner_id == owner_id,
                VisualBibleVersionRow.novel_id == novel_id,
                VisualBibleVersionRow.review_state == VisualReviewState.APPROVED.value,
            )
            .order_by(VisualBibleVersionRow.id.desc())
            .limit(1)
        )
        if row is None:
            raise SceneSpecNotFound(
                "novel has no approved Visual Bible revision; spec is stale by default"
            )
        return row

    # --------------------------------------------------------------- queries

    async def _spec(
        self, *, owner_id: int, novel_id: int, spec_key: str
    ) -> SceneSpecVersionRow | None:
        return await self._session.scalar(
            select(SceneSpecVersionRow).where(
                SceneSpecVersionRow.owner_id == owner_id,
                SceneSpecVersionRow.novel_id == novel_id,
                SceneSpecVersionRow.spec_key == spec_key,
            )
        )

    async def _spec_by_id(
        self, *, owner_id: int, novel_id: int, spec_id: int
    ) -> SceneSpecVersionRow | None:
        return await self._session.scalar(
            select(SceneSpecVersionRow).where(
                SceneSpecVersionRow.owner_id == owner_id,
                SceneSpecVersionRow.novel_id == novel_id,
                SceneSpecVersionRow.id == spec_id,
            )
        )

    @staticmethod
    def _version_idempotency_key(spec: SceneSpecContract) -> str:
        return canonical_scene_spec_hash(
            {
                "kind": "scene_spec.version",
                "owner_id": spec.owner_id,
                "novel_id": spec.novel_id,
                "spec_key": spec.spec_key,
                "content_hash": spec.content_hash,
            }
        )
