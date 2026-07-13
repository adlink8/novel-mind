"""Durable timeline job state-machine contract."""

from datetime import UTC, datetime, timedelta

import pytest

from app.services.timeline.jobs import InMemoryTimelineJobStore, TimelineJobCoordinator, stable_stage_key

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_first_entry_is_idempotent_and_restart_resumes_checkpoint():
    store = InMemoryTimelineJobStore()
    first = TimelineJobCoordinator(store)
    run1 = await first.start_on_first_entry(owner_id=7, novel_id=11)
    run2 = await first.start_on_first_entry(owner_id=7, novel_id=11)
    assert run1.id == run2.id
    await first.complete_stage(run1.id, stable_stage_key("extract", chapter_id=3), "abc")

    restarted = TimelineJobCoordinator(store)
    assert (await restarted.pending_stages(run1.id, [stable_stage_key("extract", chapter_id=3)])) == []


@pytest.mark.asyncio
async def test_lease_is_cas_and_expired_lease_can_be_reclaimed():
    store = InMemoryTimelineJobStore()
    jobs = TimelineJobCoordinator(store, lease_seconds=30)
    run = await jobs.start_on_first_entry(owner_id=1, novel_id=2)
    lease = await jobs.acquire_lease(run.id, now=datetime(2026, 1, 1, tzinfo=UTC))
    assert lease
    assert await jobs.acquire_lease(run.id, now=datetime(2026, 1, 1, tzinfo=UTC)) is None
    reclaimed = await jobs.acquire_lease(run.id, now=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=31))
    assert reclaimed and reclaimed != lease


@pytest.mark.asyncio
async def test_cancel_and_resume_preserve_checkpoint():
    jobs = TimelineJobCoordinator(InMemoryTimelineJobStore())
    run = await jobs.start_on_first_entry(owner_id=1, novel_id=3)
    await jobs.request_cancel(run.id)
    assert (await jobs.get(run.id)).status == "cancelled"
    await jobs.resume(run.id)
    assert (await jobs.get(run.id)).status == "pending"
