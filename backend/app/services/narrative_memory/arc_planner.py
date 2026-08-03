"""Deterministic Volume/Arc boundary planner and Outline candidates (no provider calls).

Phase 28-03 (REQ-NM-02/06, D-01/D-05/D-07/D-09): the deterministic plan
functions remain the authority for the builder control plane. On top of them,
``plan_outline_arcs`` consumes *only terminal* Chapter State and builds the
immutable ``OutlineCandidateArtifact`` — evidence-backed arc boundaries,
explicit gaps/overlaps/uncertain boundaries, and source lineage at every
level. Generation never writes or promotes Canon (D-07/D-09).
"""

from __future__ import annotations

from hashlib import sha256
from typing import Annotated, Any, Literal, Mapping, Sequence

from pydantic import Field, StringConstraints, StrictFloat, StrictInt, StrictStr, model_validator

from app.services.narrative_memory.builder_contracts import (
    BuilderFrozenModel,
    TerminalState,
    _stable_json,
)
from app.services.narrative_memory.contracts import (
    Hash64,
    Key,
    PositiveInt,
    Uncertainty,
    VersionLabel,
)


class BoundaryPlanError(ValueError):
    pass


def plan_arc_boundaries(
    *,
    chapter_numbers: Sequence[int],
    window_size: int = 3,
    policy_version: str = "arc-policy.v1",
    explicit_volumes: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a frozen full-cover continuous non-overlapping boundary plan."""

    chapters = sorted({int(n) for n in chapter_numbers})
    if not chapters:
        raise BoundaryPlanError("chapter_numbers must be non-empty")
    if chapters != list(range(chapters[0], chapters[-1] + 1)):
        raise BoundaryPlanError("eligible chapters must be continuous")
    if window_size < 1:
        raise BoundaryPlanError("window_size must be positive")

    if explicit_volumes:
        ranges = _validate_explicit_volumes(chapters, explicit_volumes)
        source_kind = "explicit_volume"
    else:
        ranges = _fallback_windows(chapters, window_size=window_size)
        source_kind = "deterministic_arc"

    plan = {
        "policy_version": policy_version,
        "source_kind": source_kind,
        "chapter_min": chapters[0],
        "chapter_max": chapters[-1],
        "ranges": ranges,
        "chapter_to_parent": {
            str(chapter): item["stage_key"]
            for item in ranges
            for chapter in item["chapter_numbers"]
        },
        "parent_to_global": {item["stage_key"]: "global_story:book" for item in ranges},
    }
    plan["checksum"] = boundary_plan_checksum(plan)
    return plan


def boundary_plan_checksum(plan: dict[str, Any]) -> str:
    body = {
        "policy_version": plan["policy_version"],
        "source_kind": plan["source_kind"],
        "chapter_min": plan["chapter_min"],
        "chapter_max": plan["chapter_max"],
        "ranges": [
            {
                "stage_key": item["stage_key"],
                "node_kind": item["node_kind"],
                "chapter_start": item["chapter_start"],
                "chapter_end": item["chapter_end"],
                "chapter_numbers": list(item["chapter_numbers"]),
            }
            for item in plan["ranges"]
        ],
    }
    return sha256(_stable_json(body).encode("utf-8")).hexdigest()


def blocked_closure_for_chapter(
    plan: dict[str, Any], *, chapter_number: int
) -> tuple[str, ...]:
    parent = plan["chapter_to_parent"].get(str(chapter_number))
    if parent is None:
        return ()
    return (parent, "global_story:book")


def _validate_explicit_volumes(
    chapters: list[int], volumes: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    ranges: list[dict[str, Any]] = []
    covered: list[int] = []
    for index, volume in enumerate(volumes, start=1):
        start = int(volume["chapter_start"])
        end = int(volume["chapter_end"])
        if start <= 0 or end < start:
            raise BoundaryPlanError("invalid volume range")
        span = list(range(start, end + 1))
        if any(n not in chapters for n in span):
            raise BoundaryPlanError("volume references chapter outside eligible set")
        if set(span) & set(covered):
            raise BoundaryPlanError("volume ranges overlap")
        covered.extend(span)
        label = str(volume.get("label") or f"volume-{index}")
        stage_key = str(volume.get("stage_key") or f"volume:{start}-{end}")
        ranges.append(
            {
                "stage_key": stage_key,
                "node_kind": "volume",
                "chapter_start": start,
                "chapter_end": end,
                "chapter_numbers": span,
                "label": label,
            }
        )
    if sorted(covered) != chapters:
        raise BoundaryPlanError("explicit volumes must exactly cover eligible chapters")
    ranges.sort(key=lambda item: (item["chapter_start"], item["chapter_end"]))
    # continuity already implied by exact cover of continuous chapters
    return ranges


def _fallback_windows(chapters: list[int], *, window_size: int) -> list[dict[str, Any]]:
    ranges: list[dict[str, Any]] = []
    for offset in range(0, len(chapters), window_size):
        span = chapters[offset : offset + window_size]
        start, end = span[0], span[-1]
        ranges.append(
            {
                "stage_key": f"story_arc:{start}-{end}",
                "node_kind": "story_arc",
                "chapter_start": start,
                "chapter_end": end,
                "chapter_numbers": list(span),
                "label": f"arc-{start}-{end}",
            }
        )
    return ranges


# ---------------------------------------------------------------------------
# Phase 28-03: evidence-backed outline candidates (D-01/D-05/D-07/D-09).
# Only terminal Chapter State may feed these artifacts. The output is always
# immutable candidate-only and retains source lineage at every level.
# ---------------------------------------------------------------------------

OUTLINE_SCHEMA_VERSION = "outline-candidate-artifact.v1"

# Boundary uncertainty reasons. Each reason carries an implicit uncertainty
# rank; boundaries adjacent to a gap or decided without evidence are uncertain
# by design so they stay visible to auditors instead of being silently fused.
BOUNDARY_REASON_SNAPSHOT_EDGE = "snapshot_edge"
BOUNDARY_REASON_EVIDENCE_DELTA = "evidence_delta"
BOUNDARY_REASON_WEAK_EVIDENCE = "weak_evidence"
BOUNDARY_REASON_ADJACENT_GAP = "adjacent_gap"
BOUNDARY_REASON_EXPLICIT_VOLUME = "explicit_volume"
BOUNDARY_REASON_OVERLAP = "overlap"
BOUNDARY_REASON_MISSING_EVIDENCE = "missing_evidence"

UNCERTAINTY_RANK = {
    Uncertainty.CERTAIN: 0,
    Uncertainty.LIKELY: 1,
    Uncertainty.UNCERTAIN: 2,
    Uncertainty.UNKNOWN: 3,
}
UNCERTAINTY_BY_RANK = {
    rank: level for level, rank in UNCERTAINTY_RANK.items()
}
BOUNDARY_UNCERTAINTY_RANK = {
    BOUNDARY_REASON_SNAPSHOT_EDGE: 1,
    BOUNDARY_REASON_EVIDENCE_DELTA: 1,
    BOUNDARY_REASON_EXPLICIT_VOLUME: 1,
    BOUNDARY_REASON_WEAK_EVIDENCE: 2,
    BOUNDARY_REASON_ADJACENT_GAP: 2,
    BOUNDARY_REASON_OVERLAP: 2,
    BOUNDARY_REASON_MISSING_EVIDENCE: 3,
}

# Signal strength at or above which an evidence delta is treated as an arc
# boundary. Below the threshold the split falls back to the window policy.
EVIDENCE_BOUNDARY_THRESHOLD = 0.5


class EvidenceSummary(BuilderFrozenModel):
    """Compressed per-chapter evidence signal used to score arc boundaries.

    The signal is a deterministic projection of the chapter's own authority:
    claim density, mean confidence and worst uncertainty. It is *evidence
    lineage* only — never an EvidenceRef and never reader-chat derived (D-06).
    """

    chapter_number: PositiveInt
    claim_count: Annotated[StrictInt, Field(ge=0)] = 0
    mean_confidence: Annotated[StrictFloat, Field(ge=0.0, le=1.0)] = 0.0
    max_uncertainty: Uncertainty = Uncertainty.UNCERTAIN
    content_hash: Hash64 | None = None
    source_leaf_hashes: tuple[Hash64, ...] = ()


class ChapterTerminalState(BuilderFrozenModel):
    """One durable terminal Chapter State (D-02: no silent pending).

    Only ``terminal_state == COMPLETED`` may feed arc candidates; isolated and
    blocked chapters are preserved as explicit gaps and are never disguised as
    complete facts.
    """

    chapter_number: PositiveInt
    terminal_state: TerminalState
    reason_code: VersionLabel | None = None
    source_snapshot_hash: Hash64
    input_hash: Hash64 | None = None


class BoundaryUncertainty(BuilderFrozenModel):
    """An explicit uncertainty annotation on one arc boundary (D-05)."""

    side: Literal["start", "end"]
    chapter_number: PositiveInt
    reason: VersionLabel
    detail: Annotated[StrictStr, StringConstraints(max_length=300)] | None = None


class GapRange(BuilderFrozenModel):
    """A maximal range of chapters that are not completed facts.

    Every chapter inside a gap is isolated or blocked (or absent) and carries
    its terminal reason codes so the gap stays auditable.
    """

    chapter_start: PositiveInt
    chapter_end: PositiveInt
    reason_codes: tuple[VersionLabel, ...] = ()
    terminal_states: tuple[VersionLabel, ...] = ()


class OverlapRange(BuilderFrozenModel):
    """An explicit overlap between candidate ranges.

    Generated plans never produce overlaps (arcs are constructed disjoint);
    this field exists so that any externally supplied overlap is preserved
    verbatim instead of being silently normalised away.
    """

    chapter_start: PositiveInt
    chapter_end: PositiveInt
    arc_keys: tuple[Key, ...] = ()


class ArcCandidateRange(BuilderFrozenModel):
    """One continuous arc candidate over a run of terminal chapters."""

    stage_key: Key
    node_kind: Literal["story_arc"] = "story_arc"
    chapter_start: PositiveInt
    chapter_end: PositiveInt
    chapter_numbers: tuple[PositiveInt, ...]
    coverage: Literal["complete", "partial", "empty"]
    uncertainty: Uncertainty
    confidence: Annotated[StrictFloat, Field(ge=0.0, le=1.0)]
    boundary_uncertainties: tuple[BoundaryUncertainty, ...] = ()
    evidence_lineage: dict[str, Any] = Field(default_factory=dict)
    input_hash: Hash64

    @model_validator(mode="after")
    def validate_range(self) -> "ArcCandidateRange":
        if len(self.chapter_numbers) != len(set(self.chapter_numbers)):
            raise ValueError("arc chapter_numbers must be unique")
        if self.chapter_numbers != tuple(
            range(self.chapter_start, self.chapter_end + 1)
        ):
            raise ValueError("arc chapter_numbers must be a continuous span")
        return self


class OutlineCandidateArtifact(BuilderFrozenModel):
    """Immutable, candidate-only outline over terminal Chapter State (D-07/D-09).

    Carries source snapshot, chapter/range lineage, input hash, per-arc evidence
    lineage, boundary uncertainty and an explicit ``candidate_status``. It is
    never promoted to Canon by generation alone.
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
    arcs: tuple[ArcCandidateRange, ...]
    covered_ranges: tuple[tuple[PositiveInt, PositiveInt], ...]
    gaps: tuple[GapRange, ...] = ()
    overlaps: tuple[OverlapRange, ...] = ()
    candidate_status: Literal["candidate"] = "candidate"
    lineage: dict[str, Any] = Field(default_factory=dict)
    checksum: Hash64

    @model_validator(mode="after")
    def validate_coverage(self) -> "OutlineCandidateArtifact":
        ordered = list(self.arcs)
        if ordered != sorted(ordered, key=lambda arc: (arc.chapter_start, arc.chapter_end)):
            raise ValueError("outline arcs must be ordered")
        seen: set[int] = set()
        for arc in ordered:
            if arc.chapter_start < self.chapter_min or arc.chapter_end > self.chapter_max:
                raise ValueError("arc range must be inside the snapshot range")
            for chapter in arc.chapter_numbers:
                if chapter in seen:
                    raise ValueError("outline arcs must not overlap")
                seen.add(chapter)
        expected = _covered_ranges_from_arcs(ordered)
        if expected != self.covered_ranges:
            raise ValueError("covered_ranges must match the arc union")
        return self


def terminal_chapters_input_hash(
    chapters: Sequence[ChapterTerminalState],
) -> str:
    """Deterministic input hash over the terminal chapter states (lineage)."""
    body = [
        {
            "chapter_number": chapter.chapter_number,
            "terminal_state": chapter.terminal_state.value,
            "reason_code": chapter.reason_code,
            "source_snapshot_hash": chapter.source_snapshot_hash,
            "input_hash": chapter.input_hash,
        }
        for chapter in sorted(chapters, key=lambda chapter: chapter.chapter_number)
    ]
    return sha256(_stable_json(body).encode("utf-8")).hexdigest()


def _covered_ranges_from_arcs(
    arcs: Sequence[ArcCandidateRange],
) -> tuple[tuple[int, int], ...]:
    numbers = sorted({int(n) for arc in arcs for n in arc.chapter_numbers})
    ranges: list[tuple[int, int]] = []
    for number in numbers:
        if ranges and number == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], number)
        else:
            ranges.append((number, number))
    return tuple(ranges)


def _uncertainty_rank(level: Uncertainty) -> int:
    return UNCERTAINTY_RANK.get(level, 2)


def _arc_uncertainty(
    evidence_levels: Sequence[Uncertainty],
    boundary_reasons: Sequence[str],
) -> Uncertainty:
    rank = max((_uncertainty_rank(level) for level in evidence_levels), default=0)
    for reason in boundary_reasons:
        rank = max(rank, BOUNDARY_UNCERTAINTY_RANK.get(str(reason), 2))
    return UNCERTAINTY_BY_RANK[rank]


def _boundary_signal(
    left: EvidenceSummary | None,
    right: EvidenceSummary | None,
) -> tuple[float, str | None]:
    """Score the strength of an arc boundary between adjacent chapters.

    Returns ``(signal, reason)``. A signal at or above
    ``EVIDENCE_BOUNDARY_THRESHOLD`` with reason ``evidence_delta`` marks a
    boundary supported by evidence. Missing evidence on either side is itself
    an uncertain signal that forces a split under the window policy.
    """
    if left is None or right is None:
        return 1.0, BOUNDARY_REASON_MISSING_EVIDENCE
    denominator = max(left.claim_count, right.claim_count, 1)
    density_delta = abs(left.claim_count - right.claim_count) / denominator
    uncertainty_delta = abs(
        _uncertainty_rank(right.max_uncertainty) - _uncertainty_rank(left.max_uncertainty)
    )
    confidence_delta = abs(left.mean_confidence - right.mean_confidence)
    signal = (
        0.6 * density_delta
        + 0.2 * min(1.0, float(uncertainty_delta))
        + 0.2 * min(1.0, confidence_delta)
    )
    if signal >= EVIDENCE_BOUNDARY_THRESHOLD:
        return signal, BOUNDARY_REASON_EVIDENCE_DELTA
    return signal, None


def _split_run(
    run: list[int],
    evidence: dict[int, EvidenceSummary],
    window_size: int,
) -> list[list[int]]:
    """Split one contiguous completed run into arc groups.

    Evidence-backed boundaries win when present; otherwise the window policy
    enforces the maximum arc span so coverage always stays continuous.
    """
    split_after: set[int] = set()
    for left, right in zip(run, run[1:]):
        if right != left + 1:
            continue
        signal, reason = _boundary_signal(
            evidence.get(left), evidence.get(right)
        )
        if (
            reason == BOUNDARY_REASON_EVIDENCE_DELTA
            and signal >= EVIDENCE_BOUNDARY_THRESHOLD
        ):
            split_after.add(left)
    groups: list[list[int]] = []
    current: list[int] = []
    for index, number in enumerate(run):
        if current and (run[index - 1] in split_after or len(current) >= window_size):
            groups.append(current)
            current = []
        current.append(number)
    if current:
        groups.append(current)
    return groups


def _boundary_reason_at(
    *,
    chapter: int,
    neighbour: int,
    evidence: dict[int, EvidenceSummary],
    by_number: dict[int, ChapterTerminalState],
    chapter_min: int,
    chapter_max: int,
) -> str:
    """Resolve why an arc boundary sits where it does (evidence vs window)."""
    if neighbour < chapter_min or neighbour > chapter_max:
        return BOUNDARY_REASON_SNAPSHOT_EDGE
    neighbour_state = by_number.get(neighbour)
    if neighbour_state is None or neighbour_state.terminal_state != TerminalState.COMPLETED:
        return BOUNDARY_REASON_ADJACENT_GAP
    left, right = evidence.get(min(chapter, neighbour)), evidence.get(max(chapter, neighbour))
    if left is None or right is None:
        return BOUNDARY_REASON_WEAK_EVIDENCE
    _signal, reason = _boundary_signal(left, right)
    return BOUNDARY_REASON_EVIDENCE_DELTA if reason else BOUNDARY_REASON_WEAK_EVIDENCE


def _compute_gaps(
    by_number: dict[int, ChapterTerminalState],
    chapter_min: int,
    chapter_max: int,
) -> tuple[GapRange, ...]:
    gaps: list[GapRange] = []
    current: list[int | list[str]] | None = None
    for number in range(chapter_min, chapter_max + 1):
        chapter = by_number.get(number)
        if chapter is None or chapter.terminal_state != TerminalState.COMPLETED:
            if current is None:
                current = [number, number, [], []]
            else:
                current[1] = number
            if chapter is None:
                current[2].append("chapter_absent")
                current[3].append("absent")
            else:
                current[2].append(
                    str(chapter.reason_code) if chapter.reason_code else "non_completed"
                )
                current[3].append(chapter.terminal_state.value)
            continue
        if current is not None:
            gaps.append(
                GapRange(
                    chapter_start=int(current[0]),
                    chapter_end=int(current[1]),
                    reason_codes=tuple(current[2]),  # type: ignore[arg-type]
                    terminal_states=tuple(current[3]),  # type: ignore[arg-type]
                )
            )
            current = None
    if current is not None:
        gaps.append(
            GapRange(
                chapter_start=int(current[0]),
                chapter_end=int(current[1]),
                reason_codes=tuple(current[2]),  # type: ignore[arg-type]
                terminal_states=tuple(current[3]),  # type: ignore[arg-type]
            )
        )
    return tuple(gaps)


def outline_candidate_checksum(artifact: OutlineCandidateArtifact) -> str:
    """Deterministic checksum over every field except ``checksum`` itself."""
    payload = artifact.model_dump(mode="json")
    payload.pop("checksum", None)
    return sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def validate_outline_candidate(artifact: OutlineCandidateArtifact) -> None:
    """Fail closed if an outline candidate loses its immutable integrity."""
    if artifact.candidate_status != "candidate":
        raise ValueError("outline candidate must stay candidate-only")
    if artifact.checksum != outline_candidate_checksum(artifact):
        raise ValueError("outline candidate checksum mismatch")
    if not artifact.lineage:
        raise ValueError("outline candidate must carry source lineage")


def plan_outline_arcs(
    *,
    chapters: Sequence[ChapterTerminalState],
    evidence_by_chapter: Mapping[int, EvidenceSummary] | None = None,
    policy_version: str = "arc-policy.v1",
    window_size: int = 3,
    owner_id: int = 1,
    novel_id: int = 1,
    version_id: int = 1,
    hierarchy_build_id: str = "hierarchy:unknown",
    hierarchy_checksum: str = "0" * 64,
) -> OutlineCandidateArtifact:
    """Build the immutable outline candidate over terminal Chapter State.

    Only ``COMPLETED`` chapters feed arcs. Isolated/blocked/absent chapters are
    collected into explicit gaps and never disguised as complete facts. Arc
    boundaries are evidence-backed where evidence exists and fall back to the
    window policy otherwise; every boundary keeps an explicit uncertainty
    annotation and full lineage (D-05/D-07/D-09).
    """
    if not chapters:
        raise BoundaryPlanError("chapters must be non-empty")
    if window_size < 1:
        raise BoundaryPlanError("window_size must be positive")

    evidence = dict(evidence_by_chapter or {})
    by_number = {int(chapter.chapter_number): chapter for chapter in chapters}
    if len(by_number) != len(chapters):
        raise BoundaryPlanError("duplicate chapter_number in terminal states")
    chapter_min = min(by_number)
    chapter_max = max(by_number)
    snapshots = {chapter.source_snapshot_hash for chapter in chapters}
    if len(snapshots) != 1:
        raise BoundaryPlanError("terminal chapters must share one source snapshot")
    source_snapshot_hash = snapshots.pop()

    completed = sorted(
        number
        for number, chapter in by_number.items()
        if chapter.terminal_state == TerminalState.COMPLETED
    )
    runs: list[list[int]] = []
    for number in completed:
        if runs and number == runs[-1][-1] + 1:
            runs[-1].append(number)
        else:
            runs.append([number])

    arc_groups: list[list[int]] = []
    for run in runs:
        arc_groups.extend(_split_run(run, evidence, window_size))

    arcs: list[ArcCandidateRange] = []
    for group in arc_groups:
        start, end = group[0], group[-1]
        boundaries: list[BoundaryUncertainty] = []
        if start == chapter_min:
            boundaries.append(
                BoundaryUncertainty(
                    side="start", chapter_number=start, reason=BOUNDARY_REASON_SNAPSHOT_EDGE
                )
            )
        else:
            boundaries.append(
                BoundaryUncertainty(
                    side="start",
                    chapter_number=start,
                    reason=_boundary_reason_at(
                        chapter=start,
                        neighbour=start - 1,
                        evidence=evidence,
                        by_number=by_number,
                        chapter_min=chapter_min,
                        chapter_max=chapter_max,
                    ),
                )
            )
        if end == chapter_max:
            boundaries.append(
                BoundaryUncertainty(
                    side="end", chapter_number=end, reason=BOUNDARY_REASON_SNAPSHOT_EDGE
                )
            )
        else:
            boundaries.append(
                BoundaryUncertainty(
                    side="end",
                    chapter_number=end,
                    reason=_boundary_reason_at(
                        chapter=end,
                        neighbour=end + 1,
                        evidence=evidence,
                        by_number=by_number,
                        chapter_min=chapter_min,
                        chapter_max=chapter_max,
                    ),
                )
            )

        evidence_levels = [
            evidence[number].max_uncertainty for number in group if number in evidence
        ]
        confidences = [
            evidence[number].mean_confidence for number in group if number in evidence
        ]
        uncertainty = _arc_uncertainty(
            evidence_levels, [boundary.reason for boundary in boundaries]
        )
        confidence = (
            round(sum(confidences) / len(confidences), 4) if confidences else 0.0
        )
        coverage = (
            "complete"
            if all(number in completed for number in range(start, end + 1))
            else "partial"
        )
        arcs.append(
            ArcCandidateRange(
                stage_key=f"story_arc:{start}-{end}",
                chapter_start=start,
                chapter_end=end,
                chapter_numbers=tuple(group),
                coverage=coverage,
                uncertainty=uncertainty,
                confidence=confidence,
                boundary_uncertainties=tuple(boundaries),
                evidence_lineage={
                    str(number): {
                        "claim_count": evidence[number].claim_count,
                        "max_uncertainty": evidence[number].max_uncertainty.value,
                        "mean_confidence": evidence[number].mean_confidence,
                        "content_hash": evidence[number].content_hash,
                    }
                    for number in group
                    if number in evidence
                },
                input_hash=terminal_chapters_input_hash(
                    [by_number[number] for number in group]
                ),
            )
        )

    gaps = _compute_gaps(by_number, chapter_min, chapter_max)
    covered_ranges = _covered_ranges_from_arcs(arcs)
    input_hash = terminal_chapters_input_hash(chapters)
    lineage = {
        "source_snapshot_hash": source_snapshot_hash,
        "hierarchy_build_id": hierarchy_build_id,
        "hierarchy_checksum": hierarchy_checksum,
        "chapter_input_hashes": {
            str(chapter.chapter_number): chapter.input_hash
            for chapter in chapters
            if chapter.input_hash
        },
        "arc_input_hashes": {arc.stage_key: arc.input_hash for arc in arcs},
    }
    placeholder = OutlineCandidateArtifact(
        schema_version=OUTLINE_SCHEMA_VERSION,
        policy_version=policy_version,
        owner_id=owner_id,
        novel_id=novel_id,
        version_id=version_id,
        source_snapshot_hash=source_snapshot_hash,
        hierarchy_build_id=hierarchy_build_id,
        hierarchy_checksum=hierarchy_checksum,
        input_hash=input_hash,
        chapter_min=chapter_min,
        chapter_max=chapter_max,
        arcs=tuple(arcs),
        covered_ranges=covered_ranges,
        gaps=gaps,
        overlaps=(),
        candidate_status="candidate",
        lineage=lineage,
        checksum="0" * 64,
    )
    return placeholder.model_copy(
        update={"checksum": outline_candidate_checksum(placeholder)}
    )
