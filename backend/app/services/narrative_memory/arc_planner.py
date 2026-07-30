"""Deterministic Volume/Arc boundary planner (no provider calls)."""

from __future__ import annotations

from hashlib import sha256
from typing import Any, Sequence

from app.services.narrative_memory.builder_contracts import _stable_json


class BoundaryPlanError(ValueError):
    pass


def plan_arc_boundaries(
    *,
    chapter_numbers: Sequence[int],
    window_size: int = 3,
    policy_version: str = "arc-policy.v1",
    explicit_volumes: Sequence[dict[str, Any]] | None = None,
    llm_ranges: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return a frozen full-cover continuous non-overlapping boundary plan.

    ``llm_ranges`` is the production path used by the NM builder. The legacy
    ``window_size`` fallback remains for deterministic tests and old callers.
    """

    chapters = sorted({int(n) for n in chapter_numbers})
    if not chapters:
        raise BoundaryPlanError("chapter_numbers must be non-empty")
    if chapters != list(range(chapters[0], chapters[-1] + 1)):
        raise BoundaryPlanError("eligible chapters must be continuous")
    if window_size < 1:
        raise BoundaryPlanError("window_size must be positive")

    if explicit_volumes and llm_ranges:
        raise BoundaryPlanError("explicit_volumes and llm_ranges are mutually exclusive")
    if llm_ranges:
        ranges = _validate_llm_ranges(chapters, llm_ranges)
        source_kind = "llm_story_arc"
    elif explicit_volumes:
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


def _validate_llm_ranges(
    chapters: list[int], proposed_ranges: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Validate LLM-selected story ranges without trusting its labels/keys."""
    ranges: list[dict[str, Any]] = []
    covered: list[int] = []
    for index, item in enumerate(proposed_ranges, start=1):
        try:
            start = int(item["chapter_start"])
            end = int(item["chapter_end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BoundaryPlanError("LLM range must contain integer chapter_start/chapter_end") from exc
        if start <= 0 or end < start:
            raise BoundaryPlanError("LLM range has invalid chapter bounds")
        span = list(range(start, end + 1))
        if any(n not in chapters for n in span):
            raise BoundaryPlanError("LLM range references a chapter outside the build")
        if set(span) & set(covered):
            raise BoundaryPlanError("LLM ranges overlap")
        covered.extend(span)
        ranges.append(
            {
                "stage_key": f"story_arc:{start}-{end}",
                "node_kind": "story_arc",
                "chapter_start": start,
                "chapter_end": end,
                "chapter_numbers": span,
                "label": str(item.get("label") or f"story-arc-{index}")[:240],
                "reason": str(item.get("reason") or "")[:500],
            }
        )
    if sorted(covered) != chapters:
        raise BoundaryPlanError("LLM ranges must exactly cover all eligible chapters")
    ranges.sort(key=lambda item: (item["chapter_start"], item["chapter_end"]))
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
