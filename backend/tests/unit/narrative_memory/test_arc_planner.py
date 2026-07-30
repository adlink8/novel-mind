"""Unit tests for deterministic arc/volume boundary planning."""

from __future__ import annotations

import pytest

from app.services.narrative_memory.arc_planner import (
    BoundaryPlanError,
    blocked_closure_for_chapter,
    boundary_plan_checksum,
    plan_arc_boundaries,
)


pytestmark = pytest.mark.unit


def test_fallback_windows_exact_cover_stable() -> None:
    plan = plan_arc_boundaries(
        chapter_numbers=[3, 1, 2, 4, 5],
        window_size=2,
        policy_version="arc-policy.v1",
    )
    assert plan["source_kind"] == "deterministic_arc"
    assert plan["ranges"][0]["chapter_numbers"] == [1, 2]
    assert plan["ranges"][1]["chapter_numbers"] == [3, 4]
    assert plan["ranges"][2]["chapter_numbers"] == [5]
    assert plan["checksum"] == boundary_plan_checksum(plan)
    again = plan_arc_boundaries(
        chapter_numbers=[5, 4, 3, 2, 1], window_size=2, policy_version="arc-policy.v1"
    )
    assert again["checksum"] == plan["checksum"]


def test_explicit_volumes_preferred_when_legal() -> None:
    plan = plan_arc_boundaries(
        chapter_numbers=[1, 2, 3, 4],
        window_size=2,
        explicit_volumes=[
            {
                "chapter_start": 1,
                "chapter_end": 2,
                "label": "卷一",
                "stage_key": "volume:1-2",
            },
            {
                "chapter_start": 3,
                "chapter_end": 4,
                "label": "卷二",
                "stage_key": "volume:3-4",
            },
        ],
    )
    assert plan["source_kind"] == "explicit_volume"
    assert plan["ranges"][0]["node_kind"] == "volume"
    assert plan["ranges"][0]["label"] == "卷一"


def test_llm_story_arcs_define_variable_size_ranges() -> None:
    plan = plan_arc_boundaries(
        chapter_numbers=[1, 2, 3, 4, 5],
        window_size=3,
        llm_ranges=[
            {
                "chapter_start": 1,
                "chapter_end": 2,
                "label": "流亡与结盟",
                "reason": "主角离开故乡并建立新的目标关系",
            },
            {
                "chapter_start": 3,
                "chapter_end": 5,
                "label": "反击准备",
                "reason": "冲突升级并完成反击前的铺垫",
            },
        ],
    )
    assert plan["source_kind"] == "llm_story_arc"
    assert [item["chapter_numbers"] for item in plan["ranges"]] == [[1, 2], [3, 4, 5]]
    assert plan["ranges"][1]["stage_key"] == "story_arc:3-5"


def test_llm_story_arcs_must_cover_every_chapter_once() -> None:
    with pytest.raises(BoundaryPlanError):
        plan_arc_boundaries(
            chapter_numbers=[1, 2, 3],
            llm_ranges=[{"chapter_start": 1, "chapter_end": 2, "reason": "未覆盖第3章"}],
        )


def test_invalid_boundaries_fail() -> None:
    with pytest.raises(BoundaryPlanError):
        plan_arc_boundaries(chapter_numbers=[1, 3])
    with pytest.raises(BoundaryPlanError):
        plan_arc_boundaries(
            chapter_numbers=[1, 2, 3],
            explicit_volumes=[{"chapter_start": 1, "chapter_end": 2}],
        )
    with pytest.raises(BoundaryPlanError):
        plan_arc_boundaries(
            chapter_numbers=[1, 2],
            explicit_volumes=[
                {"chapter_start": 1, "chapter_end": 2},
                {"chapter_start": 2, "chapter_end": 2},
            ],
        )


def test_checksum_sensitive_and_blocked_closure() -> None:
    a = plan_arc_boundaries(chapter_numbers=[1, 2, 3], window_size=2)
    b = plan_arc_boundaries(chapter_numbers=[1, 2, 3], window_size=3)
    assert a["checksum"] != b["checksum"]
    closure = blocked_closure_for_chapter(a, chapter_number=2)
    assert closure[0].startswith("story_arc:")
    assert closure[-1] == "global_story:book"
