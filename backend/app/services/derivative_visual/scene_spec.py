"""Canonical derivative Scene Spec compiler (Phase 38-02, REQ-FORK-04/REQ-CRE-06).

D-38-01/D-38-02/D-38-03: the provider of a derivative illustration never
receives an Original Visual Bible row, a raw file path or an un-declared
divergence. It receives **one frozen canonical ``DerivativeSceneSpecContract``**
compiled deterministically from the sealed story context + the approved visual
fork:

    approved derivative visual fork + sealed SceneSpec -> deterministic gates
        -> canonical derivative Scene Spec (the only provider input)

This module owns:

- ``compile_derivative_scene_spec`` — the pure, DB-free, replayable compiler.
  It evaluates every gate (``gates.py``), then assembles the frozen
  identity/style/reference/divergence/cutoff/evidence/hash envelope and seals
  it with a canonical SHA-256 ``content_hash``. Same snapshot -> same spec
  hash; a failing gate blocks the compile before any spec is produced.
- ``DerivativeSceneSpecService`` — the owner-scoped DB seam that revalidates
  the approved fork, the approved Original Visual Bible revision and the
  approved sealed SceneSpec inside the explicit owner/novel scope, then builds
  the compile input. A missing or unapproved upstream contract blocks the
  compile (no spec, no provider call).

Fail-closed rules (38-VALIDATION failure policy):
- only an approved ``fanfiction_visual`` fork and an approved Original Visual
  Bible revision may anchor a spec;
- the sealed SceneSpec must be approved and replay its own content hash, its
  source snapshot hash and its Visual Bible revision hash against the fork;
- the fork's source snapshot/manifest hashes must match the Original snapshot;
- identity rows pin the exact Original entities, reference assets pin the
  exact Original assets, and nothing is silently approved (D-38-03);
- unsupported detail, hidden divergence, mixed authority, stale source hashes
  and future cutoffs are blocked by the gates — never silently repaired.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.derivative_visual import (
    DerivativeVisualAsset,
    DerivativeVisualEntity,
    DerivativeVisualVersion,
)
from app.models.illustration import AssetRevision
from app.models.illustration_anchor import IllustrationAnchor
from app.models.scene_spec import (
    SceneSpecEvidenceRef as SceneSpecEvidenceRefRow,
    SceneSpecVersion as SceneSpecVersionRow,
)
from app.models.visual_bible import VisualBibleVersion
from app.schemas.derivative_visual import (
    DERIVATIVE_SCENE_SPEC_ARTIFACT_KIND,
    DERIVATIVE_SCENE_SPEC_COMPILER_ID,
    DERIVATIVE_SCENE_SPEC_COMPILER_VERSION,
    DERIVATIVE_SCENE_SPEC_SCHEMA_VERSION,
    DerivativeAnchorRef,
    DerivativeAssetLineageRow,
    DerivativeIdentityRow,
    DerivativeNegativeConstraint,
    DerivativeReferenceAssetRow,
    DerivativeSceneSpecCompileRequest,
    DerivativeSceneSpecContract,
    DerivativeSceneSpecEvidenceRef,
    DerivativeSceneSpecUncertainty,
    DerivativeVisualEntityType,
    DerivativeVisualRightsStatus,
    canonical_derivative_visual_hash,
    recompute_derivative_scene_spec_hash,
)
from app.schemas.scene_spec import (
    ConstraintScope,
    NegativeConstraint,
    SceneDetail,
    SceneSpecContract,
    SceneUncertainty,
    SpecDetailKind,
    SpecEvidenceRef,
    SpecReviewState,
    SpecSource,
    UncertaintyReason,
    VisualBibleRef,
    recompute_scene_spec_hash,
)
from app.services.derivative_visual.gates import (
    DerivativeSceneSpecBlockedError,
    DerivativeSceneSpecCompileInput,
    DerivativeSceneSpecGateError,
    DerivativeSceneSpecScopeError,
    GateCheck,
    assert_spec_gates_pass,
    run_compile_gates,
)


@dataclass(frozen=True)
class CompiledDerivativeSceneSpec:
    """Compiler result: the canonical spec plus the deterministic gate report."""

    spec: DerivativeSceneSpecContract
    gate_checks: tuple[GateCheck, ...]


def _require_scope(*, owner_id: int, novel_id: int) -> None:
    values = (owner_id, novel_id)
    if any(type(value) is not int or value <= 0 for value in values):
        raise DerivativeSceneSpecScopeError(
            "scope identifiers must be explicit positive integers"
        )


# ---------------------------------------------------------------------------
# Pure deterministic compiler (DB-free, unit-testable)
# ---------------------------------------------------------------------------


def compile_derivative_scene_spec(
    input_: DerivativeSceneSpecCompileInput,
) -> DerivativeSceneSpecContract:
    """Compile one frozen canonical derivative Scene Spec (D-38-03).

    Every gate must pass before anything is assembled; a failing gate raises
    ``DerivativeSceneSpecGateError`` with the auditable check report and no
    spec is ever produced. The returned contract is the only provider input.
    """
    checks = run_compile_gates(input_)
    assert_spec_gates_pass(checks)

    negative_constraints: list[DerivativeNegativeConstraint] = []
    if input_.scene_spec is not None:
        for c in input_.scene_spec.negative_constraints:
            negative_constraints.append(
                DerivativeNegativeConstraint(
                    constraint_key=f"scene-spec:{c.constraint_key}",
                    scope=c.scope.value,
                    source="scene_spec",
                    text=c.text,
                    rationale=c.rationale,
                )
            )
    negative_constraints.extend(input_.derivative_negative_constraints)

    uncertainties = [
        DerivativeSceneSpecUncertainty(
            uncertainty_key=u.uncertainty_key,
            reason=u.reason,
            detail=u.detail,
        )
        for u in input_.uncertainties
    ]

    scene_candidate_hash = (
        input_.scene_spec.scene_candidate_hash if input_.scene_spec is not None else ""
    )
    # Assemble without running the replay validator (the placeholder hash is
    # replaced below), then seal and re-validate the frozen contract.
    draft = DerivativeSceneSpecContract.model_construct(
        schema_version=DERIVATIVE_SCENE_SPEC_SCHEMA_VERSION,
        artifact_kind=DERIVATIVE_SCENE_SPEC_ARTIFACT_KIND,
        owner_id=input_.owner_id,
        novel_id=input_.novel_id,
        project_id=input_.project_id,
        fork_id=input_.fork_id,
        visual_namespace=input_.visual_namespace,
        spec_key=input_.spec_key,
        revision_number=input_.revision_number,
        visual_fork_version_id=input_.visual_fork_version_id,
        visual_fork_version_hash=input_.visual_fork_version_hash,
        scene_spec_id=input_.scene_spec_id,
        scene_spec_hash=input_.scene_spec_hash,
        scene_candidate_hash=scene_candidate_hash,
        visual_bible_revision_id=input_.visual_bible_revision_id,
        visual_bible_revision_hash=input_.visual_bible_revision_hash,
        source_snapshot_id=input_.source_snapshot_id,
        source_snapshot_hash=input_.source_snapshot_hash,
        source_manifest_hash=input_.source_manifest_hash,
        cutoff_chapter=input_.cutoff_chapter,
        divergence=input_.divergence,
        provenance=input_.provenance,
        identity=list(input_.identity),
        style_profile=input_.style_profile,
        negative_constraints=negative_constraints,
        reference_assets=list(input_.reference_assets),
        asset_lineage=list(input_.asset_lineage),
        anchors=list(input_.anchors),
        evidence_refs=list(input_.evidence_refs),
        uncertainties=uncertainties,
        export_manifest_hash=input_.export_manifest_hash,
        content_hash="0" * 64,
        review_state="candidate",
    )
    spec = draft.model_copy(
        update={"content_hash": recompute_derivative_scene_spec_hash(draft)}
    )
    # Self-validate: the frozen contract replays its own hash (defense-in-depth).
    try:
        spec = DerivativeSceneSpecContract.model_validate(spec.model_dump())
    except ValueError as exc:
        raise DerivativeSceneSpecGateError(
            "spec_gate_violation",
            f"compiled derivative Scene Spec failed its own contract gate: {exc}",
            gate="implicit_canon",
        ) from exc
    return spec


# ---------------------------------------------------------------------------
# Owner-scoped service seam (server-verified rows -> compile input -> spec)
# ---------------------------------------------------------------------------


class DerivativeSceneSpecService:
    """Owner-scoped deterministic derivative Scene Spec compile seam."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def compile(
        self,
        *,
        owner_id: int,
        novel_id: int,
        request: DerivativeSceneSpecCompileRequest,
    ) -> CompiledDerivativeSceneSpec:
        """Compile one canonical spec for an owned approved fork + sealed spec.

        A missing or unapproved upstream contract blocks the compile
        (``DerivativeSceneSpecBlockedError``); a gate failure raises
        ``DerivativeSceneSpecGateError`` with the full check report. No
        provider is ever called here.
        """
        _require_scope(owner_id=owner_id, novel_id=novel_id)

        version = await self._version(
            owner_id=owner_id, novel_id=novel_id, version_id=request.version_id
        )
        if version is None:
            raise DerivativeSceneSpecScopeError(
                "derivative visual fork version not found in the explicit "
                "owner/novel scope"
            )
        if version.review_state != "approved":
            raise DerivativeSceneSpecBlockedError(
                "visual_fork_not_approved",
                "only an approved derivative visual fork can anchor a Scene Spec",
            )

        original = await self._original(
            owner_id=owner_id,
            novel_id=novel_id,
            source_version_id=version.source_version_id,
        )
        if original is None:
            raise DerivativeSceneSpecBlockedError(
                "visual_bible_source_not_found",
                "the Original Visual Bible revision this fork derives from was "
                "not found in the owner/novel scope",
            )
        if original.review_state != "approved":
            raise DerivativeSceneSpecBlockedError(
                "visual_bible_source_not_approved",
                "only an approved Original Visual Bible revision can be forked from",
            )

        spec_row = await self._scene_spec(
            owner_id=owner_id, novel_id=novel_id, spec_id=request.scene_spec_id
        )
        if spec_row is None:
            raise DerivativeSceneSpecBlockedError(
                "scene_spec_not_found",
                "the sealed SceneSpec was not found in the owner/novel scope",
            )
        if spec_row.review_state != "approved":
            raise DerivativeSceneSpecBlockedError(
                "scene_spec_not_approved",
                "only an approved SceneSpec can be sealed into a derivative Scene Spec",
            )

        evidence_rows = (
            await self._session.scalars(
                select(SceneSpecEvidenceRefRow)
                .where(
                    SceneSpecEvidenceRefRow.owner_id == owner_id,
                    SceneSpecEvidenceRefRow.novel_id == novel_id,
                    SceneSpecEvidenceRefRow.spec_id == spec_row.id,
                )
                .order_by(SceneSpecEvidenceRefRow.evidence_key.asc())
            )
        ).all()
        scene_spec_contract = self._reconstruct_scene_spec(spec_row, evidence_rows)

        entity_rows = (
            await self._session.scalars(
                select(DerivativeVisualEntity)
                .where(
                    DerivativeVisualEntity.owner_id == owner_id,
                    DerivativeVisualEntity.novel_id == novel_id,
                    DerivativeVisualEntity.version_id == version.id,
                )
                .order_by(DerivativeVisualEntity.id.asc())
            )
        ).all()
        asset_rows = (
            await self._session.scalars(
                select(DerivativeVisualAsset)
                .where(
                    DerivativeVisualAsset.owner_id == owner_id,
                    DerivativeVisualAsset.novel_id == novel_id,
                    DerivativeVisualAsset.version_id == version.id,
                )
                .order_by(DerivativeVisualAsset.id.asc())
            )
        ).all()

        identity = tuple(
            DerivativeIdentityRow(
                stable_id=row.stable_id,
                entity_key=row.entity_key,
                entity_type=DerivativeVisualEntityType(row.entity_type),
                description=row.description,
                authority=row.authority,
                divergence=row.divergence,
                source_entity_ref=row.source_entity_ref,
                disclosure_cutoff=row.disclosure_cutoff,
            )
            for row in entity_rows
        )
        reference_assets = tuple(
            DerivativeReferenceAssetRow(
                asset_key=row.asset_key,
                asset_id=row.asset_id,
                mime_type=row.mime_type,
                bytes_hash=row.bytes_hash,
                rights_status=DerivativeVisualRightsStatus(row.rights_status),
                source_asset_ref=row.source_asset_ref,
                approved=bool(row.approved),
            )
            for row in asset_rows
        )
        derivative_negative_constraints = self._derivative_constraints(
            version.constraints
        )
        evidence_refs = tuple(
            DerivativeSceneSpecEvidenceRef(
                evidence_key=row.evidence_key,
                source_snapshot_id=row.source_snapshot_id,
                source_snapshot_hash=row.source_snapshot_hash,
                chapter_number=row.chapter_number,
                source_start=row.source_start,
                source_end=row.source_end,
                content_hash=row.content_hash,
                cutoff_chapter=row.cutoff_chapter,
            )
            for row in evidence_rows
        )
        uncertainties = tuple(
            DerivativeSceneSpecUncertainty(
                uncertainty_key=u.uncertainty_key,
                reason=u.reason.value,
                detail=u.detail,
            )
            for u in scene_spec_contract.uncertainties
        )

        # Approved AssetRevision lineage bound to the sealed scene spec
        # (provider-candidate assets, never Original). Anchors reference them.
        asset_revisions = (
            await self._session.scalars(
                select(AssetRevision)
                .where(
                    AssetRevision.owner_id == owner_id,
                    AssetRevision.novel_id == novel_id,
                    AssetRevision.scene_spec_hash == spec_row.content_hash,
                    AssetRevision.approval_state == "proposal_ready",
                )
                .order_by(AssetRevision.id.asc())
            )
        ).all()
        asset_lineage = tuple(
            DerivativeAssetLineageRow(
                asset_revision_id=row.id,
                asset_id=row.asset_id,
                bytes_hash=row.bytes_hash,
                mime_type=row.mime_type,
                approval_state=row.approval_state,
                scene_spec_hash=row.scene_spec_hash,
                provider=row.provider,
                provider_model=row.provider_model,
            )
            for row in asset_revisions
        )
        asset_revision_ids = {row.id for row in asset_revisions}
        anchor_rows = list(
            (
                await self._session.scalars(
                    select(IllustrationAnchor)
                    .where(
                        IllustrationAnchor.owner_id == owner_id,
                        IllustrationAnchor.novel_id == novel_id,
                        IllustrationAnchor.published_asset_revision_id.in_(
                            asset_revision_ids
                        )
                        if asset_revision_ids
                        else IllustrationAnchor.id < 0,
                    )
                    .order_by(IllustrationAnchor.id.asc())
                )
            ).all()
        )
        anchors = tuple(
            DerivativeAnchorRef(
                anchor_id=row.id,
                anchor_key=row.anchor_key,
                chapter_number=row.chapter_number,
                status=row.status,
                asset_revision_id=row.published_asset_revision_id,
                publish_manifest_hash=row.publish_manifest_hash,
            )
            for row in anchor_rows
        )
        export_manifest_hash = _export_manifest_hash(
            [row.publish_manifest_hash for row in anchor_rows]
        )

        compile_input = DerivativeSceneSpecCompileInput(
            owner_id=owner_id,
            novel_id=novel_id,
            project_id=version.project_id,
            fork_id=version.fork_id,
            spec_key=request.spec_key,
            visual_fork_version_id=version.id,
            visual_fork_version_hash=version.canonical_payload_hash,
            visual_fork_review_state=version.review_state,
            visual_namespace=version.visual_namespace,
            divergence=version.divergence,
            provenance=version.provenance,
            cutoff_chapter=version.cutoff_chapter,
            style_profile=version.style_profile,
            identity=identity,
            reference_assets=reference_assets,
            derivative_negative_constraints=derivative_negative_constraints,
            visual_bible_revision_id=original.id,
            visual_bible_revision_hash=original.manifest_hash,
            visual_bible_review_state=original.review_state,
            source_snapshot_id=version.source_snapshot_id,
            source_snapshot_hash=version.source_snapshot_hash,
            source_manifest_hash=version.source_manifest_hash,
            scene_spec_id=spec_row.id,
            scene_spec_hash=spec_row.content_hash,
            scene_spec_review_state=spec_row.review_state,
            scene_spec_visual_bible_revision_hash=spec_row.visual_bible_revision_hash,
            scene_spec_source_snapshot_hash=spec_row.source_snapshot_hash,
            scene_spec_cutoff_chapter=spec_row.cutoff_chapter,
            scene_spec=scene_spec_contract,
            evidence_refs=evidence_refs,
            uncertainties=uncertainties,
            asset_lineage=asset_lineage,
            anchors=anchors,
            export_manifest_hash=export_manifest_hash,
        )
        checks = run_compile_gates(compile_input)
        assert_spec_gates_pass(checks)
        spec = compile_derivative_scene_spec(compile_input)
        return CompiledDerivativeSceneSpec(spec=spec, gate_checks=tuple(checks))

    # ------------------------------------------------------------ read seams

    async def _version(
        self, *, owner_id: int, novel_id: int, version_id: int
    ) -> DerivativeVisualVersion | None:
        return await self._session.scalar(
            select(DerivativeVisualVersion).where(
                DerivativeVisualVersion.owner_id == owner_id,
                DerivativeVisualVersion.novel_id == novel_id,
                DerivativeVisualVersion.id == version_id,
            )
        )

    async def _original(
        self, *, owner_id: int, novel_id: int, source_version_id: int
    ) -> VisualBibleVersion | None:
        return await self._session.scalar(
            select(VisualBibleVersion).where(
                VisualBibleVersion.owner_id == owner_id,
                VisualBibleVersion.novel_id == novel_id,
                VisualBibleVersion.id == source_version_id,
            )
        )

    async def _scene_spec(
        self, *, owner_id: int, novel_id: int, spec_id: int
    ) -> SceneSpecVersionRow | None:
        return await self._session.scalar(
            select(SceneSpecVersionRow).where(
                SceneSpecVersionRow.owner_id == owner_id,
                SceneSpecVersionRow.novel_id == novel_id,
                SceneSpecVersionRow.id == spec_id,
            )
        )

    # -------------------------------------------------------- reconstruction

    @staticmethod
    def _derivative_constraints(
        constraints: list[dict[str, Any]] | None,
    ) -> tuple[DerivativeNegativeConstraint, ...]:
        """Branch-fork negative constraints from the approved derivative version."""
        out: list[DerivativeNegativeConstraint] = []
        for index, entry in enumerate(constraints or []):
            if not isinstance(entry, dict):
                raise DerivativeSceneSpecBlockedError(
                    "derivative_constraint_invalid",
                    f"derivative constraint[{index}] is not an object",
                )
            constraint_key = (
                entry.get("constraint_key") or f"derivative-constraint-{index}"
            )
            text = entry.get("text")
            if not isinstance(text, str) or not text:
                raise DerivativeSceneSpecBlockedError(
                    "derivative_constraint_invalid",
                    f"derivative constraint {constraint_key!r} has no text",
                )
            scope = entry.get("scope")
            if (
                not isinstance(scope, str)
                or scope not in ConstraintScope._value2member_map_
            ):
                raise DerivativeSceneSpecBlockedError(
                    "derivative_constraint_invalid",
                    f"derivative constraint {constraint_key!r} has unsupported scope",
                )
            out.append(
                DerivativeNegativeConstraint(
                    constraint_key=constraint_key,
                    scope=scope,
                    source="derivative",
                    text=text,
                    rationale=(
                        entry["rationale"]
                        if isinstance(entry.get("rationale"), str)
                        else None
                    ),
                )
            )
        return tuple(out)

    def _reconstruct_scene_spec(
        self,
        spec_row: SceneSpecVersionRow,
        evidence_rows: list[SceneSpecEvidenceRefRow]
        | tuple[SceneSpecEvidenceRefRow, ...],
    ) -> SceneSpecContract:
        """Reconstruct the immutable SceneSpec contract from persisted rows.

        Fails closed when a persisted evidence ref is missing or the content
        hash no longer replays — a drifted sealed context can never compile.
        """
        evidence_by_key = {row.evidence_key: row for row in evidence_rows}

        def _refs(keys: list[str]) -> list[SpecEvidenceRef]:
            refs: list[SpecEvidenceRef] = []
            for key in keys:
                row = evidence_by_key.get(key)
                if row is None:
                    raise DerivativeSceneSpecBlockedError(
                        "scene_spec_evidence_missing",
                        f"sealed SceneSpec evidence {key!r} is missing in the "
                        "owner/novel scope",
                    )
                refs.append(
                    SpecEvidenceRef(
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
            return refs

        def _vb_refs(refs: list[dict[str, Any]]) -> list[VisualBibleRef]:
            return [
                VisualBibleRef(
                    stable_id=ref["stable_id"],
                    claim_key=ref.get("claim_key"),
                    revision_id=spec_row.visual_bible_revision_id,
                    revision_hash=spec_row.visual_bible_revision_hash,
                )
                for ref in refs
            ]

        payload = dict(spec_row.canonical_payload or {})
        try:
            details = [
                SceneDetail(
                    detail_key=item["detail_key"],
                    kind=SpecDetailKind(item["kind"]),
                    source=SpecSource(item["source"]),
                    text=item["text"],
                    author=item.get("author"),
                    rationale=item.get("rationale"),
                    evidence_refs=_refs(list(item.get("evidence_keys") or [])),
                    visual_bible_refs=_vb_refs(item.get("visual_bible_refs") or []),
                    spoiler_cutoff=item["spoiler_cutoff"],
                )
                for item in (payload.get("details") or [])
            ]
            constraints = [
                NegativeConstraint(
                    constraint_key=item["constraint_key"],
                    scope=ConstraintScope(item["scope"]),
                    source=SpecSource(item["source"]),
                    text=item["text"],
                    author=item.get("author"),
                    rationale=item.get("rationale"),
                    evidence_refs=_refs(list(item.get("evidence_keys") or [])),
                    visual_bible_refs=_vb_refs(item.get("visual_bible_refs") or []),
                    spoiler_cutoff=item["spoiler_cutoff"],
                )
                for item in (payload.get("negative_constraints") or [])
            ]
            uncertainties = [
                SceneUncertainty(
                    uncertainty_key=item["uncertainty_key"],
                    reason=UncertaintyReason(item["reason"]),
                    detail=item["detail"],
                )
                for item in (payload.get("uncertainties") or [])
            ]
            contract = SceneSpecContract(
                schema_version=spec_row.schema_version,
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
                review_state=SpecReviewState(spec_row.review_state),
            )
        except (ValueError, KeyError) as exc:
            raise DerivativeSceneSpecBlockedError(
                "scene_spec_revalidation_failed",
                f"sealed SceneSpec could not be reconstructed: {exc}",
            ) from exc
        if recompute_scene_spec_hash(contract) != spec_row.content_hash:
            raise DerivativeSceneSpecBlockedError(
                "scene_spec_hash_mismatch",
                "sealed SceneSpec content_hash does not replay from its "
                "persisted content; the sealed story context has drifted",
            )
        return contract


def _export_manifest_hash(manifest_hashes: list[str]) -> str | None:
    """Deterministic export-manifest reference for the bound published anchors."""
    distinct = sorted({h for h in manifest_hashes if h})
    if not distinct:
        return None
    if len(distinct) == 1:
        return distinct[0]
    return canonical_derivative_visual_hash({"export_manifests": distinct})


__all__ = [
    "DERIVATIVE_SCENE_SPEC_COMPILER_ID",
    "DERIVATIVE_SCENE_SPEC_COMPILER_VERSION",
    "DERIVATIVE_SCENE_SPEC_SCHEMA_VERSION",
    "CompiledDerivativeSceneSpec",
    "DerivativeSceneSpecService",
    "compile_derivative_scene_spec",
]
