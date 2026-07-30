"""Reader-chat structured gateway: citations, repair, no tools, dual budgets."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.schemas.reader_chat import (
    ReaderAnswerEnvelope,
    validate_answer_against_manifest,
)
from app.services.reader_chat.budget import DualBudgetGate, BudgetPolicy
from app.services.reader_chat.gateway import (
    DependencyPaused,
    ModelDeployment,
    ReaderChatGateway,
    StructuredOutputRejected,
    business_validate_answer,
)

pytestmark = pytest.mark.unit

ALLOWED = {"selection:primary", "hierarchy:1"}


def _valid_answer(*, refs=None) -> str:
    refs = refs or ["selection:primary"]
    payload = {
        "schema_version": "reader-answer.v1",
        "answer_blocks": [
            {
                "block_id": "b1",
                "text": "阿宁走进竹林。",
                "evidence_refs": refs,
            }
        ],
        "clarifying_question": None,
        "uncertainty": None,
        "suggestion_candidates": [],
    }
    return json.dumps(payload, ensure_ascii=False)


def _uncertainty() -> str:
    return json.dumps(
        {
            "schema_version": "reader-answer.v1",
            "answer_blocks": [],
            "clarifying_question": "这段具体指哪一幕？",
            "uncertainty": None,
            "suggestion_candidates": [],
        },
        ensure_ascii=False,
    )


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def deployment(*, structured=True, priced=True):
    return ModelDeployment(
        provider="test",
        model_id="reader-balanced",
        revision="r1",
        supports_structured_output=structured,
        input_price_per_million=Decimal("1") if priced else None,
        output_price_per_million=Decimal("2") if priced else None,
    )


def budget(calls=3):
    return DualBudgetGate(
        conversation_policy=BudgetPolicy(calls, 10_000, 2_000, Decimal("1")),
        novel_policy=BudgetPolicy(calls, 10_000, 2_000, Decimal("1")),
    )


@pytest.mark.asyncio
async def test_structured_call_disables_hidden_retry_stream_and_remote_thread():
    transport = FakeTransport(
        [
            {
                "content": _valid_answer(),
                "usage": {"input_tokens": 20, "output_tokens": 5},
            }
        ]
    )
    gateway = ReaderChatGateway(transport)
    result = await gateway.generate(
        deployment=deployment(),
        messages=[{"role": "user", "content": "x"}],
        allowed_evidence_ids=ALLOWED,
        budget=budget(),
        job_id=9,
        max_input_tokens=100,
        max_output_tokens=50,
        timeout=12,
    )
    assert result.output.answer_blocks[0].block_id == "b1"
    call = transport.calls[0]
    assert call["response_format"] is ReaderAnswerEnvelope
    assert (
        call["timeout"] == 12 and call["num_retries"] == 0 and call["stream"] is False
    )
    assert call["model"] == "test/reader-balanced"
    assert "remote_thread_id" not in call and "conversation_id" not in call
    assert len(result.attempts) == 1 and result.attempts[0].status == "succeeded"


@pytest.mark.asyncio
async def test_tool_call_is_executed_before_structured_answer():
    transport = FakeTransport(
        [
            {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "search_novel_text",
                            "arguments": {"query": "第十章伏笔", "top_k": 3},
                        },
                    }
                ],
                "usage": {"input_tokens": 20, "output_tokens": 5},
            },
            {
                "content": _valid_answer(),
                "usage": {"input_tokens": 40, "output_tokens": 8},
            },
        ]
    )
    tool_calls: list[tuple[str, dict]] = []

    async def execute(name: str, arguments: dict) -> dict:
        tool_calls.append((name, arguments))
        return {"results": [{"chapter_number": 10, "text": "原文段落"}]}

    result = await ReaderChatGateway(transport).generate(
        deployment=deployment(),
        messages=[{"role": "user", "content": "问伏笔"}],
        allowed_evidence_ids=ALLOWED,
        budget=budget(),
        job_id=10,
        max_input_tokens=100,
        max_output_tokens=50,
        tools=[{"type": "function", "function": {"name": "search_novel_text"}}],
        tool_executor=execute,
    )

    assert result.output.answer_blocks[0].block_id == "b1"
    assert tool_calls == [("search_novel_text", {"query": "第十章伏笔", "top_k": 3})]
    assert [attempt.status for attempt in result.attempts] == [
        "tool_call",
        "succeeded",
    ]
    assert "tools" in transport.calls[0]
    assert "response_format" not in transport.calls[0]
    # Keep tools available for a second search; the model may decide that one
    # passage is insufficient. Structured validation still gates the response.
    assert "tools" in transport.calls[1]
    assert transport.calls[1]["messages"][-2]["role"] == "assistant"
    assert transport.calls[1]["messages"][-1]["role"] == "tool"


@pytest.mark.asyncio
async def test_capability_failure_pauses_before_network_or_budget():
    transport = FakeTransport([{"content": _valid_answer()}])
    gate = budget()
    with pytest.raises(DependencyPaused):
        await ReaderChatGateway(transport).generate(
            deployment=deployment(structured=False),
            messages=[],
            allowed_evidence_ids=ALLOWED,
            budget=gate,
            job_id=1,
            max_input_tokens=10,
            max_output_tokens=10,
        )
    assert transport.calls == []
    assert gate.conversation.reservations == {}


@pytest.mark.asyncio
async def test_unknown_ref_and_empty_citation_rejected_with_one_repair():
    bad = json.dumps(
        {
            "schema_version": "reader-answer.v1",
            "answer_blocks": [
                {
                    "block_id": "b1",
                    "text": "invented",
                    "evidence_refs": ["future:ch99"],
                }
            ],
            "clarifying_question": None,
            "uncertainty": None,
            "suggestion_candidates": [],
        }
    )
    transport = FakeTransport(
        [
            {"content": bad, "usage": {}},
            {
                "content": _valid_answer(),
                "usage": {"input_tokens": 8, "output_tokens": 3},
            },
        ]
    )
    gate = budget()
    result = await ReaderChatGateway(transport).generate(
        deployment=deployment(),
        messages=[],
        allowed_evidence_ids=ALLOWED,
        budget=gate,
        job_id=1,
        max_input_tokens=100,
        max_output_tokens=50,
    )
    assert [a.status for a in result.attempts] == ["failed", "succeeded"]
    assert len(transport.calls) == 2
    assert "validation error" in transport.calls[1]["messages"][-1][
        "content"
    ].lower() or ("error" in transport.calls[1]["messages"][-1]["content"].lower())


@pytest.mark.asyncio
async def test_second_invalid_response_publishes_no_success():
    transport = FakeTransport(
        [{"content": "{}", "usage": {}}, {"content": "{}", "usage": {}}]
    )
    with pytest.raises(StructuredOutputRejected) as exc:
        await ReaderChatGateway(transport).generate(
            deployment=deployment(),
            messages=[],
            allowed_evidence_ids=ALLOWED,
            budget=budget(),
            job_id=1,
            max_input_tokens=100,
            max_output_tokens=50,
        )
    assert len(transport.calls) == 2
    assert all(a.status == "failed" for a in exc.value.attempts)


@pytest.mark.asyncio
async def test_no_evidence_rejects_factual_blocks():
    transport = FakeTransport(
        [
            {"content": _valid_answer(), "usage": {}},
            {"content": _uncertainty(), "usage": {}},
        ]
    )
    result = await ReaderChatGateway(transport).generate(
        deployment=deployment(),
        messages=[],
        allowed_evidence_ids=set(),
        budget=budget(),
        job_id=2,
        max_input_tokens=100,
        max_output_tokens=50,
    )
    assert result.output.answer_blocks == []
    assert result.output.clarifying_question


def test_validate_answer_against_manifest_unit_gate():
    env = ReaderAnswerEnvelope.model_validate_json(_valid_answer())
    validate_answer_against_manifest(env, ALLOWED)
    with pytest.raises(ValueError):
        validate_answer_against_manifest(env, {"other:only"})


def test_business_validate_rejects_domain_write_suggestion_language():
    payload = {
        "schema_version": "reader-answer.v1",
        "answer_blocks": [],
        "clarifying_question": "ok?",
        "uncertainty": None,
        "suggestion_candidates": [
            {
                "candidate_type": "timeline",
                "target_ref": None,
                "proposal": "already applied to domain timeline",
                "evidence_refs": ["selection:primary"],
                "requires_explicit_confirmation": True,
            }
        ],
    }
    env = ReaderAnswerEnvelope.model_validate(payload)
    with pytest.raises(ValueError):
        business_validate_answer(env, allowed_evidence_ids=ALLOWED)


@pytest.mark.asyncio
async def test_ai_router_maps_reader_chat_to_balanced():
    from app.services.ai_router import ai_router, _TASK_TIER_MAP

    assert _TASK_TIER_MAP["reader_chat"] == "balanced"
    tier = ai_router.route_task("reader_chat")
    assert tier.model_id
