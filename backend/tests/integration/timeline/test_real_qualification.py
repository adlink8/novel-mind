"""Production-backed timeline qualification contracts."""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.analysis import AnalysisRun
from app.models.chunk_build import ChunkActivePointer, ChunkBuild, ChunkHierarchyNode
from app.models.novel import Chapter, Novel
from app.models.user import User
from app.services.timeline.model_gateway import ModelDeployment, PostgresCallRepository, TimelineModelGateway
from app.services.timeline.worker import TimelineWorkerRuntime
from scripts.run_timeline_qualification import render_markdown, run_production_qualification

pytestmark = pytest.mark.integration


class QualificationTransport:
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
        return {
            "id": f"qualification-request-{len(self.calls)}",
            "content": json.dumps(content),
            "usage": {"input_tokens": 20, "output_tokens": 10},
        }


def _deployment(model_id: str) -> ModelDeployment:
    return ModelDeployment("controlled", model_id, "r1", True, Decimal("1"), Decimal("2"))


async def _seed_run(session):
    unique = uuid.uuid4().hex
    owner = User(
        username=f"qualification-{unique}", email=f"qualification-{unique}@example.test",
        hashed_password="not-used-by-qualification",
    )
    session.add(owner)
    await session.flush()
    novel = Novel(owner_id=owner.id, title=f"Qualification novel {unique}", status="ready")
    session.add(novel)
    await session.flush()
    chapters = [
        Chapter(novel_id=novel.id, chapter_number=2, title="Two", content="Mira wakes."),
        Chapter(novel_id=novel.id, chapter_number=9, title="Nine", content="Mira leaves."),
    ]
    session.add_all(chapters)
    await session.flush()
    novel.reading_progress = {"chapter_id": chapters[0].id, "progress_percent": 100}
    build = ChunkBuild(
        build_id=f"qualification-{unique}", novel_id=novel.id, status="active",
        source_snapshot_hash="a" * 64, manifest_checksum="b" * 64,
        chunker_name="test", chunker_version="1", chunker_config_hash="c" * 64,
        collection_name="test", is_candidate=False, immutable=True,
    )
    session.add(build)
    session.add(ChunkActivePointer(
        novel_id=novel.id, build_id=build.build_id, committed_at=datetime.now(UTC),
    ))
    for index, chapter in enumerate(chapters):
        content_hash = __import__("hashlib").sha256(chapter.content.encode()).hexdigest()
        session.add(ChunkHierarchyNode(
            build_id=build.build_id, novel_id=novel.id,
            node_id=f"qualification-evidence-{chapter.id}", level="evidence",
            chapter_id=chapter.id, chapter_number=chapter.chapter_number,
            parent_id=f"scene-{chapter.id}", child_ids=[], content=chapter.content,
            content_hash=content_hash, source_start=0, source_end=len(chapter.content),
            chunk_type="paragraph", decision_lineage=[], order_index=index,
        ))
    run = AnalysisRun(owner_id=owner.id, novel_id=novel.id, status="pending", active_key="active")
    session.add(run)
    await session.commit()
    return owner, novel, chapters, run


@pytest.mark.asyncio
async def test_qualification_executes_worker_and_measures_persisted_artifacts(pg_async_url, require_postgres):
    engine = create_async_engine(pg_async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        owner, novel, chapters, run = await _seed_run(session)
    transport = QualificationTransport()
    runtime = TimelineWorkerRuntime(
        sessions=sessions,
        gateway=TimelineModelGateway(transport, persistence=PostgresCallRepository(sessions)),
        extraction_deployment=_deployment("balanced-qualified"),
        reconciliation_deployment=_deployment("quality-qualified"),
    )
    expected_ids = [f"event-{chapter.id}" for chapter in chapters]

    report = await run_production_qualification(
        run.id, runtime=runtime, sessions=sessions,
        expected_event_ids=expected_ids, expected_story_order=expected_ids,
    )

    assert report["status"] == "qualified", report
    assert report["artifact"]["database_dialect"] == "postgresql"
    assert report["artifact"]["run"]["status"] == "completed"
    assert report["artifact"]["counts"] == {
        "events": 2, "evidence_refs": 2, "model_attempts": 3, "completed_stages": 3,
    }
    assert report["artifact"]["visible_default_event_ids"] == [expected_ids[0]]
    assert report["metrics"]["event_precision"] == 1.0
    assert report["metrics"]["event_recall"] == 1.0
    assert report["metrics"]["spoiler_leaks"] == 0
    assert report["metrics"]["provider_calls"] == 3
    assert report["metrics"]["cost_usd_total"] > 0
    assert report["artifact_sha256"] and report["report_sha256"]
    assert transport.calls == ["TimelineExtraction", "TimelineExtraction", "ReconciliationOutputModel"]
    if output := os.environ.get("TIMELINE_QUALIFICATION_OUT"):
        Path(output).write_text(render_markdown(report), encoding="utf-8")
    async with sessions.begin() as session:
        persisted_owner = await session.get(User, owner.id)
        await session.delete(persisted_owner)
    await engine.dispose()


@pytest.mark.asyncio
async def test_qualification_cannot_pass_from_missing_expected_production_output(pg_async_url, require_postgres):
    engine = create_async_engine(pg_async_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        owner, _, chapters, run = await _seed_run(session)
    transport = QualificationTransport()
    runtime = TimelineWorkerRuntime(
        sessions=sessions,
        gateway=TimelineModelGateway(transport, persistence=PostgresCallRepository(sessions)),
        extraction_deployment=_deployment("balanced-qualified"),
        reconciliation_deployment=_deployment("quality-qualified"),
    )

    report = await run_production_qualification(
        run.id, runtime=runtime, sessions=sessions,
        expected_event_ids=[*(f"event-{chapter.id}" for chapter in chapters), "never-produced"],
        expected_story_order=[f"event-{chapter.id}" for chapter in chapters],
    )

    assert report["status"] == "failed_policy"
    assert report["metrics"]["event_recall"] < 1.0
    assert report["gates"]["quality_thresholds"] is False
    async with sessions.begin() as session:
        persisted_owner = await session.get(User, owner.id)
        await session.delete(persisted_owner)
    await engine.dispose()
