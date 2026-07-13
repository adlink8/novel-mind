"""Production timeline worker integration contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api import timeline as timeline_api
from app.models.analysis import AnalysisChapterStage, AnalysisRun, ModelCallAttempt
from app.models.chunk_build import ChunkActivePointer, ChunkBuild, ChunkHierarchyNode
from app.models.novel import Chapter, Novel
from app.models.timeline import (
    MachineTimelineEvent,
    TimelineActivePointer,
    TimelineEvidenceRef,
)
from app.models.user import User
from app.services.timeline.model_gateway import (
    ModelDeployment,
    PostgresCallRepository,
    TimelineModelGateway,
)
from app.services.timeline.worker import TimelineWorkerRuntime, run_timeline_worker

pytestmark = pytest.mark.integration


class ProductionTransport:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def complete(self, **kwargs):
        schema_name = kwargs["response_format"].__name__
        self.calls.append(schema_name)
        if schema_name == "TimelineExtraction":
            payload = json.loads(kwargs["messages"][1]["content"])
            chapter_id = payload["scope"]["chapter_id"]
            evidence = payload["evidence"][0]
            content = {
                "events": [{
                    "candidate_id": f"chapter-{chapter_id}-event",
                    "title": f"Event in chapter {chapter_id}",
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
        return {
            "id": f"request-{len(self.calls)}",
            "content": json.dumps(content),
            "usage": {"input_tokens": 20, "output_tokens": 10},
        }


class InterruptOnceTransport(ProductionTransport):
    def __init__(self) -> None:
        super().__init__()
        self.extraction_attempts: list[int] = []
        self.interrupted = False

    async def complete(self, **kwargs):
        if kwargs["response_format"].__name__ == "TimelineExtraction":
            payload = json.loads(kwargs["messages"][1]["content"])
            chapter_id = payload["scope"]["chapter_id"]
            self.extraction_attempts.append(chapter_id)
            if len(set(self.extraction_attempts)) == 2 and not self.interrupted:
                self.interrupted = True
                raise ConnectionError("simulated worker loss after first chapter")
        return await super().complete(**kwargs)


class InvalidReconciliationTransport(ProductionTransport):
    async def complete(self, **kwargs):
        if kwargs["response_format"].__name__ == "ReconciliationOutputModel":
            self.calls.append("ReconciliationOutputModel")
            return {"id": "invalid-reconcile", "content": "{}", "usage": {}}
        return await super().complete(**kwargs)


def _deployment(model_id: str) -> ModelDeployment:
    return ModelDeployment(
        provider="test",
        model_id=model_id,
        revision="r1",
        supports_structured_output=True,
        input_price_per_million=Decimal("1"),
        output_price_per_million=Decimal("2"),
    )


async def _seed_hierarchy(db_session):
    owner = await db_session.scalar(select(User).where(User.username == "testuser"))
    novel = Novel(owner_id=owner.id, title="Worker novel", status="ready")
    db_session.add(novel)
    await db_session.flush()
    chapters = [
        Chapter(novel_id=novel.id, chapter_number=10, title="Ten", content="Mira wakes."),
        Chapter(novel_id=novel.id, chapter_number=20, title="Twenty", content="Mira leaves."),
    ]
    db_session.add_all(chapters)
    await db_session.flush()
    build = ChunkBuild(
        build_id="worker-build", novel_id=novel.id, status="active",
        source_snapshot_hash="a" * 64, manifest_checksum="b" * 64,
        chunker_name="test", chunker_version="1", chunker_config_hash="c" * 64,
        collection_name="test", is_candidate=False, immutable=True,
    )
    db_session.add(build)
    db_session.add(ChunkActivePointer(
        novel_id=novel.id, build_id=build.build_id, committed_at=datetime.now(UTC),
    ))
    for index, chapter in enumerate(chapters):
        text = chapter.content
        db_session.add(ChunkHierarchyNode(
            build_id=build.build_id, novel_id=novel.id,
            node_id=f"evidence-{chapter.id}", level="evidence",
            chapter_id=chapter.id, chapter_number=chapter.chapter_number,
            parent_id=f"scene-{chapter.id}", child_ids=[], content=text,
            content_hash=__import__("hashlib").sha256(text.encode()).hexdigest(),
            source_start=0, source_end=len(text), chunk_type="paragraph",
            decision_lineage=[], order_index=index,
        ))
    await db_session.commit()
    return owner, novel, chapters


@pytest.mark.asyncio
async def test_first_entry_runs_durable_pipeline_and_repeat_entry_is_idempotent(
    db_session, auth_client, monkeypatch,
):
    owner, novel, chapters = await _seed_hierarchy(db_session)
    transport = ProductionTransport()
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    runtime = TimelineWorkerRuntime(
        sessions=sessions,
        gateway=TimelineModelGateway(transport, persistence=PostgresCallRepository(sessions)),
        extraction_deployment=_deployment("balanced-qualified"),
        reconciliation_deployment=_deployment("quality-qualified"),
    )

    async def dispatch(run_id: int) -> None:
        await run_timeline_worker(run_id, runtime=runtime)

    monkeypatch.setattr(timeline_api, "dispatch_timeline_run", dispatch)
    response = await auth_client.post(f"/api/timeline/{novel.id}/start-or-resume")
    assert response.status_code == 200
    run_id = response.json()["id"]
    owner_id, novel_id = owner.id, novel.id
    chapter_ids = [chapter.id for chapter in chapters]

    db_session.expire_all()
    run = await db_session.get(AnalysisRun, run_id)
    assert run.status == "completed"
    assert run.progress == {"completed_chapters": 2, "total_chapters": 2, "stage": "completed"}
    pointer = await db_session.scalar(select(TimelineActivePointer).where(
        TimelineActivePointer.owner_id == owner_id,
        TimelineActivePointer.novel_id == novel_id,
    ))
    assert pointer is not None and pointer.version_id == run.version_id
    events = list((await db_session.scalars(select(MachineTimelineEvent).where(
        MachineTimelineEvent.version_id == run.version_id,
    ).order_by(MachineTimelineEvent.narrative_chapter_number))).all())
    assert [event.narrative_chapter_number for event in events] == [10, 20]
    assert await db_session.scalar(select(func.count(TimelineEvidenceRef.id))) == 2
    completed = list((await db_session.scalars(select(AnalysisChapterStage).where(
        AnalysisChapterStage.run_id == run.id,
        AnalysisChapterStage.status == "completed",
    ))).all())
    assert {stage.stage_key for stage in completed} == {
        f"chapter_extract:{chapter_id}" for chapter_id in chapter_ids
    } | {"cross_chapter_reconcile:book"}
    assert transport.calls == ["TimelineExtraction", "TimelineExtraction", "ReconciliationOutputModel"]
    attempts = list((await db_session.scalars(select(ModelCallAttempt).where(
        ModelCallAttempt.run_id == run.id,
    ).order_by(ModelCallAttempt.id))).all())
    assert [attempt.status for attempt in attempts] == ["succeeded", "succeeded", "succeeded"]
    assert [attempt.stage_key for attempt in attempts] == [
        f"chapter_extract:{chapter_id}" for chapter_id in chapter_ids
    ] + ["cross_chapter_reconcile:book"]

    again = await auth_client.post(f"/api/timeline/{novel_id}/start-or-resume")
    assert again.status_code == 200 and again.json()["id"] == run_id
    assert transport.calls == ["TimelineExtraction", "TimelineExtraction", "ReconciliationOutputModel"]


@pytest.mark.asyncio
async def test_resume_skips_completed_chapter_after_interruption(
    db_session, auth_client, monkeypatch,
):
    _, novel, chapters = await _seed_hierarchy(db_session)
    novel_id = novel.id
    chapter_ids = [chapter.id for chapter in chapters]
    transport = InterruptOnceTransport()
    runtime = TimelineWorkerRuntime(
        sessions=async_sessionmaker(db_session.bind, expire_on_commit=False),
        gateway=TimelineModelGateway(
            transport,
            persistence=PostgresCallRepository(
                async_sessionmaker(db_session.bind, expire_on_commit=False),
            ),
        ),
        extraction_deployment=_deployment("balanced-qualified"),
        reconciliation_deployment=_deployment("quality-qualified"),
    )

    async def dispatch(run_id: int) -> None:
        await run_timeline_worker(run_id, runtime=runtime)

    monkeypatch.setattr(timeline_api, "dispatch_timeline_run", dispatch)
    started = await auth_client.post(f"/api/timeline/{novel_id}/start-or-resume")
    assert started.status_code == 200
    run_id = started.json()["id"]
    db_session.expire_all()
    assert (await db_session.get(AnalysisRun, run_id)).status == "paused_dependency"

    resumed = await auth_client.post(f"/api/timeline/{novel_id}/resume")
    assert resumed.status_code == 200
    db_session.expire_all()
    assert (await db_session.get(AnalysisRun, run_id)).status == "completed"
    assert transport.extraction_attempts == [chapter_ids[0], chapter_ids[1], chapter_ids[1]]


@pytest.mark.asyncio
async def test_invalid_reconciliation_never_promotes_partial_candidate(
    db_session, auth_client, monkeypatch,
):
    owner, novel, _ = await _seed_hierarchy(db_session)
    owner_id, novel_id = owner.id, novel.id
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    transport = InvalidReconciliationTransport()
    runtime = TimelineWorkerRuntime(
        sessions=sessions,
        gateway=TimelineModelGateway(
            transport, persistence=PostgresCallRepository(sessions),
        ),
        extraction_deployment=_deployment("balanced-qualified"),
        reconciliation_deployment=_deployment("quality-qualified"),
    )

    async def dispatch(run_id: int) -> None:
        await run_timeline_worker(run_id, runtime=runtime)

    monkeypatch.setattr(timeline_api, "dispatch_timeline_run", dispatch)
    response = await auth_client.post(f"/api/timeline/{novel_id}/start-or-resume")
    assert response.status_code == 200
    run_id = response.json()["id"]
    db_session.expire_all()
    run = await db_session.get(AnalysisRun, run_id)
    assert run.status == "paused_dependency"
    assert await db_session.scalar(select(TimelineActivePointer.id).where(
        TimelineActivePointer.owner_id == owner_id,
        TimelineActivePointer.novel_id == novel_id,
    )) is None
    attempts = list((await db_session.scalars(select(ModelCallAttempt).where(
        ModelCallAttempt.run_id == run_id,
        ModelCallAttempt.stage_key == "cross_chapter_reconcile:book",
    ).order_by(ModelCallAttempt.attempt_number))).all())
    assert [attempt.status for attempt in attempts] == ["schema_rejected", "schema_rejected"]
    assert transport.calls.count("ReconciliationOutputModel") == 2
