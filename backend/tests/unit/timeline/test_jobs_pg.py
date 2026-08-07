"""PostgresTimelineJobStore + coordinator edge cases (short-transaction CAS)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.analysis import AnalysisChapterStage, AnalysisRun
from app.models.novel import Novel
from app.models.user import User
from app.services.timeline.jobs import (
    PostgresTimelineJobStore,
    TimelineJobCoordinator,
    stable_stage_key,
)

pytestmark = pytest.mark.unit


def test_stable_stage_key_variants():
    assert stable_stage_key("extract") == "extract:book"
    assert stable_stage_key("extract", chapter_id=3) == "extract:3"
    assert stable_stage_key("extract", chapter_id=3, attempt=1) == "extract:3:1"


async def _seed_run(db_session, *, status="pending", lease_id=None, lease_expires_at=None):
    from app.models.novel import Chapter

    owner = User(username="pg-jobs", email="pg-jobs@example.com", hashed_password="x")
    db_session.add(owner)
    await db_session.flush()
    novel = Novel(owner_id=owner.id, title="PG任务书", status="ready")
    db_session.add(novel)
    await db_session.flush()
    chapter = Chapter(
        novel_id=novel.id, chapter_number=1, title="第一章", content="正文"
    )
    db_session.add(chapter)
    await db_session.flush()
    run = AnalysisRun(
        owner_id=owner.id,
        novel_id=novel.id,
        active_key="active",
        status=status,
        lease_id=lease_id,
        lease_expires_at=lease_expires_at,
    )
    db_session.add(run)
    await db_session.flush()
    await db_session.commit()
    return owner, novel, run, chapter


def _store(db_session):
    return PostgresTimelineJobStore(async_sessionmaker(db_session.bind, expire_on_commit=False))


@pytest.mark.asyncio
async def test_create_or_get_active_creates_then_returns_same(db_session):
    owner, novel, run, chapter = await _seed_run(db_session)
    store = _store(db_session)
    first = await store.create_or_get_active(owner.id, novel.id)
    assert first.id == run.id
    second = await store.create_or_get_active(owner.id, novel.id)
    assert second.id == first.id


@pytest.mark.asyncio
async def test_get_returns_none_and_includes_completed_stages(db_session):
    owner, novel, run, chapter = await _seed_run(db_session)
    db_session.add(
        AnalysisChapterStage(
            run_id=run.id,
            chapter_id=chapter.id,
            stage_key="extract:1",
            status="completed",
            artifact_checksum="abc123",
            checkpoint={},
        )
    )
    await db_session.commit()
    store = _store(db_session)
    record = await store.get(run.id)
    assert record.owner_id == owner.id
    assert record.completed_stages["extract:1"] == "abc123"
    assert await store.get(999_999) is None


@pytest.mark.asyncio
async def test_acquire_lease_cas_semantics(db_session):
    now = datetime.now(UTC)
    owner, novel, run, chapter = await _seed_run(db_session)
    store = _store(db_session)
    assert await store.acquire_lease(run.id, "lease-1", now, now + timedelta(minutes=5)) is True
    # a second lease while the first is still valid must fail
    assert (
        await store.acquire_lease(run.id, "lease-2", now, now + timedelta(minutes=5)) is False
    )
    # expired lease can be re-acquired
    later = now + timedelta(minutes=10)
    assert (
        await store.acquire_lease(run.id, "lease-3", later, later + timedelta(minutes=5)) is True
    )


@pytest.mark.asyncio
async def test_complete_stage_inserts_and_updates(db_session):
    owner, novel, run, chapter = await _seed_run(db_session)
    store = _store(db_session)
    await store.complete_stage(run.id, "extract:1", "cs-a")
    await store.complete_stage(run.id, "extract:1", "cs-a")  # idempotent
    await db_session.commit()
    stages = list((await db_session.scalars(select(AnalysisChapterStage))).all())
    assert len(stages) == 1
    assert stages[0].artifact_checksum == "cs-a"

    # pre-existing incomplete stage gets updated to completed
    db_session.add(
        AnalysisChapterStage(
            run_id=run.id,
            chapter_id=chapter.id,
            stage_key="extract:2",
            status="running",
            checkpoint={},
        )
    )
    await db_session.commit()
    await store.complete_stage(run.id, "extract:2", "cs-b")
    await db_session.commit()
    stage2 = (
        await db_session.scalar(
            select(AnalysisChapterStage).where(AnalysisChapterStage.stage_key == "extract:2")
        )
    )
    assert stage2.status == "completed"
    assert stage2.artifact_checksum == "cs-b"


@pytest.mark.asyncio
async def test_set_status_cancels_active_key_on_failure(db_session):
    owner, novel, run, chapter = await _seed_run(db_session)
    store = _store(db_session)
    await store.set_status(run.id, "failed")
    run_id = run.id  # capture before expire_all expires the PK too
    db_session.expire_all()
    row = await db_session.get(AnalysisRun, run_id)
    assert row.status == "failed"
    assert row.active_key is None

    await store.set_status(run_id, "pending")
    db_session.expire_all()
    row = await db_session.get(AnalysisRun, run_id)
    assert row.status == "pending"
    assert row.active_key == "active"


@pytest.mark.asyncio
async def test_coordinator_get_missing_raises_and_cancel_resume(db_session):
    owner, novel, run, chapter = await _seed_run(db_session)
    coordinator = TimelineJobCoordinator(_store(db_session))
    with pytest.raises(KeyError):
        await coordinator.get(999_999)

    await coordinator.request_cancel(run.id)
    await coordinator.resume(run.id)
    record = await coordinator.get(run.id)
    assert record.status == "pending"
    assert record.cancel_requested is False


@pytest.mark.asyncio
async def test_coordinator_acquire_lease_and_pending_stages(db_session):
    owner, novel, run, chapter = await _seed_run(db_session)
    store = _store(db_session)
    coordinator = TimelineJobCoordinator(store)
    lease = await coordinator.acquire_lease(run.id, now=datetime.now(UTC))
    assert lease is not None
    assert await coordinator.acquire_lease(run.id) is None  # already leased

    await store.complete_stage(run.id, "extract:1", "cs")
    await db_session.commit()
    pending = await coordinator.pending_stages(run.id, ["extract:1", "extract:2", "reconcile"])
    assert pending == ["extract:2", "reconcile"]
