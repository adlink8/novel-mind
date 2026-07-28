import pytest

from app.api.timeline import router
from app.schemas.timeline import TimelineEnvelope, TimelineVersionSource


def test_timeline_router_exposes_durable_owner_scoped_contract():
    routes = {
        (route.path, method) for route in router.routes for method in route.methods
    }
    expected = {
        ("/{novel_id}/start-or-resume", "POST"),
        ("/{novel_id}/status", "GET"),
        ("/{novel_id}/cancel", "POST"),
        ("/{novel_id}/resume", "POST"),
        ("/{novel_id}/versions/{version_id}", "GET"),
        ("/{novel_id}/rollback", "POST"),
        ("/{novel_id}/events/{logical_event_id}", "PUT"),
        ("/{novel_id}/preference", "PUT"),
    }
    assert expected <= routes


def test_active_and_running_candidate_are_separate_envelopes():
    envelope = TimelineEnvelope(active=None, running_candidate=None)
    assert envelope.active is None
    assert envelope.running_candidate is None
    assert {source.value for source in TimelineVersionSource} == {
        "active",
        "running_candidate",
    }


def test_timeline_schema_does_not_merge_version_counts_or_progress():
    schema = TimelineEnvelope.model_json_schema()
    properties = schema["properties"]
    assert "active" in properties and "running_candidate" in properties
    assert "events" not in properties
    assert "counts" not in properties
    assert "progress" not in properties


pytestmark = pytest.mark.unit
