"""Validated-parent-only Global Story package construction and Mainline candidates.

Phase 28-03 (REQ-NM-02/06, D-01/D-05/D-07/D-09): beside the validated-parent
``CandidatePackage`` construction, this module projects candidate Volume and
Global ranges into the immutable ``MainlineCandidateArtifact``. The projection
consumes only the terminal-chapter ``OutlineCandidateArtifact``; gaps,
overlaps and uncertain boundaries are preserved verbatim, and the output never
enters Canon by generation alone.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Annotated, Any, Literal, Sequence

from pydantic import Field, StrictFloat

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.narrative_memory import (
    NarrativeMemoryClaim,
    NarrativeMemoryNode,
    NarrativeMemorySourceLink,
    NarrativeMemoryVersion,
)
from app.services.narrative_memory.arc_planner import (
    UNCERTAINTY_RANK,
    ArcCandidateRange,
    BoundaryUncertainty,
    GapRange,
    OutlineCandidateArtifact,
    OverlapRange,
    _stable_json,
)
from app.services.narrative_memory.builder_contracts import BuilderFrozenModel
from app.services.narrative_memory.builder_packages import (
    PackageBuildError,
    build_global_candidate,
)
from app.services.narrative_memory.contracts import (
    CandidatePackage,
    Hash64,
    Key,
    NodeKind,
    PositiveInt,
    Uncertainty,
    VersionLabel,
)


async def load_validated_parents(
    session: AsyncSession,
    *,
    owner_id: int,
    novel_id: int,
    version_id: int,
    expected_parent_keys: Sequence[str] | None = None,
) -> tuple[
    list[NarrativeMemoryNode],
    list[NarrativeMemoryClaim],
    list[NarrativeMemorySourceLink],
]:
    parents = (
        await session.scalars(
            select(NarrativeMemoryNode)
            .where(
                NarrativeMemoryNode.owner_id == owner_id,
                NarrativeMemoryNode.novel_id == novel_id,
                NarrativeMemoryNode.version_id == version_id,
                NarrativeMemoryNode.node_kind.in_(
                    [NodeKind.STORY_ARC.value, NodeKind.VOLUME.value]
                ),
            )
            .order_by(NarrativeMemoryNode.chapter_start, NarrativeMemoryNode.node_key)
        )
    ).all()
    if not parents:
        raise PackageBuildError("no middle-level parents for global")
    if expected_parent_keys is not None:
        keys = {p.node_key for p in parents}
        missing = set(expected_parent_keys) - keys
        if missing:
            raise PackageBuildError(f"missing parent keys: {sorted(missing)}")
    parent_ids = [p.id for p in parents]
    claims = (
        await session.scalars(
            select(NarrativeMemoryClaim)
            .where(
                NarrativeMemoryClaim.owner_id == owner_id,
                NarrativeMemoryClaim.novel_id == novel_id,
                NarrativeMemoryClaim.version_id == version_id,
                NarrativeMemoryClaim.node_id.in_(parent_ids),
            )
            .order_by(NarrativeMemoryClaim.claim_key)
        )
    ).all()
    claim_ids = [c.id for c in claims]
    links = (
        await session.scalars(
            select(NarrativeMemorySourceLink)
            .where(
                NarrativeMemorySourceLink.owner_id == owner_id,
                NarrativeMemorySourceLink.novel_id == novel_id,
                NarrativeMemorySourceLink.version_id == version_id,
                NarrativeMemorySourceLink.claim_id.in_(claim_ids),
            )
            .order_by(NarrativeMemorySourceLink.id)
        )
    ).all()
    return list(parents), list(claims), list(links)


def build_global_package_from_parents(
    *,
    version: NarrativeMemoryVersion,
    parents: Sequence[NarrativeMemoryNode],
    claims: Sequence[NarrativeMemoryClaim],
    links: Sequence[NarrativeMemorySourceLink],
    model_claims: Sequence[dict[str, Any]] | None = None,
) -> CandidatePackage:
    if not parents:
        raise PackageBuildError("global requires parents")
    chapter_start = min(p.chapter_start for p in parents)
    chapter_end = max(p.chapter_end for p in parents)
    return build_global_candidate(
        chapter_start=chapter_start,
        chapter_end=chapter_end,
        parent_nodes=parents,
        parent_claims=claims,
        parent_links=links,
        model_claims=model_claims,
    )


# ---------------------------------------------------------------------------
# Phase 28-03: candidate Volume/Global projections (D-01/D-05/D-07/D-09).
# These are immutable candidate-only projections over the terminal-chapter
# outline. Gaps and uncertain boundaries are preserved, never normalised.
# ---------------------------------------------------------------------------

MAINLINE_SCHEMA_VERSION = "mainline-candidate-artifact.v1"


class VolumeProjection(BuilderFrozenModel):
    """One candidate Volume range grouping consecutive arc candidates."""

    stage_key: Key
    node_kind: Literal["volume"] = "volume"
    chapter_start: PositiveInt
    chapter_end: PositiveInt
    arc_stage_keys: tuple[Key, ...]
    coverage: Literal["complete", "partial", "empty"]
    uncertainty: Uncertainty
    confidence: Annotated[StrictFloat, Field(ge=0.0, le=1.0)]
    boundary_uncertainties: tuple[BoundaryUncertainty, ...] = ()
    input_hash: Hash64

    @property
    def child_stage_keys(self) -> tuple[Key, ...]:
        return self.arc_stage_keys


class GlobalProjection(BuilderFrozenModel):
    """One candidate Global Story projection over the whole snapshot range.

    ``chapter_start``/``chapter_end`` cover the full frozen snapshot range; any
    interior gaps keep the coverage ``partial`` and remain explicit.
    """

    stage_key: Key = "global_story:book"
    node_kind: Literal["global_story"] = "global_story"
    chapter_start: PositiveInt
    chapter_end: PositiveInt
    child_stage_keys: tuple[Key, ...]
    coverage: Literal["complete", "partial", "empty"]
    uncertainty: Uncertainty
    confidence: Annotated[StrictFloat, Field(ge=0.0, le=1.0)]
    boundary_uncertainties: tuple[BoundaryUncertainty, ...] = ()
    input_hash: Hash64


class MainlineCandidateArtifact(BuilderFrozenModel):
    """Immutable candidate Volume/Global projection (D-07/D-09).

    Retains the outline's source snapshot, chapter/range lineage, input hash,
    gaps, overlaps, uncertainty and candidate status. It is never promoted to
    Canon by generation alone.
    """

    schema_version: VersionLabel
    policy_version: VersionLabel
    owner_id: PositiveInt
    novel_id: PositiveInt
    version_id: PositiveInt
    source_snapshot_hash: Hash64
    hierarchy_build_id: Key
    hierarchy_checksum: Hash64
    input_hash: Hash64
    chapter_min: PositiveInt
    chapter_max: PositiveInt
    volumes: tuple[VolumeProjection, ...]
    global_projection: GlobalProjection
    covered_ranges: tuple[tuple[PositiveInt, PositiveInt], ...]
    gaps: tuple[GapRange, ...] = ()
    overlaps: tuple[OverlapRange, ...] = ()
    candidate_status: Literal["candidate"] = "candidate"
    lineage: dict[str, Any] = Field(default_factory=dict)
    checksum: Hash64


class VolumeProjectionError(ValueError):
    pass


def _coverage_for_span(
    span_start: int,
    span_end: int,
    covered: set[int],
) -> str:
    if all(number in covered for number in range(span_start, span_end + 1)):
        return "complete"
    if covered.intersection(range(span_start, span_end + 1)):
        return "partial"
    return "empty"


def _volume_boundary_uncertainties(
    *,
    start: int,
    end: int,
    chapter_min: int,
    chapter_max: int,
    gaps: Sequence[GapRange],
) -> tuple[BoundaryUncertainty, ...]:
    """Boundary uncertainty for a volume span.

    A volume boundary that touches a gap is ``adjacent_gap``; at the snapshot
    edge it is ``snapshot_edge``; otherwise it inherits the arc boundary's own
    reason (already recorded on the outline arc).
    """
    boundaries: list[BoundaryUncertainty] = []
    if start == chapter_min:
        boundaries.append(
            BoundaryUncertainty(
                side="start", chapter_number=start, reason="snapshot_edge"
            )
        )
    elif any(g.chapter_end == start - 1 for g in gaps):
        boundaries.append(
            BoundaryUncertainty(
                side="start", chapter_number=start, reason="adjacent_gap"
            )
        )
    if end == chapter_max:
        boundaries.append(
            BoundaryUncertainty(
                side="end", chapter_number=end, reason="snapshot_edge"
            )
        )
    elif any(g.chapter_start == end + 1 for g in gaps):
        boundaries.append(
            BoundaryUncertainty(
                side="end", chapter_number=end, reason="adjacent_gap"
            )
        )
    return tuple(boundaries)


def project_volumes(
    *,
    arcs: Sequence[ArcCandidateRange],
    volume_arc_window: int = 2,
    gaps: Sequence[GapRange] = (),
    chapter_min: int,
    chapter_max: int,
) -> tuple[VolumeProjection, ...]:
    """Group consecutive arc candidates into candidate Volume projections.

    A volume may span an interior gap; the gap stays explicit and coverage is
    reported as ``partial``. Empty arc input produces no volumes.
    """
    if volume_arc_window < 1:
        raise VolumeProjectionError("volume_arc_window must be positive")
    ordered = sorted(arcs, key=lambda arc: (arc.chapter_start, arc.chapter_end))
    volumes: list[VolumeProjection] = []
    for offset in range(0, len(ordered), volume_arc_window):
        group = ordered[offset : offset + volume_arc_window]
        start = group[0].chapter_start
        end = group[-1].chapter_end
        covered = {int(n) for arc in group for n in arc.chapter_numbers}
        levels = [arc.uncertainty for arc in group]
        confidences = [arc.confidence for arc in group]
        uncertainty = max(levels, key=lambda level: UNCERTAINTY_RANK.get(level, 2))
        confidence = (
            round(sum(confidences) / len(confidences), 4) if confidences else 0.0
        )
        volumes.append(
            VolumeProjection(
                stage_key=f"volume:{start}-{end}",
                chapter_start=start,
                chapter_end=end,
                arc_stage_keys=tuple(arc.stage_key for arc in group),
                coverage=_coverage_for_span(start, end, covered),
                uncertainty=uncertainty,
                confidence=confidence,
                boundary_uncertainties=_volume_boundary_uncertainties(
                    start=start,
                    end=end,
                    chapter_min=chapter_min,
                    chapter_max=chapter_max,
                    gaps=gaps,
                ),
                input_hash=sha256(
                    _stable_json(
                        {
                            "start": start,
                            "end": end,
                            "arc_keys": [arc.stage_key for arc in group],
                        }
                    ).encode("utf-8")
                ).hexdigest(),
            )
        )
    return tuple(volumes)


def mainline_candidate_checksum(
    artifact: MainlineCandidateArtifact,
) -> str:
    """Deterministic checksum over every field except ``checksum`` itself."""
    payload = artifact.model_dump(mode="json")
    payload.pop("checksum", None)
    return sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def validate_mainline_candidate(
    artifact: MainlineCandidateArtifact,
) -> None:
    """Fail closed if a mainline candidate loses its immutable integrity."""
    if artifact.candidate_status != "candidate":
        raise ValueError("mainline candidate must stay candidate-only")
    if artifact.checksum != mainline_candidate_checksum(artifact):
        raise ValueError("mainline candidate checksum mismatch")
    if not artifact.lineage:
        raise ValueError("mainline candidate must carry source lineage")
    if artifact.volumes and artifact.global_projection.child_stage_keys != tuple(
        volume.stage_key for volume in artifact.volumes
    ):
        raise ValueError("global projection must reference the projected volumes")


def project_mainline_candidate(
    *,
    outline: OutlineCandidateArtifact,
    volume_arc_window: int = 2,
) -> MainlineCandidateArtifact:
    """Project candidate Volume/Global ranges from the outline candidate.

    The global projection always covers the full frozen snapshot range; gaps
    keep its coverage ``partial`` and remain explicit. The result is immutable
    candidate-only with full source lineage.
    """
    volumes = project_volumes(
        arcs=outline.arcs,
        volume_arc_window=volume_arc_window,
        gaps=outline.gaps,
        chapter_min=outline.chapter_min,
        chapter_max=outline.chapter_max,
    )
    arc_chapters = {int(n) for arc in outline.arcs for n in arc.chapter_numbers}
    if volumes:
        global_uncertainty = max(
            (volume.uncertainty for volume in volumes),
            key=lambda level: UNCERTAINTY_RANK.get(level, 2),
        )
        global_confidence = round(
            sum(volume.confidence for volume in volumes) / len(volumes), 4
        )
        global_coverage = (
            "complete"
            if not outline.gaps
            else ("partial" if arc_chapters else "empty")
        )
        global_children = tuple(volume.stage_key for volume in volumes)
        global_boundaries = _volume_boundary_uncertainties(
            start=outline.chapter_min,
            end=outline.chapter_max,
            chapter_min=outline.chapter_min,
            chapter_max=outline.chapter_max,
            gaps=outline.gaps,
        )
    else:
        global_uncertainty = Uncertainty.UNKNOWN
        global_confidence = 0.0
        global_coverage = "empty"
        global_children = ()
        global_boundaries = (
            BoundaryUncertainty(
                side="start",
                chapter_number=outline.chapter_min,
                reason="snapshot_edge",
            ),
            BoundaryUncertainty(
                side="end",
                chapter_number=outline.chapter_max,
                reason="snapshot_edge",
            ),
        )

    global_projection = GlobalProjection(
        chapter_start=outline.chapter_min,
        chapter_end=outline.chapter_max,
        child_stage_keys=global_children,
        coverage=global_coverage,
        uncertainty=global_uncertainty,
        confidence=global_confidence,
        boundary_uncertainties=global_boundaries,
        input_hash=sha256(
            _stable_json(
                {
                    "chapter_min": outline.chapter_min,
                    "chapter_max": outline.chapter_max,
                    "gaps": [
                        (gap.chapter_start, gap.chapter_end) for gap in outline.gaps
                    ],
                }
            ).encode("utf-8")
        ).hexdigest(),
    )
    input_hash = sha256(
        _stable_json(
            {
                "volumes": [volume.stage_key for volume in volumes],
                "global": global_projection.stage_key,
            }
        ).encode("utf-8")
    ).hexdigest()
    lineage = {
        "source_snapshot_hash": outline.source_snapshot_hash,
        "hierarchy_build_id": outline.hierarchy_build_id,
        "hierarchy_checksum": outline.hierarchy_checksum,
        "volume_input_hashes": {
            volume.stage_key: volume.input_hash for volume in volumes
        },
        "global_input_hash": global_projection.input_hash,
    }
    placeholder = MainlineCandidateArtifact(
        schema_version=MAINLINE_SCHEMA_VERSION,
        policy_version=outline.policy_version,
        owner_id=outline.owner_id,
        novel_id=outline.novel_id,
        version_id=outline.version_id,
        source_snapshot_hash=outline.source_snapshot_hash,
        hierarchy_build_id=outline.hierarchy_build_id,
        hierarchy_checksum=outline.hierarchy_checksum,
        input_hash=input_hash,
        chapter_min=outline.chapter_min,
        chapter_max=outline.chapter_max,
        volumes=volumes,
        global_projection=global_projection,
        covered_ranges=outline.covered_ranges,
        gaps=outline.gaps,
        overlaps=outline.overlaps,
        candidate_status="candidate",
        lineage=lineage,
        checksum="0" * 64,
    )
    return placeholder.model_copy(
        update={"checksum": mainline_candidate_checksum(placeholder)}
    )
