from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.agent_runtime import SkillRunView

pytestmark = pytest.mark.unit


def _run(**overrides):
    values = {
        "id": 11,
        "owner_id": 7,
        "novel_id": 3,
        "skill_version_id": 42,
        "status": "completed",
        "input_hash": "a" * 64,
        "cancel_requested": False,
        "retry_count": 0,
        "created_at": "2026-08-13T00:00:00Z",
        "updated_at": "2026-08-13T00:01:00Z",
        "frozen_manifest": {
            "tool_runs": [
                {"tool_name": "get_chapter", "calls": 2, "errors": 1},
                {"tool_name": "get_events", "calls": 1, "errors": 0},
            ],
            "connector_versions": [{"checksum": "opaque"}],
        },
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_skill_run_view_projects_frozen_runtime_tool_runs():
    view = SkillRunView.model_validate(_run())

    assert view.frozen_manifest["tool_runs"] == [
        {"tool_name": "get_chapter", "calls": 2, "errors": 1},
        {"tool_name": "get_events", "calls": 1, "errors": 0},
    ]
    assert [item.model_dump() for item in view.tool_runs] == [
        {"tool_name": "get_chapter", "calls": 2, "errors": 1},
        {"tool_name": "get_events", "calls": 1, "errors": 0},
    ]
    assert "frozen_manifest" not in view.model_dump()


def test_skill_run_view_rejects_non_deterministic_tool_summary():
    with pytest.raises(ValidationError, match="errors.*calls|errors cannot"):
        SkillRunView.model_validate(
            _run(
                frozen_manifest={
                    "tool_runs": [
                        {"tool_name": "get_chapter", "calls": 1, "errors": 2}
                    ]
                }
            )
        )


def test_skill_run_view_rejects_duplicate_or_unsorted_tool_summary():
    with pytest.raises(ValidationError, match="sorted|unique"):
        SkillRunView.model_validate(
            _run(
                frozen_manifest={
                    "tool_runs": [
                        {"tool_name": "get_events", "calls": 1, "errors": 0},
                        {"tool_name": "get_chapter", "calls": 1, "errors": 0},
                    ]
                }
            )
        )
