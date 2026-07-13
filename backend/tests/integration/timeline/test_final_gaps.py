"""Production evidence for the final Phase 08 verifier gaps."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.analysis import AnalysisRun, ModelCallAttempt
from app.models.chunk_build import ChunkActivePointer, ChunkBuild, ChunkHierarchyNode
from app.models.novel import Chapter, Novel
from app.models.timeline import TimelineActivePointer
from app.models.user import User
from app.services.timeline.model_gateway import (
    ModelDeployment,
    PostgresCallRepository,
    TimelineModelGateway,
)
from app.services.timeline.worker import TimelineWorkerRuntime, run_timeline_worker
from app.services.timeline import worker as worker_module

pytestmark = pytest.mark.integration


class FinalGapTransport:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.after_call = None

    async def complete(self, **kwargs):
        schema_name = kwargs["response_format"].__name__
        self.calls.append(schema_name)
        if schema_name == "TimelineExtraction":
            payload = json.loads(kwargs["messages"][1]["content"])
            chapter_id = payload["scope"]["chapter_id"]
            evidence = payload["evidence"][0]
            content = {
                "events": [{
                    "candidate_id": f"event-{chapter_id}",
                    "title": f"Event {chapter_id}",
                    "description": evidence["text"],
                    "event_type": "plot",
                    "narrative_chapter_number": chapter_id,
                    "narrative_index": 0,
                    "participants": [{"mention": "Mira", "entity_id": None}],
                    "story_time": {"precision": "unknown"},
                    "evidence": [{
                        "chapter_id": chapter_id,
                        "evidence_id": evidence["evidence_id"],
                        "source_start": evidence["source_start"],
                        "source_end": evidence["source_end"],
                        "content_hash": evidence["content_hash"],
                    }],
                    "confidence": 0.95,
                }],
                "story_time_constraints": [],
            }
        else:
            content = {"duplicate_groups": [], "story_constraints": [], "causal_edges": []}
        if self.after_call is not None:
            await self.after_call(schema_name)
        return {
            "id": f"final-gap-{len(self.calls)}",
            "content": json.dumps(content),
            "usage": {"input_tokens": 20, "output_tokens": 10},
        }


def _deployment(model_id: str) -> ModelDeployment:
    return ModelDeployment("test", model_id, "r1", True, Decimal("1"), Decimal("2"))


async def _seed_run(session):
    owner = await session.scalar(select(User).where(User.username == "testuser"))
    unique = uuid.uuid4().hex
    novel = Novel(owner_id=owner.id, title=f"Final gaps {unique}", status="ready")
    session.add(novel)
    await session.flush()
    chapters = [
        Chapter(novel_id=novel.id, chapter_number=2, title="Two", content="Mira wakes."),
        Chapter(novel_id=novel.id, chapter_number=9, title="Nine", content="Mira leaves."),
    ]
    session.add_all(chapters)
    await session.flush()
    build = ChunkBuild(
        build_id=f"final-gap-{unique}", novel_id=novel.id, status="active",
        source_snapshot_hash="a" * 64, manifest_checksum="b" * 64,
        chunker_name="test", chunker_version="1", chunker_config_hash="c" * 64,
        collection_name="test", is_candidate=False, immutable=True,
    )
    session.add(build)
    session.add(ChunkActivePointer(
        novel_id=novel.id, build_id=build.build_id, committed_at=datetime.now(UTC),
    ))
    for index, chapter in enumerate(chapters):
        session.add(ChunkHierarchyNode(
            build_id=build.build_id, novel_id=novel.id,
            node_id=f"evidence-{chapter.id}", level="evidence",
            chapter_id=chapter.id, chapter_number=chapter.chapter_number,
            parent_id=f"scene-{chapter.id}", child_ids=[], content=chapter.content,
            content_hash=hashlib.sha256(chapter.content.encode()).hexdigest(),
            source_start=index * 100, source_end=index * 100 + len(chapter.content),
            chunk_type="paragraph", decision_lineage=[], order_index=index,
        ))
    run = AnalysisRun(owner_id=owner.id, novel_id=novel.id, status="pending", active_key=None)
    session.add(run)
    await session.commit()
    return owner, novel, run


def _runtime(db_session, transport: FinalGapTransport) -> TimelineWorkerRuntime:
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    return TimelineWorkerRuntime(
        sessions=sessions,
        gateway=TimelineModelGateway(transport, persistence=PostgresCallRepository(sessions)),
        extraction_deployment=_deployment("balanced-qualified"),
        reconciliation_deployment=_deployment("quality-qualified"),
    )


async def _request_cancel(sessions, run_id: int) -> None:
    async with sessions.begin() as session:
        run = await session.get(AnalysisRun, run_id, with_for_update=True)
        run.cancel_requested = True


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["evidence", "extraction", "persist", "reconcile", "promotion"])
async def test_running_worker_observes_cancel_between_every_production_stage(
    db_session, auth_client, monkeypatch, boundary,
):
    owner, novel, run = await _seed_run(db_session)
    owner_id, novel_id, run_id = owner.id, novel.id, run.id
    transport = FinalGapTransport()
    runtime = _runtime(db_session, transport)
    sessions = runtime.sessions

    if boundary == "evidence":
        original = worker_module._prepare_run

        async def cancel_after_prepare(*args, **kwargs):
            result = await original(*args, **kwargs)
            await _request_cancel(sessions, run_id)
            return result

        monkeypatch.setattr(worker_module, "_prepare_run", cancel_after_prepare)
    elif boundary == "extraction":
        async def cancel_after_first_extraction(schema_name):
            if schema_name == "TimelineExtraction" and transport.calls.count(schema_name) == 1:
                await _request_cancel(sessions, run_id)

        transport.after_call = cancel_after_first_extraction
    elif boundary == "persist":
        original = worker_module._persist_chapter

        async def cancel_after_persist(*args, **kwargs):
            await original(*args, **kwargs)
            await _request_cancel(sessions, run_id)

        monkeypatch.setattr(worker_module, "_persist_chapter", cancel_after_persist)
    elif boundary == "reconcile":
        async def cancel_during_reconcile(schema_name):
            if schema_name == "ReconciliationOutputModel":
                await _request_cancel(sessions, run_id)

        transport.after_call = cancel_during_reconcile
    else:
        original = worker_module._reconcile_and_persist

        async def cancel_before_promotion(*args, **kwargs):
            await original(*args, **kwargs)
            await _request_cancel(sessions, run_id)

        monkeypatch.setattr(worker_module, "_reconcile_and_persist", cancel_before_promotion)

    await run_timeline_worker(run_id, runtime=runtime)

    db_session.expire_all()
    persisted = await db_session.get(AnalysisRun, run_id)
    assert persisted.status == "cancelled"
    assert await db_session.scalar(select(TimelineActivePointer.id).where(
        TimelineActivePointer.owner_id == owner_id,
        TimelineActivePointer.novel_id == novel_id,
    )) is None
    expected_calls = {"evidence": 0, "extraction": 1, "persist": 1, "reconcile": 3, "promotion": 3}
    assert len(transport.calls) == expected_calls[boundary]


@pytest.mark.asyncio
@pytest.mark.parametrize("changed_field", ["prompt_hash", "schema_hash"])
async def test_reconciliation_cache_misses_after_lineage_hash_change(
    db_session, auth_client, monkeypatch, changed_field,
):
    _, novel, baseline_run = await _seed_run(db_session)
    transport = FinalGapTransport()
    runtime = _runtime(db_session, transport)
    await run_timeline_worker(baseline_run.id, runtime=runtime)

    changed_run = AnalysisRun(
        owner_id=baseline_run.owner_id, novel_id=novel.id, status="pending", active_key=None,
    )
    db_session.add(changed_run)
    await db_session.commit()
    original = worker_module._prepare_run

    async def mutate_lineage_after_restart(*args, **kwargs):
        result = await original(*args, **kwargs)
        run, version, build, chapters = result
        changed = hashlib.sha256(f"changed-{changed_field}".encode()).hexdigest()
        async with runtime.sessions.begin() as session:
            persisted = await session.get(type(version), version.id, with_for_update=True)
            setattr(persisted, changed_field, changed)
        setattr(version, changed_field, changed)
        return run, version, build, chapters

    monkeypatch.setattr(worker_module, "_prepare_run", mutate_lineage_after_restart)
    call_start = len(transport.calls)
    await run_timeline_worker(changed_run.id, runtime=runtime)

    db_session.expire_all()
    attempts = list((await db_session.scalars(select(ModelCallAttempt).where(
        ModelCallAttempt.run_id == changed_run.id,
        ModelCallAttempt.stage_key == "cross_chapter_reconcile:book",
    ))).all())
    assert [attempt.status for attempt in attempts] == ["succeeded"]
    assert transport.calls[call_start:].count("ReconciliationOutputModel") == 1
