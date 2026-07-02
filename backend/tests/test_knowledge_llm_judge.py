"""LLM judgment tests for evidence-bounded knowledge packages."""

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge import (
    KnowledgeExtractionRun,
    KnowledgeRelationCandidate,
    KnowledgeRelationJudgment,
)
from app.models.novel import Novel
from app.models.user import User
from app.services.knowledge.llm_judge import (
    JudgmentResult,
    KnowledgeLLMJudgeService,
    llm_judge_service,
)


def _package() -> dict:
    return {
        "package_version": "knowledge-evidence-package.v1",
        "domain_profile": "history",
        "ontology_profile": "history.v1",
        "allowed_relation_types": ["allied_with", "conflicted_with", "preceded"],
        "allowed_evidence_ids": ["ev-chunk-1", "ev-chunk-2"],
        "candidate": {
            "candidate_id": 7,
            "relation_type": "allied_with",
            "source": {"kind": "text_chunk", "id": 1},
            "target": {"kind": "text_chunk", "id": 2},
            "recall_signals": {"adjacency": {"same_chapter": True}},
            "evidence_refs": ["ev-chunk-1", "ev-chunk-2"],
        },
        "evidence": [
            {"evidence_id": "ev-chunk-1", "excerpt": "刘备与关羽结义。"},
            {"evidence_id": "ev-chunk-2", "excerpt": "二人共同起兵。"},
        ],
    }


def _valid_output(**overrides) -> str:
    payload = {
        "candidate_id": 7,
        "relation_type": "allied_with",
        "confidence": 0.74,
        "evidence_refs": ["ev-chunk-1"],
        "rationale": "The cited evidence supports an alliance-style relation.",
        "risk_flags": [],
        "needs_human_review": False,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def test_parse_valid_judgment_keeps_result_unaccepted():
    service = KnowledgeLLMJudgeService()
    result = service.parse_judgment(
        _valid_output(),
        package=_package(),
        model_name="openai/gpt-4o-mini",
        raw_output={"content": _valid_output()},
    )

    assert result.status == "pending"
    assert result.gate_status == "evidence_passed"
    assert result.confidence == 0.74
    assert result.evidence_refs == ["ev-chunk-1"]
    assert result.structured_output["relation_type"] == "allied_with"
    assert result.status != "accepted"


def test_parse_invalid_json_returns_schema_failed():
    service = KnowledgeLLMJudgeService()
    result = service.parse_judgment(
        "not json",
        package=_package(),
        model_name="openai/gpt-4o-mini",
    )

    assert result.status == "schema_failed"
    assert result.gate_status == "schema_failed"
    assert result.needs_human_review is True
    assert result.gate_failures[0].startswith("schema:")


def test_parse_out_of_package_evidence_returns_evidence_failed():
    service = KnowledgeLLMJudgeService()
    result = service.parse_judgment(
        _valid_output(evidence_refs=["ev-chunk-999"]),
        package=_package(),
        model_name="openai/gpt-4o-mini",
    )

    assert result.status == "evidence_failed"
    assert result.gate_status == "evidence_failed"
    assert result.needs_human_review is True
    assert result.gate_failures == ["out_of_package_evidence:ev-chunk-999"]


@pytest.mark.asyncio
async def test_judge_package_records_blocked_when_llm_call_fails(monkeypatch):
    async def fake_chat(**kwargs):
        assert kwargs["temperature"] <= 0.2
        raise RuntimeError("missing api key")

    monkeypatch.setattr("app.services.knowledge.llm_judge.ai_service.chat", fake_chat)
    monkeypatch.setattr(
        "app.services.knowledge.llm_judge.ai_router.route_task",
        lambda task_type: SimpleNamespace(provider="openai", model_id="gpt-4o-mini"),
    )

    result = await llm_judge_service.judge_package(_package())

    assert result.status == "blocked"
    assert result.gate_status == "rejected"
    assert result.needs_human_review is True
    assert result.gate_failures == ["blocked:RuntimeError"]
    assert "missing api key" in result.raw_output["error"]


@pytest.mark.asyncio
async def test_judge_package_uses_ai_service_and_schema_validation(monkeypatch):
    class FakeResponse:
        choices = [SimpleNamespace(message=SimpleNamespace(content=_valid_output()))]
        usage = SimpleNamespace(prompt_tokens=11, completion_tokens=13)

    async def fake_chat(**kwargs):
        assert kwargs["model"] == "openai/gpt-4o-mini"
        assert kwargs["temperature"] == 0.1
        assert kwargs["max_tokens"] <= 1200
        user_message = kwargs["messages"][1]["content"]
        assert "allowed_evidence_ids" in user_message
        assert "ev-chunk-999" not in user_message
        return FakeResponse()

    monkeypatch.setattr("app.services.knowledge.llm_judge.ai_service.chat", fake_chat)
    monkeypatch.setattr(
        "app.services.knowledge.llm_judge.ai_router.route_task",
        lambda task_type: SimpleNamespace(provider="openai", model_id="gpt-4o-mini"),
    )

    result = await llm_judge_service.judge_package(_package())

    assert result.status == "pending"
    assert result.gate_status == "evidence_passed"
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 13


@pytest.mark.asyncio
async def test_persist_judgment_result_can_store_blocked_status(
    db_session: AsyncSession,
):
    user = User(
        username="kg_judge_owner",
        email="kg_judge_owner@test.com",
        hashed_password="hash",
    )
    db_session.add(user)
    await db_session.flush()

    novel = Novel(title="LLM 判定测试", owner_id=user.id)
    db_session.add(novel)
    await db_session.flush()

    run = KnowledgeExtractionRun(
        owner_id=user.id,
        novel_id=novel.id,
        run_name="judge run",
        domain_profile="fiction",
        ontology_profile="fiction.v1",
        status="running",
    )
    db_session.add(run)
    await db_session.flush()

    candidate = KnowledgeRelationCandidate(
        owner_id=user.id,
        novel_id=novel.id,
        run_id=run.id,
        domain_profile="fiction",
        relation_type="precedes",
        source_kind="text_chunk",
        source_id=1,
        target_kind="text_chunk",
        target_id=2,
        recall_signals={"adjacency": {"same_chapter": True}},
        package_snapshot=_package(),
        evidence_refs=["ev-chunk-1", "ev-chunk-2"],
        status="candidate",
    )
    db_session.add(candidate)
    await db_session.flush()

    result = JudgmentResult(
        status="blocked",
        gate_status="rejected",
        candidate_id=candidate.id,
        model_name="openai/gpt-4o-mini",
        relation_type="precedes",
        evidence_refs=["ev-chunk-1", "ev-chunk-2"],
        raw_output={"error": "not authenticated"},
        gate_failures=["blocked:AuthenticationError"],
        needs_human_review=True,
    )

    judgment = await llm_judge_service.persist_judgment_result(
        db_session,
        candidate=candidate,
        result=result,
    )
    await db_session.commit()

    persisted = (
        await db_session.execute(
            select(KnowledgeRelationJudgment).where(
                KnowledgeRelationJudgment.id == judgment.id
            )
        )
    ).scalar_one()

    assert persisted.status == "blocked"
    assert persisted.gate_status == "rejected"
    assert persisted.gate_failures == ["blocked:AuthenticationError"]
    assert persisted.structured_output == {}
    assert candidate.status == "needs_human_review"
