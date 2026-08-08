"""Scene-spec service primitives (dependency-free leaf).

Extracted from ``service.py`` (refactor split): pure, session-free helpers
shared by the ``CompileMixin`` / ``MutationMixin`` / ``ReadQueryMixin`` seams.
The leaf imports only models/schemas — never ``service.py`` nor any mixin —
so the service package dependency graph stays acyclic. ``service.py``
re-exports ``_reconstruct_candidate`` to keep the facade's module surface
unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.models.key_scene import (
    SceneCandidate as SceneCandidateRow,
    SceneCandidateSet as SceneCandidateSetRow,
    SceneEvidenceRange as SceneEvidenceRangeRow,
)
from app.models.scene_spec import SceneSpecVersion as SceneSpecVersionRow
from app.schemas.key_scene import SceneCandidateContract, SceneEvidenceRange
from app.schemas.scene_spec import (
    NegativeConstraint,
    NegativeConstraintView,
    SceneDetail,
    SceneDetailView,
    SceneSpecContract,
    SceneSpecView,
    SceneUncertainty,
    SceneUncertaintyView,
    SpecEvidenceRef,
    VisualBibleRef,
    build_prompt_sections,
    canonical_scene_spec_hash,
)


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


async def view_from_rows(*, spec: SceneSpecVersionRow) -> SceneSpecView:
    """Project one persisted version row into the read-only view."""
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


def view_from_contract(spec: SceneSpecContract) -> SceneSpecView:
    """Project a compiled contract (not yet persisted) into the read-only view."""
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


def sections_from_payload(payload: Mapping[str, Any]) -> dict[str, str]:
    """Deterministic canonical sections of a stored payload (for diffing)."""

    def _detail_from_item(item: Mapping[str, Any]) -> SceneDetail | None:
        evidence_refs = [
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
        ]
        visual_bible_refs = [
            VisualBibleRef(
                stable_id=ref["stable_id"],
                claim_key=ref.get("claim_key"),
                revision_hash="0" * 64,
            )
            for ref in (item.get("visual_bible_refs") or [])
        ]
        source = item["source"]
        # D-32-02 source-shape gate: a stored payload whose source cannot be
        # satisfied (e.g. source='visual_bible' with no refs) must not raise
        # an uncaught ValueError from the strict validator and turn diff into
        # a 500 (WR-05). Such malformed items are skipped for rendering.
        if source == "evidence" and not evidence_refs:
            return None
        if source == "visual_bible" and not visual_bible_refs:
            return None
        if source == "user_interpretation":
            if not item.get("author") or not item.get("rationale"):
                return None
            evidence_refs = []
            visual_bible_refs = []
        return SceneDetail(
            detail_key=item["detail_key"],
            kind=item["kind"],
            source=source,
            text=item["text"],
            author=item.get("author"),
            rationale=item.get("rationale"),
            evidence_refs=evidence_refs,
            visual_bible_refs=visual_bible_refs,
            spoiler_cutoff=item["spoiler_cutoff"],
        )

    def _constraint_from_item(item: Mapping[str, Any]) -> NegativeConstraint | None:
        evidence_refs = [
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
        ]
        visual_bible_refs = [
            VisualBibleRef(
                stable_id=ref["stable_id"],
                claim_key=ref.get("claim_key"),
                revision_hash="0" * 64,
            )
            for ref in (item.get("visual_bible_refs") or [])
        ]
        source = item["source"]
        if source == "evidence" and not evidence_refs:
            return None
        if source == "visual_bible" and not visual_bible_refs:
            return None
        if source == "user_interpretation":
            if not item.get("author") or not item.get("rationale"):
                return None
            evidence_refs = []
            visual_bible_refs = []
        return NegativeConstraint(
            constraint_key=item["constraint_key"],
            scope=item["scope"],
            source=source,
            text=item["text"],
            author=item.get("author"),
            rationale=item.get("rationale"),
            evidence_refs=evidence_refs,
            visual_bible_refs=visual_bible_refs,
            spoiler_cutoff=item["spoiler_cutoff"],
        )

    details: list[SceneDetail] = []
    for item in payload.get("details") or []:
        detail = _detail_from_item(item)
        if detail is not None:
            details.append(detail)
    constraints: list[NegativeConstraint] = []
    for item in payload.get("negative_constraints") or []:
        constraint = _constraint_from_item(item)
        if constraint is not None:
            constraints.append(constraint)
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
        negative_constraints=constraints,
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


def same_provenance(
    existing: SceneSpecVersionRow, spec: SceneSpecContract
) -> bool:
    """Replay requires identical provenance, not just identical content hash:
    the same source candidate, the same approved Visual Bible revision and
    the same source snapshot. A retry with a different candidate/revision but
    identical content would otherwise be misreported as a replay of the
    existing row (WR-06)."""
    return (
        existing.visual_bible_revision_id == spec.visual_bible_revision_id
        and existing.scene_candidate_id == spec.scene_candidate_id
        and existing.source_snapshot_id == spec.source_snapshot_id
    )


def version_idempotency_key(spec: SceneSpecContract) -> str:
    return canonical_scene_spec_hash(
        {
            "kind": "scene_spec.version",
            "owner_id": spec.owner_id,
            "novel_id": spec.novel_id,
            "spec_key": spec.spec_key,
            "content_hash": spec.content_hash,
        }
    )
