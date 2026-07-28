"""Unit tests for timeline structure chapter-range bounds (Phase 20)."""

import inspect

import pytest

from app.api import timeline as timeline_api
from app.services.timeline.query import build_version_view, effective_narrative_bounds

pytestmark = pytest.mark.unit


def test_effective_bounds_spoiler_only_when_no_range():
    hide, lower, upper = effective_narrative_bounds(
        spoiler_cutoff=4, spoiler_open=False
    )
    assert hide is False
    assert lower is None
    assert upper == 4


def test_effective_bounds_hide_all_when_closed_without_cutoff():
    hide, lower, upper = effective_narrative_bounds(
        spoiler_cutoff=None, spoiler_open=False
    )
    assert hide is True
    assert lower is None and upper is None


def test_effective_bounds_chapter_end_min_with_spoiler():
    # Structure end 10, spoiler at 3 → upper is 3
    _, lower, upper = effective_narrative_bounds(
        spoiler_cutoff=3, spoiler_open=False, chapter_start=1, chapter_end=10
    )
    assert lower == 1
    assert upper == 3

    # Structure end 2, spoiler at 5 → upper is 2
    _, lower, upper = effective_narrative_bounds(
        spoiler_cutoff=5, spoiler_open=False, chapter_start=2, chapter_end=2
    )
    assert lower == 2
    assert upper == 2


def test_effective_bounds_spoiler_open_uses_range_only():
    # full_book / running candidate: no spoiler upper, structure still applies
    hide, lower, upper = effective_narrative_bounds(
        spoiler_cutoff=None,
        spoiler_open=True,
        chapter_start=3,
        chapter_end=7,
    )
    assert hide is False
    assert lower == 3
    assert upper == 7


def test_effective_bounds_start_only_with_spoiler():
    hide, lower, upper = effective_narrative_bounds(
        spoiler_cutoff=8, spoiler_open=False, chapter_start=5
    )
    assert hide is False
    assert lower == 5
    assert upper == 8


def test_effective_bounds_end_only_when_spoiler_open():
    hide, lower, upper = effective_narrative_bounds(
        spoiler_cutoff=None, spoiler_open=True, chapter_end=4
    )
    assert hide is False
    assert lower is None
    assert upper == 4


def test_build_version_view_accepts_optional_chapter_range_params():
    sig = inspect.signature(build_version_view)
    assert "chapter_start" in sig.parameters
    assert "chapter_end" in sig.parameters
    assert sig.parameters["chapter_start"].default is None
    assert sig.parameters["chapter_end"].default is None


def test_timeline_get_endpoints_expose_optional_chapter_range_query():
    by_name = {route.name: route for route in timeline_api.router.routes}
    for name in ("get_timeline", "get_version"):
        route = by_name[name]
        dependant = route.dependant
        query_names = {q.name for q in dependant.query_params}
        assert "chapter_start" in query_names, name
        assert "chapter_end" in query_names, name
