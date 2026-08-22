"""Continuous Chapter -> Arc -> Volume -> Global candidate hierarchy.

Phase 28-03 (REQ-NM-02/06, D-01/D-02/D-05/D-06/D-07/D-09): only *terminal*
Chapter State feeds the hierarchy. The assembled ``HierarchyCandidate`` is
immutable and candidate-only, every level retains source lineage, and gaps /
overlaps / uncertain boundaries are preserved explicitly. Blocked input can
never be represented as a complete fact.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any, Literal, Mapping, Sequence

from pydantic import Field, model_validator

from app.services.narrative_memory.arc_planner import (
    ArcCandidateRange,
    ChapterTerminalState,
    EvidenceSummary,
    OverlapRange,
    outline_candidate_checksum,
    plan_outline_arcs,
    validate_outline_candidate,
)
from app.services.narrative_memory.builder_contracts import (
    BuilderFrozenModel,
    TerminalState,
    _stable_json,
)
from app.services.narrative_memory.contracts import (
    Hash64,
    Key,
    PositiveInt,
    VersionLabel,
)
from app.services.narrative_memory.global_builder import (
    MainlineCandidateArtifact,
    mainline_candidate_checksum,
    project_mainline_candidate,
    validate_mainline_candidate,
)

HIERARCHY_SCHEMA_VERSION = "hierarchy-candidate.v1"

# Markers that must never appear anywhere in a candidate hierarchy. Generation
# alone may never write or promote Canon (D-07/D-09); these strings are only
# kept in this stripped constant for the fail-closed guard below.
FORBIDDEN_HIERARCHY_MARKERS = frozenset(
    {
        "active_pointer",
        "set_active_pointer",
        "promote_timeline",
        "promote_clue",
        "TimelineActivePointer",
        "ClueActivePointer",
        "NarrativeActivePointer",
        "cutover",
        "production_promotion",
        "reader_chat",
        "conversation_id",
        "message_id",
    }
)

# Stage status -> durable terminal state mapping (D-02). Used to rehydrate
# terminal Chapter State from builder stage rows.
TERMINAL_STATE_BY_STATUS = {
    "completed": TerminalState.COMPLETED,
    "failed": TerminalState.ISOLATED,
    "isolated": TerminalState.ISOLATED,
    "paused_budget": TerminalState.ISOLATED,
    "paused_dependency": TerminalState.ISOLATED,
    "cancelled": TerminalState.ISOLATED,
    "blocked_dependency": TerminalState.BLOCKED,
}


class HierarchyError(ValueError):
    """Fail-closed error for an invalid or promoted hierarchy candidate."""


class HierarchyCandidate(BuilderFrozenModel):
    """Immutable candidate-only hierarchy over terminal Chapter State.

    Embeds the outline (arcs) and mainline (volume/global) candidates and a
    chapter -> arc parent index so lineage is queryable at every level.
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
    outline: Any
    mainline: MainlineCandidateArtifact
    chapter_to_parent: dict[str, Key] = Field(default_factory=dict)
    candidate_status: Literal["candidate"] = "candidate"
    checksum: Hash64

    @model_validator(mode="after")
    def validate_hierarchy(self) -> "HierarchyCandidate":
        if self.candidate_status != "candidate":
            raise ValueError("hierarchy must stay candidate-only")
        if self.outline.source_snapshot_hash != self.source_snapshot_hash:
            raise ValueError("hierarchy snapshot must match the outline")
        if (
            self.outline.chapter_min != self.chapter_min
            or self.outline.chapter_max != self.chapter_max
        ):
            raise ValueError("hierarchy range must match the outline")
        if self.mainline.source_snapshot_hash != self.source_snapshot_hash:
            raise ValueError("hierarchy snapshot must match the mainline")
        arc_keys = {arc.stage_key for arc in self.outline.arcs}
        if any(parent not in arc_keys for parent in self.chapter_to_parent.values()):
            raise ValueError("chapter_to_parent must reference outline arc keys")
        return self


def _collect_forbidden_keys(
    payload: object,
    markers: frozenset[str],
    path: str = "$",
) -> set[str]:
    """Recursively collect any forbidden canonical marker key present."""
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key) in markers:
                found.add(str(key))
            found |= _collect_forbidden_keys(value, markers, f"{path}.{key}")
    elif isinstance(payload, (list, tuple)):
        for index, value in enumerate(payload):
            found |= _collect_forbidden_keys(value, markers, f"{path}[{index}]")
    return found


def hierarchy_checksum_value(candidate: HierarchyCandidate) -> str:
    """Deterministic checksum over every field except ``checksum`` itself."""
    payload = candidate.model_dump(mode="json")
    payload.pop("checksum", None)
    return sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def build_hierarchy_candidate(
    *,
    chapters: Sequence[ChapterTerminalState],
    evidence_by_chapter: Mapping[int, EvidenceSummary] | None = None,
    policy_version: str = "arc-policy.v1",
    window_size: int = 3,
    volume_arc_window: int = 2,
    owner_id: int = 1,
    novel_id: int = 1,
    version_id: int = 1,
    hierarchy_build_id: str = "hierarchy:unknown",
    hierarchy_checksum: str = "0" * 64,
) -> HierarchyCandidate:
    """Assemble the continuous Chapter -> Arc -> Volume -> Global candidate.

    Consumes only terminal Chapter State (D-01/D-02). Gaps and uncertain
    boundaries are preserved explicitly; the result is immutable candidate-only
    with source lineage at every level (D-05/D-07/D-09).
    """
    outline = plan_outline_arcs(
        chapters=chapters,
        evidence_by_chapter=evidence_by_chapter,
        policy_version=policy_version,
        window_size=window_size,
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        hierarchy_build_id=hierarchy_build_id,
        hierarchy_checksum=hierarchy_checksum,
    )
    mainline = project_mainline_candidate(
        outline=outline,
        volume_arc_window=volume_arc_window,
    )
    chapter_to_parent = {
        str(number): arc.stage_key
        for arc in outline.arcs
        for number in arc.chapter_numbers
    }
    placeholder = HierarchyCandidate(
        schema_version=HIERARCHY_SCHEMA_VERSION,
        policy_version=policy_version,
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        source_snapshot_hash=outline.source_snapshot_hash,
        hierarchy_build_id=hierarchy_build_id,
        hierarchy_checksum=hierarchy_checksum,
        input_hash=outline.input_hash,
        chapter_min=outline.chapter_min,
        chapter_max=outline.chapter_max,
        outline=outline,
        mainline=mainline,
        chapter_to_parent=chapter_to_parent,
        candidate_status="candidate",
        checksum="0" * 64,
    )
    return placeholder.model_copy(
        update={"checksum": hierarchy_checksum_value(placeholder)}
    )


def validate_hierarchy_candidate(candidate: HierarchyCandidate) -> None:
    """Fail closed unless every level is immutable, candidate-only and intact."""
    if candidate.candidate_status != "candidate":
        raise HierarchyError("hierarchy must stay candidate-only")
    if candidate.checksum != hierarchy_checksum_value(candidate):
        raise HierarchyError("hierarchy checksum mismatch")
    validate_outline_candidate(candidate.outline)
    validate_mainline_candidate(candidate.mainline)
    assert_blocked_not_complete_fact(candidate)


def lineage_for_chapter(
    candidate: HierarchyCandidate,
    chapter_number: int,
) -> tuple[dict[str, Any], ...]:
    """Queryable lineage for one chapter across arc/volume/global levels.

    A chapter inside a gap resolves to a single ``gap`` lineage record instead
    of a parent chain, so blocked/isolated input is never reported as fact.
    """
    number = int(chapter_number)
    arc_key = candidate.chapter_to_parent.get(str(number))
    if arc_key is None:
        return (
            {
                "chapter_number": number,
                "kind": "gap",
                "candidate_status": candidate.candidate_status,
                "source_snapshot_hash": candidate.source_snapshot_hash,
                "hierarchy_build_id": candidate.hierarchy_build_id,
                "hierarchy_checksum": candidate.hierarchy_checksum,
                "input_hash": candidate.input_hash,
            },
        )
    chain: list[dict[str, Any]] = []
    arc = next(arc for arc in candidate.outline.arcs if arc.stage_key == arc_key)
    chain.append(
        {
            "chapter_number": number,
            "kind": "story_arc",
            "stage_key": arc.stage_key,
            "chapter_start": arc.chapter_start,
            "chapter_end": arc.chapter_end,
            "coverage": arc.coverage,
            "uncertainty": arc.uncertainty.value,
            "confidence": arc.confidence,
            "input_hash": arc.input_hash,
            "source_snapshot_hash": candidate.source_snapshot_hash,
            "hierarchy_build_id": candidate.hierarchy_build_id,
            "hierarchy_checksum": candidate.hierarchy_checksum,
        }
    )
    volume = next(
        (
            volume
            for volume in candidate.mainline.volumes
            if arc.stage_key in volume.arc_stage_keys
        ),
        None,
    )
    if volume is not None:
        chain.append(
            {
                "kind": "volume",
                "stage_key": volume.stage_key,
                "chapter_start": volume.chapter_start,
                "chapter_end": volume.chapter_end,
                "coverage": volume.coverage,
                "uncertainty": volume.uncertainty.value,
                "confidence": volume.confidence,
                "input_hash": volume.input_hash,
                "source_snapshot_hash": candidate.source_snapshot_hash,
                "hierarchy_build_id": candidate.hierarchy_build_id,
                "hierarchy_checksum": candidate.hierarchy_checksum,
            }
        )
    global_projection = candidate.mainline.global_projection
    chain.append(
        {
            "kind": "global_story",
            "stage_key": global_projection.stage_key,
            "chapter_start": global_projection.chapter_start,
            "chapter_end": global_projection.chapter_end,
            "coverage": global_projection.coverage,
            "uncertainty": global_projection.uncertainty.value,
            "confidence": global_projection.confidence,
            "input_hash": global_projection.input_hash,
            "source_snapshot_hash": candidate.source_snapshot_hash,
            "hierarchy_build_id": candidate.hierarchy_build_id,
            "hierarchy_checksum": candidate.hierarchy_checksum,
        }
    )
    return tuple(chain)


def coverage_analysis(candidate: HierarchyCandidate) -> dict[str, Any]:
    """Explicit coverage summary: continuous ranges, gaps, overlaps, fractions."""
    completed = {
        int(number) for arc in candidate.outline.arcs for number in arc.chapter_numbers
    }
    total = candidate.chapter_max - candidate.chapter_min + 1
    gap_chapters = {
        number
        for gap in candidate.outline.gaps
        for number in range(gap.chapter_start, gap.chapter_end + 1)
    }
    continuous = bool(candidate.outline.covered_ranges) and (
        len(candidate.outline.covered_ranges) == 1
        and candidate.outline.covered_ranges[0]
        == (candidate.chapter_min, candidate.chapter_max)
    )
    return {
        "chapter_min": candidate.chapter_min,
        "chapter_max": candidate.chapter_max,
        "covered_ranges": [list(range_) for range_ in candidate.outline.covered_ranges],
        "gaps": [
            {
                "chapter_start": gap.chapter_start,
                "chapter_end": gap.chapter_end,
                "reason_codes": list(gap.reason_codes),
                "terminal_states": list(gap.terminal_states),
            }
            for gap in candidate.outline.gaps
        ],
        "overlaps": [
            {
                "chapter_start": overlap.chapter_start,
                "chapter_end": overlap.chapter_end,
                "arc_keys": list(overlap.arc_keys),
            }
            for overlap in candidate.outline.overlaps
        ],
        "completed_chapters": sorted(completed),
        "gap_chapters": sorted(gap_chapters),
        "completed_count": len(completed),
        "gap_count": total - len(completed),
        "coverage_fraction": round(len(completed) / total, 4) if total else 0.0,
        "continuous": continuous,
    }


def assert_hierarchy_candidate_only(candidate: HierarchyCandidate) -> None:
    """Fail closed if any canonical/pointer marker appears in the hierarchy.

    Candidate outputs are immutable and never promote Canon (D-07/D-09).
    """
    if candidate.candidate_status != "candidate":
        raise HierarchyError("hierarchy must remain candidate-only")
    bad = _collect_forbidden_keys(
        candidate.model_dump(mode="json"), FORBIDDEN_HIERARCHY_MARKERS
    )
    if bad:
        raise HierarchyError(
            f"candidate hierarchy carries canonical markers: {sorted(bad)}"
        )


def assert_blocked_not_complete_fact(candidate: HierarchyCandidate) -> None:
    """Fail closed if blocked/isolated chapters are disguised as arc facts."""
    arc_chapters = {
        int(number) for arc in candidate.outline.arcs for number in arc.chapter_numbers
    }
    gap_chapters = {
        number
        for gap in candidate.outline.gaps
        for number in range(gap.chapter_start, gap.chapter_end + 1)
    }
    disguised = arc_chapters & gap_chapters
    if disguised:
        raise HierarchyError(
            f"blocked/isolated chapters appear in arc facts: {sorted(disguised)}"
        )
    for gap in candidate.outline.gaps:
        if gap.chapter_end < gap.chapter_start:
            raise HierarchyError("invalid gap range")


def detect_arc_overlaps(
    arcs: Sequence[ArcCandidateRange],
) -> tuple[OverlapRange, ...]:
    """Explicitly detect and preserve any chapter claimed by multiple arcs."""
    covered: dict[int, list[str]] = {}
    for arc in arcs:
        for number in arc.chapter_numbers:
            covered.setdefault(int(number), []).append(arc.stage_key)
    overlaps: list[OverlapRange] = []
    for number in sorted(covered):
        keys = covered[number]
        if len(keys) > 1:
            overlaps.append(
                OverlapRange(
                    chapter_start=number,
                    chapter_end=number,
                    arc_keys=tuple(keys),
                )
            )
    return tuple(overlaps)


def assert_no_arc_overlaps(arcs: Sequence[ArcCandidateRange]) -> None:
    """Fail closed when the candidate arc set overlaps any chapter."""
    overlaps = detect_arc_overlaps(arcs)
    if overlaps:
        raise HierarchyError(
            f"arc overlap detected: {[o.model_dump() for o in overlaps]}"
        )


def chapter_terminal_state_from_status(status: str) -> TerminalState:
    """Map a builder stage status to a durable terminal state (D-02)."""
    terminal = TERMINAL_STATE_BY_STATUS.get(status)
    if terminal is None:
        raise HierarchyError(f"unknown stage status for terminal mapping: {status}")
    return terminal


def outline_and_mainline_checksums(
    candidate: HierarchyCandidate,
) -> tuple[str, str]:
    """Expose the nested artifact checksums for audit/lineage parity."""
    return (
        outline_candidate_checksum(candidate.outline),
        mainline_candidate_checksum(candidate.mainline),
    )


__all__ = [
    "HIERARCHY_SCHEMA_VERSION",
    "TERMINAL_STATE_BY_STATUS",
    "HierarchyCandidate",
    "HierarchyError",
    "assert_blocked_not_complete_fact",
    "assert_hierarchy_candidate_only",
    "assert_no_arc_overlaps",
    "build_hierarchy_candidate",
    "chapter_terminal_state_from_status",
    "coverage_analysis",
    "detect_arc_overlaps",
    "hierarchy_checksum_value",
    "lineage_for_chapter",
    "outline_and_mainline_checksums",
    "validate_hierarchy_candidate",
]
