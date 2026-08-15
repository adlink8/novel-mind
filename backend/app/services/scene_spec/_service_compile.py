"""Scene-spec compile seam mixin (server-side revalidation + preview).

Extracted from ``service.py`` (refactor split): ``compile_input`` owns the
owner/novel scoping, the frozen-set / approved-candidate revalidation and the
snapshot/cutoff/Visual-Bible lineage gates; ``preview`` compiles without
persisting and without a provider call (Phase 32-04). This mixin never imports
``service.py`` — shared DTOs come from ``service_models`` and pure helpers from
``service_primitives`` (both leaves) — so the service package dependency graph
stays acyclic. The composed class ``SceneSpecService`` keeps the same method
surface.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.key_scene import (
    SceneCandidate as SceneCandidateRow,
    SceneCandidateSet as SceneCandidateSetRow,
    SceneEvidenceRange as SceneEvidenceRangeRow,
    SceneReviewDecision as SceneReviewDecisionRow,
)
from app.models.novel import Novel
from app.models.visual_bible import VisualBibleVersion as VisualBibleVersionRow
from app.schemas.key_scene import KeySceneReviewState, candidate_content_hash
from app.schemas.visual_bible import VisualReviewState

from app.services.key_scenes.candidates import derive_candidate_review_states

from .compiler import SceneSpecCompileInput, compile_scene_spec
from .errors import SceneSpecNotFound, SceneSpecServiceError
from .service_models import SceneSpecPreviewRequest, SceneSpecPreviewResult
from .service_primitives import _reconstruct_candidate, view_from_contract
from .visual_bible_loader import load_visual_bible_contract


class CompileMixin:
    """Compile-input revalidation and the no-persistence preview seam."""

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

        # The candidate row's review_state is a static freeze-time snapshot; the
        # effective state is derived from the append-only decisions (D-31-04).
        # A rejected or never-approved candidate must not compile into a spec.
        decision_rows = (
            await self._session.scalars(
                select(SceneReviewDecisionRow).where(
                    SceneReviewDecisionRow.owner_id == owner_id,
                    SceneReviewDecisionRow.novel_id == novel_id,
                    SceneReviewDecisionRow.set_id == set_row.id,
                )
            )
        ).all()
        effective_state = derive_candidate_review_states(decision_rows).get(
            request.candidate_key,
            KeySceneReviewState(candidate_row.review_state),
        )
        if effective_state != KeySceneReviewState.APPROVED:
            raise SceneSpecNotFound(
                f"candidate {request.candidate_key!r} is not approved in the frozen set"
            )

        evidence_rows = (
            await self._session.scalars(
                select(SceneEvidenceRangeRow)
                .where(
                    SceneEvidenceRangeRow.owner_id == owner_id,
                    SceneEvidenceRangeRow.novel_id == novel_id,
                    SceneEvidenceRangeRow.set_id == set_row.id,
                )
                .order_by(SceneEvidenceRangeRow.id.asc())
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
            view=view_from_contract(spec),
            unresolved=compiled.unresolved,
            provider_calls=0,
        )
