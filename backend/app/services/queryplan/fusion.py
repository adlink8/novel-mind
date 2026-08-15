"""Deterministic, provenance-preserving fusion of QueryPlan dimension results.

Phase 26-02 / REQ-QP-05 (D-04, D-05, D-06, D-08, D-09, D-12, D-14).

- **Deterministic**: identical ``(source, dimension_results, through_chapter,
  cutoff_mode, budget)`` inputs always produce identical output and ``checksum``.
- **Single-source comparable**: fusing one dimension reproduces exactly that
  dimension's verified refs; every fused ref carries its contributing
  dimensions, provenance and fallback stage, so a multi-dimension result can be
  diffed against any single-source baseline.
- **Provenance-preserving**: owner/novel/version/cutoff are re-verified against
  the source snapshot and every ref's provenance chain is preserved.
- **Missing dimensions never become empty-success or uncited facts**: an
  all-unavailable input fuses to ``unavailable`` with zero evidence, never an
  empty ``available``; heuristic candidates are recall-only and can never enter
  ``fused_evidence``.

The only evidence that may be fused is an exact-reader ``EvidenceRef`` (a leaf
chapter + Unicode offsets + content hash). Summaries, scores, routing metadata
and chat text are structurally absent from this boundary (D-08).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from app.services.queryplan.adapters import (
    DimensionResult,
    HeuristicCandidate,
    SourceSnapshot,
)
from app.services.queryplan.schemas import (
    AvailabilityStatus,
    CutoffMode,
    EvidenceRef,
    QueryDimension,
    canonical_json,
    sha256_hex,
)

FUSION_HASH_COMPONENT = "queryplan:fusion:v1"

_EVIDENCE_FIELDS = (
    "chapter_id",
    "chapter_number",
    "source_start",
    "source_end",
    "content_hash",
    "source_snapshot_hash",
)


class FusionReasonCode(StrEnum):
    """Stable overall reason codes for a fused result (D-05)."""

    ALL_AVAILABLE = "all_dimensions_available"
    PARTIAL_COVERAGE = "partial_coverage"
    ALL_UNAVAILABLE = "all_dimensions_unavailable"


class FusionError(ValueError):
    """Fail-closed fusion error: empty dimension set or scope/snapshot escape."""


@dataclass(frozen=True)
class FusedEvidence:
    """One deduplicated evidence ref plus its provenance chain."""

    ref: EvidenceRef
    dimensions: tuple[QueryDimension, ...]
    provenance: tuple[str, ...]
    stages: tuple[str, ...]


@dataclass(frozen=True)
class FusionResult:
    owner_id: int
    novel_id: int
    version_id: int
    through_chapter: int
    cutoff_mode: str
    dimension_results: tuple[DimensionResult, ...]
    fused_evidence: tuple[FusedEvidence, ...]
    candidate_recall: tuple[HeuristicCandidate, ...]
    evidence_count: int
    candidate_count: int
    status: AvailabilityStatus
    reason: str
    provenance_chain: tuple[str, ...]
    checksum: str
    budget: int | None
    exceeded_budget: bool


# ---------------------------------------------------------------------------
# Serialization (canonical, used by the checksum only)
# ---------------------------------------------------------------------------


def _serialize_ref(ref: EvidenceRef) -> dict:
    return {
        "chapter_id": ref.chapter_id,
        "chapter_number": ref.chapter_number,
        "source_start": ref.source_start,
        "source_end": ref.source_end,
        "content_hash": ref.content_hash,
        "source_snapshot_hash": ref.source_snapshot_hash,
    }


def _serialize_candidate(candidate: HeuristicCandidate) -> dict:
    return {
        "dimension": candidate.dimension.value,
        "chapter_id": candidate.chapter_id,
        "chapter_number": candidate.chapter_number,
        "source_start": candidate.source_start,
        "source_end": candidate.source_end,
        "content_hash": candidate.content_hash,
        "source_snapshot_hash": candidate.source_snapshot_hash,
        "snippet": candidate.snippet,
        "evidence_eligible": candidate.evidence_eligible,
    }


def _serialize_dimension_result(result: DimensionResult) -> dict:
    return {
        "dimension": result.dimension.value,
        "status": result.status.value,
        "reason": result.reason,
        "provenance": result.provenance,
        "stage": result.stage.value,
        "refs": [_serialize_ref(ref) for ref in result.refs],
        "candidates": [_serialize_candidate(c) for c in result.candidates],
    }


def fusion_checksum(result: FusionResult) -> str:
    body = canonical_json(
        {
            "owner_id": result.owner_id,
            "novel_id": result.novel_id,
            "version_id": result.version_id,
            "through_chapter": result.through_chapter,
            "cutoff_mode": result.cutoff_mode,
            "budget": result.budget,
            "dimensions": [
                _serialize_dimension_result(r) for r in result.dimension_results
            ],
            "fused_evidence": [
                {
                    "ref": _serialize_ref(fe.ref),
                    "dimensions": [d.value for d in fe.dimensions],
                    "provenance": list(fe.provenance),
                    "stages": list(fe.stages),
                }
                for fe in result.fused_evidence
            ],
            "candidate_recall": [
                _serialize_candidate(c) for c in result.candidate_recall
            ],
            "status": result.status.value,
            "reason": result.reason,
            "provenance_chain": list(result.provenance_chain),
            "evidence_count": result.evidence_count,
            "exceeded_budget": result.exceeded_budget,
        }
    )
    return sha256_hex(FUSION_HASH_COMPONENT, body)


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------


def _evidence_key(ref: EvidenceRef) -> tuple[int, int, int, str]:
    return (ref.chapter_id, ref.source_start, ref.source_end, ref.content_hash)


def _evidence_sort_key(fused: FusedEvidence) -> tuple[int, int, int, int, str]:
    return (
        fused.ref.chapter_number,
        fused.ref.source_start,
        fused.ref.source_end,
        fused.ref.chapter_id,
        fused.ref.content_hash,
    )


def fuse_dimension_results(
    results: Sequence[DimensionResult],
    *,
    source: SourceSnapshot,
    through_chapter: int,
    cutoff_mode: str = CutoffMode.READING_PROGRESS.value,
    budget: int | None = None,
) -> FusionResult:
    """Fuse dimension results deterministically, preserving provenance.

    Raises ``FusionError`` on an empty dimension set or when any ref/candidate
    escapes the frozen source snapshot (owner/version/cutoff boundary, D-12/D-14).
    """
    if not results:
        raise FusionError("cannot fuse an empty dimension result set")
    if budget is not None and budget < 0:
        raise FusionError("budget must be >= 0")
    mode = CutoffMode(cutoff_mode)

    by_key: dict[tuple[int, int, int, str], dict] = {}
    candidates: list[HeuristicCandidate] = []

    for result in results:
        for ref in result.refs:
            if ref.source_snapshot_hash != source.snapshot_hash:
                raise FusionError(
                    "evidence ref escapes the source snapshot "
                    "(owner/version/cutoff boundary)"
                )
            if (
                mode == CutoffMode.READING_PROGRESS
                and ref.chapter_number > through_chapter
            ):
                continue
            key = _evidence_key(ref)
            entry = by_key.get(key)
            if entry is None:
                by_key[key] = {
                    "ref": ref,
                    "dimensions": {result.dimension},
                    "provenance": {result.provenance},
                    "stages": {result.stage.value},
                }
            else:
                entry["dimensions"].add(result.dimension)
                entry["provenance"].add(result.provenance)
                entry["stages"].add(result.stage.value)
        for candidate in result.candidates:
            if candidate.source_snapshot_hash != source.snapshot_hash:
                raise FusionError(
                    "heuristic candidate escapes the source snapshot "
                    "(owner/version/cutoff boundary)"
                )
            if (
                mode == CutoffMode.READING_PROGRESS
                and candidate.chapter_number > through_chapter
            ):
                continue
            candidates.append(candidate)

    fused: list[FusedEvidence] = []
    for entry in by_key.values():
        fused.append(
            FusedEvidence(
                ref=entry["ref"],
                dimensions=tuple(sorted(entry["dimensions"], key=lambda d: d.value)),
                provenance=tuple(sorted(entry["provenance"])),
                stages=tuple(sorted(entry["stages"])),
            )
        )
    fused.sort(key=_evidence_sort_key)

    candidates.sort(
        key=lambda c: (
            c.chapter_number,
            c.source_start,
            c.source_end,
            c.content_hash,
            c.dimension.value,
        )
    )
    candidate_recall = tuple(candidates)

    total_evidence = len(fused)
    exceeded = budget is not None and total_evidence > budget
    if exceeded and budget is not None:
        fused = fused[:budget]

    statuses = {result.status for result in results}
    if statuses == {AvailabilityStatus.AVAILABLE}:
        status = AvailabilityStatus.AVAILABLE
        reason = FusionReasonCode.ALL_AVAILABLE.value
    elif statuses == {AvailabilityStatus.UNAVAILABLE}:
        status = AvailabilityStatus.UNAVAILABLE
        reason = FusionReasonCode.ALL_UNAVAILABLE.value
    else:
        status = AvailabilityStatus.PARTIAL
        reason = FusionReasonCode.PARTIAL_COVERAGE.value

    result = FusionResult(
        owner_id=source.owner_id,
        novel_id=source.novel_id,
        version_id=source.version_id,
        through_chapter=through_chapter,
        cutoff_mode=mode.value,
        dimension_results=tuple(results),
        fused_evidence=tuple(fused),
        candidate_recall=candidate_recall,
        evidence_count=total_evidence,
        candidate_count=len(candidate_recall),
        status=status,
        reason=reason,
        provenance_chain=tuple(sorted({r.provenance for r in results})),
        checksum="",
        budget=budget,
        exceeded_budget=exceeded,
    )
    return replace(result, checksum=fusion_checksum(result))
