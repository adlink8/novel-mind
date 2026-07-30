"""Narrative-memory prompt and aggregation input contracts."""

from types import SimpleNamespace

import pytest

from app.services.narrative_memory.builder_worker import (
    _estimated_input_tokens,
    _node_content,
)
from scripts.run_narrative_memory_build import (
    _ARC_PLAN_PROMPT,
    _PROMPT_TEXT,
    _build_messages,
    _response_schema_for_stage,
)

pytestmark = pytest.mark.unit


def test_chapter_state_prompt_requires_substantive_structured_summary() -> None:
    assert "summary" in _PROMPT_TEXT
    assert "key_elements" in _PROMPT_TEXT
    assert "narrative_progress" in _PROMPT_TEXT

    messages = _build_messages(
        stage_key="chapter_state:3",
        payload={
            "chapter_number": 3,
            "evidence_leaves": [{"evidence_node_id": "leaf-3"}],
        },
        repair=False,
    )
    assert "不能只复述章节标题" in messages[0]["content"]
    assert '"summary"' in messages[1]["content"]
    assert '"narrative_progress"' in messages[1]["content"]

    schema = _response_schema_for_stage("chapter_state:3")
    assert set(schema["required"]) >= {
        "summary",
        "key_elements",
        "narrative_progress",
    }


def test_parent_aggregation_payload_contains_claim_content() -> None:
    node = SimpleNamespace(
        id=11,
        node_key="chapter_state:1",
        chapter_start=1,
        chapter_end=1,
        display_label="第1章：主角流亡",
    )
    claim = SimpleNamespace(
        node_id=11,
        claim_key="chapter_state:1:claim:1",
        claim_kind="event_fact",
        typed_payload={"claim_kind": "event_fact", "outcome": "主角流亡"},
        uncertainty="certain",
        confidence=0.9,
        visible_from_chapter=1,
    )
    content = _node_content([node], [claim])
    assert content[0]["claims"][0]["content"]["outcome"] == "主角流亡"


def test_arc_plan_prompt_uses_story_content_instead_of_fixed_windows() -> None:
    assert "不要按固定的 3 章" in _ARC_PLAN_PROMPT
    messages = _build_messages(
        stage_key="arc_volume_plan:book",
        payload={
            "chapter_numbers": [1, 2, 3],
            "chapter_content": [
                {"chapter_number": 1, "claims": [{"content": {"summary": "故乡失守"}}]}
            ],
        },
        repair=False,
    )
    assert "chapter_content" in messages[1]["content"]
    schema = _response_schema_for_stage("arc_volume_plan:book")
    assert "ranges" in schema["required"]


def test_aggregation_budget_estimate_scales_with_claim_content() -> None:
    payload = {"child_content": [{"claims": [{"content": "长文本" * 5_000}]}]}
    assert _estimated_input_tokens(payload) > 8_000
