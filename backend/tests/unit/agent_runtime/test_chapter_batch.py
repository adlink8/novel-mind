"""TDD contract tests for scalable chapter-batch orchestration."""

from __future__ import annotations

import pytest

from app.services.agent_runtime.chapter_batch import (
    ChapterRef,
    ChapterBatchError,
    build_chapter_batch_plan,
)


def _chapters(count: int) -> list[ChapterRef]:
    return [ChapterRef(id=1000 + number, chapter_number=number) for number in range(1, count + 1)]


@pytest.mark.unit
def test_ten_chapters_are_planned_as_individual_runs_with_bounded_window():
    plan = build_chapter_batch_plan(
        owner_id=7,
        novel_id=11,
        cutoff_chapter=10,
        chapters=_chapters(10),
        concurrency_window=3,
    )

    assert plan.total == 10
    assert [item.chapter_id for item in plan.next_window] == [1001, 1002, 1003]
    assert all(item.input["novel_id"] == 11 for item in plan.next_window)
    assert all(item.input["chapter_id"] > 1000 for item in plan.next_window)
    assert all(item.input["question"] for item in plan.next_window)
    assert all("chapter_ids" not in item.input for item in plan.next_window)


@pytest.mark.unit
def test_one_hundred_chapters_keep_model_input_per_chapter_and_resume_cursor():
    plan = build_chapter_batch_plan(
        owner_id=7,
        novel_id=11,
        cutoff_chapter=100,
        chapters=_chapters(100),
        concurrency_window=5,
    )

    assert plan.total == 100
    assert len(plan.next_window) == 5
    assert plan.pending_chapter_ids == [1006 + offset for offset in range(95)]
    assert max(len(str(item.input)) for item in plan.next_window) < 500
    assert all(item.input["execution_prompt"] == item.input["question"] for item in plan.next_window)


@pytest.mark.unit
def test_four_hundred_chapters_remain_bounded_for_large_boundary():
    plan = build_chapter_batch_plan(
        owner_id=7,
        novel_id=11,
        cutoff_chapter=400,
        chapters=_chapters(400),
        concurrency_window=8,
    )

    assert (plan.total, len(plan.next_window), len(plan.pending_chapter_ids)) == (
        400,
        8,
        392,
    )
    assert all("chapter_ids" not in item.input for item in plan.next_window)


@pytest.mark.unit
def test_plan_fails_closed_when_requested_chapters_exceed_server_cutoff():
    with pytest.raises(ChapterBatchError, match="cutoff"):
        build_chapter_batch_plan(
            owner_id=7,
            novel_id=11,
            cutoff_chapter=9,
            chapters=_chapters(10),
            concurrency_window=3,
        )


@pytest.mark.unit
def test_window_must_be_positive_and_bounded():
    with pytest.raises(ChapterBatchError, match="concurrency_window"):
        build_chapter_batch_plan(
            owner_id=7,
            novel_id=11,
            cutoff_chapter=10,
            chapters=_chapters(10),
            concurrency_window=0,
        )
