"""Owner-scoped PromptRevision compile/preview/persist/edit/diff service seam
(Phase 32-03/32-04, REQ-VIS-03).

DB-backed half of the ``adapters.py`` stack: server-side gates enforce that
only owner/novel-scoped SceneSpecs compile, prompt preview never persists and
never calls a provider (D-32-04), and a human edit produces an explicit new
candidate revision with the diff retained.

This module owns ``PromptRevisionService`` plus its request/result value
objects (``PromptCompileRequest`` / ``PromptEditInput`` /
``PersistedPromptRevision`` / ``EditedPromptRevision``). The pure compile core
(``compile_prompt`` / ``PromptArtifact`` / adapter registry) lives in
``_adapter_core``; the shared fail-closed error vocabulary in
``_adapter_errors``. This module never imports the ``adapters`` facade, so the
facade → core/service dependency is one-directional. Split note: extracted
from ``adapters.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prompt_revision import PromptRevision as PromptRevisionRow
from app.models.scene_spec import (
    SceneSpecDetail as SceneSpecDetailRow,
    SceneSpecEvidenceRef as SceneSpecEvidenceRefRow,
    SceneSpecNegativeConstraint as SceneSpecNegativeConstraintRow,
    SceneSpecVersion as SceneSpecVersionRow,
)
from app.models.visual_bible import VisualBibleVersion as VisualBibleVersionRow
from app.schemas.scene_spec import (
    NegativeConstraint,
    PromptRevisionContract,
    PromptRevisionView,
    SceneDetail,
    SceneSpecContract,
    SceneSpecGateError,
    SceneUncertainty,
    SpecDetailKind,
    SpecEvidenceRef,
    VisualBibleRef,
    canonical_scene_spec_hash,
    prompt_output_payload,
    scene_spec_content_payload,
    validate_scene_spec_contract,
)
from app.schemas.visual_bible import VisualReviewState
from app.services.prompt_compiler._adapter_core import (
    MOCK_PROMPT_ADAPTER_ID,
    PromptArtifact,
    compile_prompt,
    get_adapter,
)
from app.services.prompt_compiler._adapter_errors import (
    PromptRevisionConflict,
    PromptRevisionNotFound,
    PromptRevisionServiceError,
)
from app.services.prompt_compiler.serialization import (
    PromptDiff,
    diff_prompt_revisions,
    edited_spec_with_interpretation,
)


# ---------------------------------------------------------------------------
# Owner-scoped service seam (compile / preview / persist / edit / diff)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptCompileRequest:
    """Server-side compile request; scope comes from the caller path."""

    spec_id: int
    prompt_key: str
    adapter_id: str = MOCK_PROMPT_ADAPTER_ID
    revision_number: int = 1
    parent_prompt_revision_id: int | None = None


@dataclass(frozen=True)
class PromptEditInput:
    """A human edit: only ``user_interpretation`` details may change through
    the prompt seam (D-32-04); an absent detail_key adds a new labeled
    interpretation detail. Evidence/Visual Bible canon is never editable here.
    """

    detail_key: str
    kind: SpecDetailKind
    text: str
    author: str
    rationale: str


@dataclass(frozen=True)
class PersistedPromptRevision:
    revision: PromptRevisionRow
    view: PromptRevisionView
    replayed: bool = False


@dataclass(frozen=True)
class EditedPromptRevision:
    revision: PromptRevisionRow
    view: PromptRevisionView
    diff: PromptDiff


class PromptRevisionService:
    """Owner-scoped PromptRevision compile/preview/persist/edit/diff seam."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------ compile seam

    async def compile_revision(
        self,
        *,
        owner_id: int,
        novel_id: int,
        request: PromptCompileRequest,
    ) -> PromptArtifact:
        """Server-side revalidation before the pure compile runs.

        The SceneSpec row must be inside the explicit owner/novel scope and
        pass its own contract gate (evidence/snapshot/cutoff/Visual Bible
        revision lineage). Preview and create both go through this seam; no
        provider is ever called (D-32-04).
        """
        spec_row = await self._spec_by_id(
            owner_id=owner_id, novel_id=novel_id, spec_id=request.spec_id
        )
        if spec_row is None:
            raise PromptRevisionNotFound(
                "scene spec not found in the explicit owner/novel scope"
            )
        spec = await self._load_spec_contract(spec_row)
        adapter = get_adapter(request.adapter_id)
        revision = compile_prompt(
            spec,
            adapter=adapter,
            prompt_key=request.prompt_key,
            revision_number=request.revision_number,
            parent_prompt_revision_id=request.parent_prompt_revision_id,
        )
        return PromptArtifact.build(revision, spec, provider_calls=0)

    # ---------------------------------------------------------------- preview

    async def preview(
        self,
        *,
        owner_id: int,
        novel_id: int,
        request: PromptCompileRequest,
    ) -> PromptArtifact:
        """Compile a prompt preview without persisting anything and without a
        provider call (D-32-04)."""
        return await self.compile_revision(
            owner_id=owner_id, novel_id=novel_id, request=request
        )

    # ----------------------------------------------------------------- create

    async def create(
        self,
        *,
        owner_id: int,
        novel_id: int,
        request: PromptCompileRequest,
    ) -> PersistedPromptRevision:
        """Compile and persist one immutable prompt candidate (append-only,
        idempotent replay; a conflicting prompt_key retry fails closed)."""
        artifact = await self.compile_revision(
            owner_id=owner_id, novel_id=novel_id, request=request
        )
        revision = artifact.revision
        spec_row = await self._spec_by_id(
            owner_id=owner_id, novel_id=novel_id, spec_id=request.spec_id
        )

        existing = await self._revision_by_key(
            owner_id=owner_id, novel_id=novel_id, prompt_key=revision.prompt_key
        )
        if existing is not None:
            if existing.prompt_hash == revision.prompt_hash:
                return PersistedPromptRevision(
                    revision=existing,
                    view=self._view_from_row(existing),
                    replayed=True,
                )
            raise PromptRevisionConflict(
                f"conflicting prompt retry: prompt_key {revision.prompt_key!r} "
                "already exists with different immutable content"
            )

        row = self._revision_row(
            owner_id=owner_id, novel_id=novel_id, spec_row=spec_row, revision=revision
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            existing = await self._revision_by_key(
                owner_id=owner_id,
                novel_id=novel_id,
                prompt_key=revision.prompt_key,
            )
            if existing is None:
                raise PromptRevisionConflict(
                    "prompt revision race: existing row not found after rollback"
                ) from None
            if existing.prompt_hash != revision.prompt_hash:
                raise PromptRevisionConflict(
                    f"conflicting prompt retry: prompt_key {revision.prompt_key!r} "
                    "already exists with different immutable content"
                )
            return PersistedPromptRevision(
                revision=existing,
                view=self._view_from_row(existing),
                replayed=True,
            )
        return PersistedPromptRevision(
            revision=row, view=self._view_from_row(row), replayed=False
        )

    # ------------------------------------------------------------------- edit

    async def edit(
        self,
        *,
        owner_id: int,
        novel_id: int,
        revision_id: int,
        prompt_key: str,
        edit: PromptEditInput,
    ) -> EditedPromptRevision:
        """Apply a human edit and produce an explicit new candidate revision.

        Only ``user_interpretation`` details can change through the prompt seam
        (D-32-04); the edited spec is recompiled into a new PromptRevision with
        a new prompt_key, ``revision_number = parent + 1`` and a
        ``parent_prompt_revision_id`` link, so the diff is fully auditable.
        Unsupported edits fail closed and no image provider is called.
        """
        base_row = await self._revision_by_id(
            owner_id=owner_id, novel_id=novel_id, revision_id=revision_id
        )
        if base_row is None:
            raise PromptRevisionNotFound(
                "prompt revision not found in the explicit owner/novel scope"
            )
        if base_row.scene_spec_id is None:
            raise PromptRevisionServiceError(
                "base prompt revision has no persisted SceneSpec to edit"
            )
        spec_row = await self._spec_by_id(
            owner_id=owner_id, novel_id=novel_id, spec_id=base_row.scene_spec_id
        )
        if spec_row is None:
            raise PromptRevisionNotFound(
                "base scene spec not found in the explicit owner/novel scope"
            )
        spec = await self._load_spec_contract(spec_row)
        edited_spec = edited_spec_with_interpretation(spec, edit=edit)

        adapter = get_adapter(base_row.adapter_id)
        new_revision = compile_prompt(
            edited_spec,
            adapter=adapter,
            prompt_key=prompt_key,
            revision_number=base_row.revision_number + 1,
            parent_prompt_revision_id=base_row.id,
        )
        base_contract = self._revision_contract_from_row(base_row)
        diff = diff_prompt_revisions(base_contract, new_revision)

        existing = await self._revision_by_key(
            owner_id=owner_id, novel_id=novel_id, prompt_key=prompt_key
        )
        if existing is not None:
            raise PromptRevisionConflict(
                f"conflicting edit retry: prompt_key {prompt_key!r} already exists"
            )

        # The edited SceneSpec is a derived candidate (not a persisted version
        # row); its canonical content payload is stored with the prompt so the
        # edited lineage replays from the prompt row alone.
        row = self._revision_row(
            owner_id=owner_id,
            novel_id=novel_id,
            spec_row=None,
            revision=new_revision,
            spec_payload=scene_spec_content_payload(edited_spec),
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError:
            await self._session.rollback()
            existing = await self._revision_by_key(
                owner_id=owner_id, novel_id=novel_id, prompt_key=prompt_key
            )
            if existing is not None:
                raise PromptRevisionConflict(
                    f"conflicting edit retry: prompt_key {prompt_key!r} already exists"
                ) from None
            raise
        return EditedPromptRevision(
            revision=row, view=self._view_from_row(row), diff=diff
        )

    # ------------------------------------------------------------ read seams

    async def list(self, *, owner_id: int, novel_id: int) -> list[PromptRevisionView]:
        rows = (
            await self._session.scalars(
                select(PromptRevisionRow)
                .where(
                    PromptRevisionRow.owner_id == owner_id,
                    PromptRevisionRow.novel_id == novel_id,
                )
                .order_by(PromptRevisionRow.id.asc())
            )
        ).all()
        return [self._view_from_row(row) for row in rows]

    async def load(
        self, *, owner_id: int, novel_id: int, revision_id: int
    ) -> tuple[PromptRevisionView, bool]:
        """Return (view, stale). ``stale`` means the SceneSpec or the Visual
        Bible revision / source snapshot the prompt was compiled against no
        longer matches the novel's current approved state (D-32-03)."""
        row = await self._revision_by_id(
            owner_id=owner_id, novel_id=novel_id, revision_id=revision_id
        )
        if row is None:
            raise PromptRevisionNotFound(
                "prompt revision not found in the explicit owner/novel scope"
            )
        stale = await self._is_stale(owner_id=owner_id, novel_id=novel_id, revision=row)
        return self._view_from_row(row), stale

    async def diff(
        self, *, owner_id: int, novel_id: int, revision_id: int
    ) -> PromptDiff:
        """Diff the revision against its parent (auditable edit lineage)."""
        row = await self._revision_by_id(
            owner_id=owner_id, novel_id=novel_id, revision_id=revision_id
        )
        if row is None:
            raise PromptRevisionNotFound(
                "prompt revision not found in the explicit owner/novel scope"
            )
        if row.parent_prompt_revision_id is None:
            raise PromptRevisionServiceError(
                "prompt revision has no parent to diff against"
            )
        parent = await self._revision_by_id(
            owner_id=owner_id,
            novel_id=novel_id,
            revision_id=row.parent_prompt_revision_id,
        )
        if parent is None:
            raise PromptRevisionNotFound(
                "parent prompt revision not found in the explicit owner/novel scope"
            )
        return diff_prompt_revisions(
            self._revision_contract_from_row(parent),
            self._revision_contract_from_row(row),
        )

    # -------------------------------------------------------------- persistence

    def _revision_row(
        self,
        *,
        owner_id: int,
        novel_id: int,
        spec_row: SceneSpecVersionRow | None,
        revision: PromptRevisionContract,
        spec_payload: dict[str, Any] | None = None,
    ) -> PromptRevisionRow:
        canonical_payload = prompt_output_payload(revision)
        if spec_payload is not None:
            canonical_payload = dict(canonical_payload)
            canonical_payload["edited_spec_payload"] = spec_payload
        return PromptRevisionRow(
            owner_id=owner_id,
            novel_id=novel_id,
            prompt_key=revision.prompt_key,
            revision_number=revision.revision_number,
            parent_prompt_revision_id=revision.parent_prompt_revision_id,
            scene_spec_id=spec_row.id if spec_row is not None else None,
            scene_spec_hash=revision.scene_spec_hash,
            visual_bible_revision_id=(
                spec_row.visual_bible_revision_id if spec_row is not None else None
            ),
            visual_bible_revision_hash=revision.visual_bible_revision_hash,
            source_snapshot_id=revision.source_snapshot_id,
            source_snapshot_hash=revision.source_snapshot_hash,
            cutoff_chapter=revision.cutoff_chapter,
            review_state=revision.review_state.value,
            schema_version=revision.schema_version,
            schema_hash=revision.schema_hash,
            prompt_schema_hash=revision.prompt_schema_hash,
            compiler_version=revision.compiler_version,
            adapter_id=revision.adapter_id,
            adapter_version=revision.adapter_version,
            config_hash=revision.config_hash,
            input_hash=revision.input_hash,
            prompt_hash=revision.prompt_hash,
            sections=dict(revision.sections),
            negative_constraints=list(revision.negative_constraints),
            uncertainties=list(revision.uncertainties),
            prompt_text=revision.prompt_text,
            redacted_preview=revision.redacted_preview,
            canonical_payload=canonical_payload,
            canonical_payload_hash=revision.prompt_hash,
            idempotency_key=canonical_scene_spec_hash(
                {
                    "kind": "prompt_revision.version",
                    "owner_id": owner_id,
                    "novel_id": novel_id,
                    "prompt_key": revision.prompt_key,
                    "prompt_hash": revision.prompt_hash,
                }
            ),
            projection_hash=revision.prompt_hash,
        )

    # ----------------------------------------------------------------- spec load

    async def _load_spec_contract(
        self, spec_row: SceneSpecVersionRow
    ) -> SceneSpecContract:
        """Reconstruct the immutable SceneSpecContract from persisted rows and
        revalidate it (evidence/snapshot/cutoff/Visual Bible lineage)."""
        detail_rows = (
            await self._session.scalars(
                select(SceneSpecDetailRow).where(
                    SceneSpecDetailRow.owner_id == spec_row.owner_id,
                    SceneSpecDetailRow.novel_id == spec_row.novel_id,
                    SceneSpecDetailRow.spec_id == spec_row.id,
                )
            )
        ).all()
        constraint_rows = (
            await self._session.scalars(
                select(SceneSpecNegativeConstraintRow).where(
                    SceneSpecNegativeConstraintRow.owner_id == spec_row.owner_id,
                    SceneSpecNegativeConstraintRow.novel_id == spec_row.novel_id,
                    SceneSpecNegativeConstraintRow.spec_id == spec_row.id,
                )
            )
        ).all()
        evidence_rows = (
            await self._session.scalars(
                select(SceneSpecEvidenceRefRow).where(
                    SceneSpecEvidenceRefRow.owner_id == spec_row.owner_id,
                    SceneSpecEvidenceRefRow.novel_id == spec_row.novel_id,
                    SceneSpecEvidenceRefRow.spec_id == spec_row.id,
                )
            )
        ).all()

        evidence_by_detail: dict[int, list[SpecEvidenceRef]] = {}
        evidence_by_constraint: dict[int, list[SpecEvidenceRef]] = {}
        for evidence in evidence_rows:
            ref = SpecEvidenceRef(
                evidence_key=evidence.evidence_key,
                source_snapshot_id=evidence.source_snapshot_id,
                source_snapshot_hash=evidence.source_snapshot_hash,
                chapter_id=evidence.chapter_id,
                chapter_number=evidence.chapter_number,
                source_start=evidence.source_start,
                source_end=evidence.source_end,
                content_hash=evidence.content_hash,
                excerpt=evidence.excerpt,
                cutoff_chapter=evidence.cutoff_chapter,
            )
            if evidence.detail_id is not None:
                evidence_by_detail.setdefault(evidence.detail_id, []).append(ref)
            if evidence.constraint_id is not None:
                evidence_by_constraint.setdefault(evidence.constraint_id, []).append(
                    ref
                )

        payload = spec_row.canonical_payload

        details: list[SceneDetail] = []
        for item in payload.get("details") or []:
            detail_row = next(
                (row for row in detail_rows if row.detail_key == item["detail_key"]),
                None,
            )
            refs = (
                evidence_by_detail.get(detail_row.id, [])
                if detail_row is not None
                else []
            )
            details.append(
                SceneDetail(
                    detail_key=item["detail_key"],
                    kind=item["kind"],
                    source=item["source"],
                    text=item["text"],
                    author=item.get("author"),
                    rationale=item.get("rationale"),
                    evidence_refs=refs,
                    visual_bible_refs=self._vb_refs(
                        item, spec_row.visual_bible_revision_hash
                    ),
                    spoiler_cutoff=item["spoiler_cutoff"],
                )
            )

        constraints: list[NegativeConstraint] = []
        for item in payload.get("negative_constraints") or []:
            constraint_row = next(
                (
                    row
                    for row in constraint_rows
                    if row.constraint_key == item["constraint_key"]
                ),
                None,
            )
            refs = (
                evidence_by_constraint.get(constraint_row.id, [])
                if constraint_row is not None
                else []
            )
            constraints.append(
                NegativeConstraint(
                    constraint_key=item["constraint_key"],
                    scope=item["scope"],
                    source=item["source"],
                    text=item["text"],
                    author=item.get("author"),
                    rationale=item.get("rationale"),
                    evidence_refs=refs,
                    visual_bible_refs=self._vb_refs(
                        item, spec_row.visual_bible_revision_hash
                    ),
                    spoiler_cutoff=item["spoiler_cutoff"],
                )
            )

        uncertainties = [
            SceneUncertainty(
                uncertainty_key=item["uncertainty_key"],
                reason=item["reason"],
                detail=item["detail"],
            )
            for item in (payload.get("uncertainties") or [])
        ]

        spec = SceneSpecContract(
            schema_version=payload["schema_version"],
            artifact_kind="scene_spec",
            owner_id=spec_row.owner_id,
            novel_id=spec_row.novel_id,
            spec_key=spec_row.spec_key,
            revision_number=spec_row.revision_number,
            scene_candidate_hash=spec_row.scene_candidate_hash,
            scene_candidate_id=spec_row.scene_candidate_id,
            visual_bible_revision_hash=spec_row.visual_bible_revision_hash,
            visual_bible_revision_id=spec_row.visual_bible_revision_id,
            source_snapshot_id=spec_row.source_snapshot_id,
            source_snapshot_hash=spec_row.source_snapshot_hash,
            cutoff_chapter=spec_row.cutoff_chapter,
            schema_hash=spec_row.schema_hash,
            compiler_id=spec_row.compiler_id,
            compiler_version=spec_row.compiler_version,
            policy_hash=spec_row.policy_hash,
            config_hash=spec_row.config_hash,
            content_hash=spec_row.content_hash,
            details=details,
            negative_constraints=constraints,
            uncertainties=uncertainties,
            review_state=spec_row.review_state,
        )
        try:
            validate_scene_spec_contract(spec)
        except SceneSpecGateError as exc:
            raise PromptRevisionServiceError(
                f"persisted scene spec failed its own contract gate: {exc}"
            ) from exc
        return spec

    @staticmethod
    def _vb_refs(item: Mapping[str, Any], revision_hash: str) -> list[VisualBibleRef]:
        return [
            VisualBibleRef(
                stable_id=ref["stable_id"],
                claim_key=ref.get("claim_key"),
                revision_id=None,
                revision_hash=revision_hash,
            )
            for ref in (item.get("visual_bible_refs") or [])
        ]

    @staticmethod
    def _revision_contract_from_row(
        row: PromptRevisionRow,
    ) -> PromptRevisionContract:
        return PromptRevisionContract(
            schema_version=row.schema_version,
            artifact_kind="prompt_revision",
            owner_id=row.owner_id,
            novel_id=row.novel_id,
            prompt_key=row.prompt_key,
            revision_number=row.revision_number,
            parent_prompt_revision_id=row.parent_prompt_revision_id,
            scene_spec_hash=row.scene_spec_hash,
            visual_bible_revision_hash=row.visual_bible_revision_hash,
            source_snapshot_id=row.source_snapshot_id,
            source_snapshot_hash=row.source_snapshot_hash,
            cutoff_chapter=row.cutoff_chapter,
            schema_hash=row.schema_hash,
            prompt_schema_hash=row.prompt_schema_hash,
            compiler_version=row.compiler_version,
            adapter_id=row.adapter_id,
            adapter_version=row.adapter_version,
            config_hash=row.config_hash,
            input_hash=row.input_hash,
            prompt_hash=row.prompt_hash,
            sections=dict(row.sections or {}),
            negative_constraints=list(row.negative_constraints or []),
            uncertainties=list(row.uncertainties or []),
            prompt_text=row.prompt_text,
            redacted_preview=row.redacted_preview,
            review_state=row.review_state,
        )

    @staticmethod
    def _view_from_row(row: PromptRevisionRow) -> PromptRevisionView:
        return PromptRevisionView(
            id=row.id,
            owner_id=row.owner_id,
            novel_id=row.novel_id,
            prompt_key=row.prompt_key,
            revision_number=row.revision_number,
            parent_prompt_revision_id=row.parent_prompt_revision_id,
            scene_spec_hash=row.scene_spec_hash,
            visual_bible_revision_hash=row.visual_bible_revision_hash,
            source_snapshot_id=row.source_snapshot_id,
            source_snapshot_hash=row.source_snapshot_hash,
            cutoff_chapter=row.cutoff_chapter,
            schema_version=row.schema_version,
            schema_hash=row.schema_hash,
            prompt_schema_hash=row.prompt_schema_hash,
            compiler_version=row.compiler_version,
            adapter_id=row.adapter_id,
            adapter_version=row.adapter_version,
            config_hash=row.config_hash,
            input_hash=row.input_hash,
            prompt_hash=row.prompt_hash,
            sections=dict(row.sections or {}),
            negative_constraints=list(row.negative_constraints or []),
            uncertainties=list(row.uncertainties or []),
            redacted_preview=row.redacted_preview,
            review_state=row.review_state,
        )

    # ------------------------------------------------------------------ stale

    async def _is_stale(
        self,
        *,
        owner_id: int,
        novel_id: int,
        revision: PromptRevisionRow,
    ) -> bool:
        if revision.scene_spec_id is None:
            return True
        spec_row = await self._spec_by_id(
            owner_id=owner_id, novel_id=novel_id, spec_id=revision.scene_spec_id
        )
        if spec_row is None:
            return True
        current_hash, _ = await self._current_snapshot(
            owner_id=owner_id, novel_id=novel_id
        )
        latest_vb = await self._latest_approved_version(
            owner_id=owner_id, novel_id=novel_id
        )
        return (
            spec_row.visual_bible_revision_hash != latest_vb.manifest_hash
            or spec_row.source_snapshot_hash != current_hash
        )

    async def _current_snapshot(
        self, *, owner_id: int, novel_id: int
    ) -> tuple[str, int]:
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
            raise PromptRevisionNotFound(
                "novel has no approved Visual Bible revision; prompt is stale by default"
            )
        return row

    # --------------------------------------------------------------- queries

    async def _revision_by_id(
        self, *, owner_id: int, novel_id: int, revision_id: int
    ) -> PromptRevisionRow | None:
        return await self._session.scalar(
            select(PromptRevisionRow).where(
                PromptRevisionRow.owner_id == owner_id,
                PromptRevisionRow.novel_id == novel_id,
                PromptRevisionRow.id == revision_id,
            )
        )

    async def _revision_by_key(
        self, *, owner_id: int, novel_id: int, prompt_key: str
    ) -> PromptRevisionRow | None:
        return await self._session.scalar(
            select(PromptRevisionRow).where(
                PromptRevisionRow.owner_id == owner_id,
                PromptRevisionRow.novel_id == novel_id,
                PromptRevisionRow.prompt_key == prompt_key,
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
