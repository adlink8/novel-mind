"""Scene-spec read seams mixin (list / load / diff + staleness).

Extracted from ``service.py`` (refactor split): ``list`` / ``load`` project
persisted version rows, ``diff`` recompiles the frozen candidate against the
current approved revision and diffs canonical sections, and the stale/snapshot/
revision helpers back the D-32-03 stale marker. This mixin never imports
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
)
from app.models.scene_spec import SceneSpecVersion as SceneSpecVersionRow
from app.models.visual_bible import VisualBibleVersion as VisualBibleVersionRow
from app.schemas.key_scene import candidate_content_hash
from app.schemas.scene_spec import SceneSpecView, build_prompt_sections
from app.schemas.visual_bible import VisualReviewState

from .compiler import SceneSpecCompileInput, SceneSpecCompileError, compile_scene_spec
from .errors import SceneSpecNotFound
from .service_models import SceneSpecDiffResult, SceneSpecDiffSection
from .service_primitives import (
    _reconstruct_candidate,
    sections_from_payload,
    view_from_rows,
)
from .visual_bible_loader import load_visual_bible_contract


class ReadQueryMixin:
    """Owner-scoped read / recompile-diff seams."""

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
            await view_from_rows(spec=row)
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
        view = await view_from_rows(spec=spec)
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
        if latest_vb is None:
            # No approved Visual Bible revision to recompile against: the stored
            # spec still exists, so this is stale-by-default, never a 404.
            return SceneSpecDiffResult(
                original_spec_hash=spec.content_hash,
                current_spec_hash=spec.content_hash,
                stale=True,
                same=False,
                changed_sections=(),
            )
        if spec.source_snapshot_hash != current_hash:
            # The source snapshot drifted: the frozen snapshot is gone, so
            # recompiling against the stale frozen snapshot would silently
            # reproduce the old content and hide the drift. Short-circuit as
            # stale without recompiling (WR-03).
            return SceneSpecDiffResult(
                original_spec_hash=spec.content_hash,
                current_spec_hash=spec.content_hash,
                stale=True,
                same=False,
                changed_sections=(),
            )
        stale = spec.visual_bible_revision_hash != latest_vb.manifest_hash
        try:
            original_sections = sections_from_payload(spec.canonical_payload)
        except (ValueError, KeyError):
            # Defensive: a malformed payload must not turn diff into a 500.
            original_sections = {}

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
        if latest_vb is None:
            # No current approved revision to compare against: stale by default.
            return True
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
    ) -> VisualBibleVersionRow | None:
        """The newest approved Visual Bible revision, or ``None`` when the novel
        has none. Callers treat ``None`` as stale-by-default for an existing spec
        (no current revision to compare/recompile against), never as a 404."""
        return await self._session.scalar(
            select(VisualBibleVersionRow)
            .where(
                VisualBibleVersionRow.owner_id == owner_id,
                VisualBibleVersionRow.novel_id == novel_id,
                VisualBibleVersionRow.review_state == VisualReviewState.APPROVED.value,
            )
            .order_by(VisualBibleVersionRow.id.desc())
            .limit(1)
        )

    # --------------------------------------------------------------- queries

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
