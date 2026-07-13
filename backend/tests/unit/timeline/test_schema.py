"""Strict chapter timeline extraction schema contracts."""

import pytest
from pydantic import ValidationError

from app.schemas.timeline import EventCandidate, StoryTimeConstraint, TimelineExtraction

pytestmark = pytest.mark.unit


def _event(**overrides):
    payload = {
        "candidate_id": "chapter-7:event-1",
        "title": "The envoy arrives",
        "description": "An envoy enters the eastern gate.",
        "event_type": "plot",
        "narrative_chapter_number": 7,
        "narrative_index": 0,
        "participants": [{"mention": "the envoy", "entity_id": None}],
        "story_time": {"precision": "unknown", "expression": None},
        "evidence": [{
            "chapter_id": 7,
            "evidence_id": "ev-1",
            "source_start": 12,
            "source_end": 32,
            "content_hash": "a" * 64,
        }],
        "confidence": 0.91,
    }
    payload.update(overrides)
    return payload


def test_narrative_position_and_story_time_are_distinct():
    event = EventCandidate.model_validate(_event(story_time={
        "precision": "relative",
        "expression": "the next morning",
        "anchor_event_id": "chapter-6:event-3",
        "relation": "after",
    }))
    assert (event.narrative_chapter_number, event.narrative_index) == (7, 0)
    assert event.story_time.precision == "relative"


@pytest.mark.parametrize("story_time", [
    {"precision": "exact", "expression": "on the third day"},
    {"precision": "relative", "expression": "later", "relation": "after"},
    {"precision": "fuzzy", "expression": "in childhood", "exact_time": "2020-01-01T00:00:00Z"},
    {"precision": "unknown", "expression": None, "anchor_event_id": "invented"},
])
def test_time_smuggling_and_incomplete_constraints_are_rejected(story_time):
    with pytest.raises(ValidationError):
        EventCandidate.model_validate(_event(story_time=story_time))


def test_exact_time_requires_explicit_expression_and_evidence():
    event = EventCandidate.model_validate(_event(story_time={
        "precision": "exact",
        "expression": "12 March 1842",
        "exact_time": "1842-03-12T00:00:00Z",
    }))
    assert event.story_time.exact_time.year == 1842
    with pytest.raises(ValidationError):
        EventCandidate.model_validate(_event(evidence=[]))


def test_offsets_and_extra_fields_are_rejected():
    malformed = _event()
    malformed["evidence"][0]["source_end"] = malformed["evidence"][0]["source_start"]
    with pytest.raises(ValidationError):
        EventCandidate.model_validate(malformed)
    with pytest.raises(ValidationError):
        TimelineExtraction.model_validate({"events": [_event()], "write_to_database": True})


def test_constraint_is_strict_and_directional():
    constraint = StoryTimeConstraint.model_validate({
        "source_candidate_id": "chapter-7:event-1",
        "target_candidate_id": "chapter-7:event-2",
        "relation": "before",
        "evidence_ids": ["ev-1"],
    })
    assert constraint.relation == "before"
    with pytest.raises(ValidationError):
        StoryTimeConstraint.model_validate({
            "source_candidate_id": "a", "target_candidate_id": "a",
            "relation": "before", "evidence_ids": ["ev-1"],
        })
