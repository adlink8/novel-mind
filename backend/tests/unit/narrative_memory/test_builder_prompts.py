"""Narrative-memory prompt and aggregation input contracts."""

from types import SimpleNamespace

import pytest

from app.services.narrative_memory.builder_repository import BuilderRepositoryError
from app.services.narrative_memory.builder_worker import (
    NarrativeMemoryBuilderWorker,
    _estimated_input_tokens,
    _is_retryable_lease_error,
    _is_retryable_provider_reason,
    _node_content,
)
from scripts.run_narrative_memory_build import (
    _ARC_PLAN_PROMPT,
    _PROMPT_TEXT,
    _build_messages,
    _normalize_model_output,
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


def test_chapter_state_normalizes_unknown_uncertainty_enum() -> None:
    normalized = _normalize_model_output(
        {
            "summary": "主角离开城门。",
            "key_elements": [],
            "narrative_progress": "主角开始逃亡。",
            "claims": [
                {
                    "claim_key": "chapter_state:3:claim:1",
                    "uncertainty": "none",
                    "payload": {
                        "claim_kind": "event_fact",
                        "event_kind": "action",
                        "actor_keys": ["character:main"],
                        "chapter_start": 3,
                        "chapter_end": 3,
                        "outcome": {"value_kind": "text", "value": "离开城门"},
                    },
                }
            ],
            "source_bindings": [
                {
                    "claim_key": "chapter_state:3:claim:1",
                    "evidence_node_id": "leaf-3",
                    "source_key": "source-3",
                }
            ],
        },
        payload={
            "chapter_number": 3,
            "evidence_leaves": [{"evidence_node_id": "leaf-3"}],
        },
        stage_key="chapter_state:3",
    )
    assert normalized["claims"][0]["uncertainty"] == "likely"


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


def test_lease_errors_are_limited_to_transient_claim_failures() -> None:
    assert _is_retryable_lease_error(BuilderRepositoryError("lease lost"))
    assert _is_retryable_lease_error(
        BuilderRepositoryError("run lease held by another worker")
    )
    assert not _is_retryable_lease_error(BuilderRepositoryError("run cancelled"))


def test_provider_quota_failures_pause_for_later_resume() -> None:
    assert _is_retryable_provider_reason(
        "VertexAPIError:Vertex HTTP 429: Resource exhausted"
    )
    assert _is_retryable_provider_reason("temporarily unavailable")
    assert not _is_retryable_provider_reason("PackageBuildError: invalid output")


@pytest.mark.asyncio
async def test_process_run_retries_lease_loss(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = object.__new__(NarrativeMemoryBuilderWorker)
    calls = 0

    async def fake_process_run_once(**_kwargs: object) -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise BuilderRepositoryError("lease lost")
        return "resumed"

    delays: list[int] = []

    async def fake_sleep(seconds: int) -> None:
        delays.append(seconds)

    monkeypatch.setattr(worker, "_process_run_once", fake_process_run_once)
    monkeypatch.setattr("app.services.narrative_memory.builder_worker.asyncio.sleep", fake_sleep)

    result = await worker.process_run(owner_id=2, novel_id=216, version_id=17)

    assert result == "resumed"
    assert calls == 3
    assert delays == [5, 15]
