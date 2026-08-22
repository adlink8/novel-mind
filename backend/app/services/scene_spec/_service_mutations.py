"""Scene-spec persistence mixin (append-only create with idempotent replay).

Extracted from ``service.py`` (refactor split): ``create`` compiles and
persists one immutable candidate spec; ``_persist_content`` / ``_evidence_row``
write the detail/constraint/uncertainty/evidence rows; ``_spec`` is the
spec-key lookup that gates replay/conflict detection. This mixin never imports
``service.py`` — shared DTOs come from ``service_models`` and pure helpers from
``service_primitives`` (both leaves) — so the service package dependency graph
stays acyclic. The composed class ``SceneSpecService`` keeps the same method
surface.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.scene_spec import (
    SceneSpecDetail as SceneSpecDetailRow,
    SceneSpecEvidenceRef as SceneSpecEvidenceRefRow,
    SceneSpecNegativeConstraint as SceneSpecNegativeConstraintRow,
    SceneSpecUncertainty as SceneSpecUncertaintyRow,
    SceneSpecVersion as SceneSpecVersionRow,
)
from app.schemas.scene_spec import (
    SceneSpecContract,
    SpecEvidenceRef,
    SpecReviewState,
    canonical_scene_spec_hash,
    scene_spec_content_payload,
)

from .compiler import compile_scene_spec
from .errors import SceneSpecConflict
from .service_models import PersistedSceneSpec, SceneSpecPreviewRequest
from .service_primitives import same_provenance, version_idempotency_key, view_from_rows


class MutationMixin:
    """Append-only create seam (immutable candidate spec, replay-safe)."""

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
                if not same_provenance(existing, spec):
                    raise SceneSpecConflict(
                        f"conflicting spec replay: spec_key {spec.spec_key!r} already "
                        "exists with identical content but different provenance "
                        "(candidate/Visual Bible revision/source snapshot)"
                    )
                return PersistedSceneSpec(
                    version=existing,
                    view=await view_from_rows(spec=existing),
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
            idempotency_key=version_idempotency_key(spec),
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
            if not same_provenance(existing, spec):
                raise SceneSpecConflict(
                    f"conflicting spec replay: spec_key {spec.spec_key!r} already "
                    "exists with identical content but different provenance "
                    "(candidate/Visual Bible revision/source snapshot)"
                )
            return PersistedSceneSpec(
                version=existing,
                view=await view_from_rows(spec=existing),
                replayed=True,
            )

        await self._persist_content(
            owner_id=owner_id, novel_id=novel_id, spec=spec, version_row=version_row
        )
        await self._session.flush()
        return PersistedSceneSpec(
            version=version_row,
            view=await view_from_rows(spec=version_row),
            replayed=False,
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

    # ----------------------------------------------------------------- queries

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
