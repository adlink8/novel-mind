from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.schemas.timeline import EventCandidate, EvidenceRef, Participant, StoryTime
from app.services.timeline.budget import BudgetGate, BudgetPolicy, UnknownPricing
from app.services.timeline.model_gateway import ModelDeployment
from app.services.timeline.reconcile import (
    CausalProposal,
    ReconcileInput,
    TimelineReconciler,
)

pytestmark = pytest.mark.unit


def _event(candidate_id: str, chapter: int, index: int, *, title: str,
           mention: str = "阿宁", exact: datetime | None = None) -> EventCandidate:
    story_time = (StoryTime(precision="exact", expression="明确时刻", exact_time=exact)
                  if exact else StoryTime(precision="unknown"))
    return EventCandidate(
        candidate_id=candidate_id, title=title, description=title, event_type="plot",
        narrative_chapter_number=chapter, narrative_index=index,
        participants=[Participant(mention=mention)], story_time=story_time,
        evidence=[EvidenceRef(chapter_id=chapter, evidence_id=f"ev-{candidate_id}",
                              source_start=0, source_end=4, content_hash="a" * 64)],
        confidence=.9,
    )


def _budget() -> BudgetGate:
    return BudgetGate(BudgetPolicy(3, 20_000, 5_000, Decimal("1")))


@pytest.mark.asyncio
async def test_reconcile_uses_quality_deployment_and_reserves_before_transport():
    seen = []

    async def transport(payload):
        seen.append(payload)
        return ReconcileInput(
            duplicate_groups=[["a", "a-repeat"]],
            causal_edges=[CausalProposal(source_id="a", target_id="b", edge_type="causes",
                                          evidence_ids=["ev-a", "ev-b"], confidence=.9)],
        )

    deployment = ModelDeployment("openai", "quality", "r1", True, Decimal("1"), Decimal("2"))
    reconciler = TimelineReconciler(transport=transport, deployment=deployment, budget=_budget())
    result = await reconciler.reconcile(run_id=7, events=[
        _event("a", 1, 0, title="启程", exact=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        _event("a-repeat", 2, 0, title="回忆启程"),
        _event("b", 3, 0, title="抵达", exact=datetime(2020, 1, 2, tzinfo=timezone.utc)),
    ])

    assert seen[0]["deployment"] == ("openai", "quality", "r1")
    assert seen[0]["reservation_status"] == "reserved"
    assert [event.logical_event_id for event in result.events] == ["a", "b"]
    assert result.events[0].participant_mentions == ("阿宁",)
    assert result.events[0].story_rank == 0
    assert result.edges[0].evidence_ids == ("ev-a", "ev-b")


@pytest.mark.asyncio
async def test_unknown_price_pauses_before_call_without_fallback():
    calls = 0

    async def transport(payload):
        nonlocal calls
        calls += 1

    deployment = ModelDeployment("openai", "quality", "r1", True, None, Decimal("2"))  # type: ignore[arg-type]
    reconciler = TimelineReconciler(transport=transport, deployment=deployment, budget=_budget())
    with pytest.raises(UnknownPricing):
        await reconciler.reconcile(run_id=8, events=[_event("a", 1, 0, title="启程")])
    assert calls == 0
    assert reconciler.run_status == "paused_budget"


@pytest.mark.asyncio
async def test_cycles_and_contradictions_are_retained_without_fabricated_order():
    async def transport(payload):
        return ReconcileInput(
            duplicate_groups=[],
            story_constraints=[("a", "b", "before"), ("b", "a", "before")],
            causal_edges=[CausalProposal(source_id="a", target_id="b", edge_type="causes",
                                          evidence_ids=["ev-a"], confidence=.8)],
        )

    reconciler = TimelineReconciler(
        transport=transport,
        deployment=ModelDeployment("openai", "quality", "r1", True, Decimal("1"), Decimal("1")),
        budget=_budget(),
    )
    result = await reconciler.reconcile(run_id=9, events=[
        _event("a", 1, 0, title="甲"), _event("b", 1, 1, title="乙")
    ])
    assert all(event.story_rank is None for event in result.events)
    assert result.conflicts
    assert result.edges == ()  # causal evidence must cover both endpoints
