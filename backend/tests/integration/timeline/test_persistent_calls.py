"""PostgreSQL-backed timeline budget, cache, and call-audit contracts."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.analysis import (
    AnalysisBudgetLedger,
    AnalysisBudgetReservation,
    AnalysisRun,
    ModelCallAttempt,
)
from app.models.novel import Novel
from app.models.user import User
from app.schemas.timeline import TimelineExtraction
from app.services.timeline.budget import BudgetGate, BudgetPolicy, UnknownPricing
from app.services.timeline.model_gateway import (
    DependencyPaused,
    ModelDeployment,
    PostgresCallRepository,
    TimelineModelGateway,
)

pytestmark = pytest.mark.integration


VALID = '{"events":[],"story_time_constraints":[]}'


class RecordingTransport:
    def __init__(self, responses=None):
        self.responses = list(responses or [{
            "id": "provider-request-1",
            "content": VALID,
            "usage": {"input_tokens": 12, "output_tokens": 4},
        }])
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def deployment(*, structured=True, priced=True):
    return ModelDeployment(
        provider="test", model_id="balanced-qualified", revision="r1",
        supports_structured_output=structured,
        input_price_per_million=Decimal("1") if priced else None,
        output_price_per_million=Decimal("2"),
    )


async def _run_with_ledger(session, suffix: str, *, max_calls: int = 3):
    owner = User(username=f"calls-{suffix}", email=f"calls-{suffix}@example.test", hashed_password="x")
    session.add(owner)
    await session.flush()
    novel = Novel(owner_id=owner.id, title=f"calls-{suffix}", status="ready")
    session.add(novel)
    await session.flush()
    run = AnalysisRun(owner_id=owner.id, novel_id=novel.id, active_key="active", status="running")
    session.add(run)
    await session.flush()
    session.add(AnalysisBudgetLedger(
        run_id=run.id, max_calls=max_calls, max_input_tokens=1000,
        max_output_tokens=500, max_cost_usd=Decimal("1"),
    ))
    await session.commit()
    return run.id


@pytest.mark.asyncio
async def test_successful_call_reserves_settles_and_survives_repository_restart(db_session):
    run_id = await _run_with_ledger(db_session, "success")
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    transport = RecordingTransport()
    gateway = TimelineModelGateway(transport, persistence=PostgresCallRepository(sessions))
    result = await gateway.generate(
        deployment=deployment(), schema=TimelineExtraction,
        messages=[{"role": "user", "content": "extract"}],
        budget=BudgetGate(BudgetPolicy(3, 1000, 500, Decimal("1"))),
        run_id=run_id, stage_key="chapter_extract:1",
        cache_key="cache-key-1", max_input_tokens=100, max_output_tokens=50,
    )
    assert result.output.events == []

    db_session.expire_all()
    attempt = (await db_session.scalars(select(ModelCallAttempt).where(
        ModelCallAttempt.run_id == run_id,
    ))).one()
    reservation = await db_session.get(AnalysisBudgetReservation, attempt.reservation_id)
    ledger = await db_session.scalar(select(AnalysisBudgetLedger).where(
        AnalysisBudgetLedger.run_id == run_id,
    ))
    assert attempt.status == "succeeded"
    assert attempt.provider_request_id == "provider-request-1"
    assert attempt.cache_key == "cache-key-1"
    assert reservation.status == "settled"
    assert ledger.reserved_calls == 0 and ledger.settled_calls == 1
    assert ledger.settled_input_tokens == 12 and ledger.settled_output_tokens == 4

    restarted_repository = PostgresCallRepository(sessions)
    skipped = await restarted_repository.record_cache_hit(
        run_id=run_id, stage_key="chapter_extract:cached", cache_key="cache-key-1",
        source_attempt_id=attempt.id, artifact_checksum="a" * 64,
    )
    assert skipped.status == "call-skipped" and skipped.cache_source_attempt_id == attempt.id
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_unknown_price_and_missing_schema_capability_make_zero_provider_calls(db_session):
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    run_id = await _run_with_ledger(db_session, "preflight")
    transport = RecordingTransport()
    gateway = TimelineModelGateway(transport, persistence=PostgresCallRepository(sessions))
    with pytest.raises(UnknownPricing):
        await gateway.generate(
            deployment=deployment(priced=False), schema=TimelineExtraction, messages=[],
            budget=BudgetGate(BudgetPolicy(3, 1000, 500, Decimal("1"))),
            run_id=run_id, stage_key="chapter_extract:unknown-price",
            max_input_tokens=100, max_output_tokens=50,
        )
    with pytest.raises(DependencyPaused):
        await gateway.generate(
            deployment=deployment(structured=False), schema=TimelineExtraction, messages=[],
            budget=BudgetGate(BudgetPolicy(3, 1000, 500, Decimal("1"))),
            run_id=run_id, stage_key="chapter_extract:no-schema",
            max_input_tokens=100, max_output_tokens=50,
        )
    assert transport.calls == []
    db_session.expire_all()
    run = await db_session.get(AnalysisRun, run_id)
    assert run.status == "paused_budget"


@pytest.mark.asyncio
async def test_gateway_uses_strict_validation_and_one_persisted_repair(db_session):
    run_id = await _run_with_ledger(db_session, "strict")
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    coercible = (
        '{"events":[{"candidate_id":"e","title":"t","description":"d",'
        '"event_type":"plot","narrative_chapter_number":1,"narrative_index":0,'
        '"participants":[{"mention":"Mira","entity_id":"7"}],'
        '"story_time":{"precision":"unknown"},"evidence":[{"chapter_id":1,'
        '"evidence_id":"ev","source_start":0,"source_end":1,"content_hash":"'
        + "a" * 64 + '"}],"confidence":0.9}],"story_time_constraints":[]}'
    )
    transport = RecordingTransport([
        {"id": "bad", "content": coercible, "usage": {}},
        {"id": "good", "content": VALID, "usage": {}},
    ])
    gateway = TimelineModelGateway(transport, persistence=PostgresCallRepository(sessions))
    result = await gateway.generate(
        deployment=deployment(), schema=TimelineExtraction, messages=[],
        budget=BudgetGate(BudgetPolicy(3, 1000, 500, Decimal("1"))),
        run_id=run_id, stage_key="chapter_extract:strict",
        max_input_tokens=100, max_output_tokens=50,
    )
    assert result.output.events == [] and len(transport.calls) == 2
    db_session.expire_all()
    attempts = list((await db_session.scalars(select(ModelCallAttempt).where(
        ModelCallAttempt.run_id == run_id,
    ).order_by(ModelCallAttempt.attempt_number))).all())
    assert [attempt.status for attempt in attempts] == ["schema_rejected", "succeeded"]
