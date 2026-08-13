"""SQLite contract coverage for the public batch and claim seams."""

from __future__ import annotations

import hashlib
import json

import pytest
from sqlalchemy import select

from app.core.security import require_gateway_token
from app.models import Chapter, Novel, User
from app.models.agent_runtime import SkillRun
from app.schemas.agent_runtime import SkillVersionRegister
from app.services.agent_runtime.registry import register_skill_version
from app.services.agent_runtime.structured_output_integrity import (
    canonical_content_hash,
)
from app.services.narrative_memory.builder_contracts import (
    CONTEXT_SUMMARY_MAX_LENGTH,
    CONTINUITY_NOTES_MAX_LENGTH,
    NEXT_HINT_MAX_LENGTH,
    build_chapter_analysis_artifact,
)

pytestmark = pytest.mark.contract


async def _seed(db_session, *, count: int = 10) -> tuple[User, Novel, int]:
    owner = await db_session.scalar(select(User).where(User.username == "testuser"))
    assert owner is not None
    novel = Novel(
        owner_id=owner.id,
        title="chapter-batch-contract",
        status="ready",
        chapter_count=count,
    )
    db_session.add(novel)
    await db_session.flush()
    chapters = [
        Chapter(
            novel_id=novel.id,
            chapter_number=number,
            title=f"第{number}章",
            content=f"正文{number}",
            word_count=3,
        )
        for number in range(1, count + 1)
    ]
    db_session.add_all(chapters)
    await db_session.flush()
    novel.reading_progress = {"chapter_id": chapters[-1].id}
    _, version = await register_skill_version(
        db_session,
        owner_id=owner.id,
        novel_id=novel.id,
        contract=SkillVersionRegister.model_validate(
            {
                "novel_id": novel.id,
                "name": "analyze-chapter",
                "version": "1.0.0",
                "prompt": "Analyze one chapter.",
                "allowed_tools": ["get_chapter"],
                "read_permissions": ["novel:read"],
                "write_permissions": [],
                "forbidden_spaces": ["canon:original"],
                "budget": {"max_calls": 40},
                "approval_required_for": [],
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
            }
        ),
    )
    await db_session.commit()
    return owner, novel, version.id


def _chapter_analysis_envelope(run: SkillRun) -> dict:
    chapter_id = int(run.input["chapter_id"])
    chapter_number = int(run.input["chapter_number"])
    analysis = build_chapter_analysis_artifact(
        chapter_id=chapter_id,
        chapter_number=chapter_number,
        source_snapshot_hash="a" * 64,
        input_hash=canonical_content_hash({"chapter_id": chapter_id}),
        spoiler_policy_version="chapter-batch-test.v1",
        max_length=max(
            CONTEXT_SUMMARY_MAX_LENGTH,
            NEXT_HINT_MAX_LENGTH,
            CONTINUITY_NOTES_MAX_LENGTH,
        ),
        context_payload={"chapter_id": chapter_id},
        chunk_reprs=[{"evidence_key": "evidence:1"}],
        previous_context_summary="仅使用当前 cutoff 内的上下文。",
        next_context_hint=f"继续核对第 {chapter_number} 章内部证据。",
        continuity_notes="chapter-batch contract",
    ).model_dump(mode="json")
    envelope = {
        "type": "chapter_analysis",
        "schema_version": "chapter-analysis.v1",
        "owner_id": run.owner_id,
        "novel_id": run.novel_id,
        "branch": None,
        "producing_skill": "analyze-chapter",
        "producing_skill_version": "1.0.0",
        "skill_version_id": run.skill_version_id,
        "model_lineage": {"provider": "contract", "model": "stub"},
        "source_versions": {"novel": "contract"},
        "input_hash": run.input_hash,
        "evidence_refs": ["evidence:1"],
        "analysis": analysis,
        "tool_runs": [{"tool_name": "get_chapter", "calls": 1}],
        "status": "candidate",
        "parent_revision": None,
    }
    content_hash = canonical_content_hash(envelope)
    envelope["normalization"] = {
        "raw_hash": content_hash,
        "repaired_hash": content_hash,
        "normalization_actions": [],
        "warnings": [],
    }
    return envelope


@pytest.mark.asyncio
async def test_batch_endpoint_handles_ten_real_chapters_and_refills_window(
    auth_client, db_session
):
    _, novel, _ = await _seed(db_session)
    payload = {"chapter_start": 1, "chapter_end": 10, "concurrency_window": 3}

    first = await auth_client.post(
        f"/api/agent/novels/{novel.id}/chapter-batches", json=payload
    )
    assert first.status_code == 202, first.text
    body = first.json()
    assert (body["total"], body["queued"], body["pending"]) == (10, 3, 7)
    assert all(item["status"] == "queued" for item in body["chapters"][:3])
    assert all(item["status"] == "pending" for item in body["chapters"][3:])

    repeated = await auth_client.post(
        f"/api/agent/novels/{novel.id}/chapter-batches", json=payload
    )
    assert repeated.status_code == 202, repeated.text
    assert repeated.json()["batch_id"] == body["batch_id"]
    assert repeated.json()["created_run_ids"] == []

    for run in (
        await db_session.scalars(
            select(SkillRun).where(SkillRun.novel_id == novel.id)
        )
    ).all():
        run.status = "completed"
    await db_session.commit()

    resumed = await auth_client.post(
        f"/api/agent/novels/{novel.id}/chapter-batches", json=payload
    )
    assert resumed.status_code == 202, resumed.text
    assert (resumed.json()["completed"], resumed.json()["queued"]) == (3, 3)


@pytest.mark.asyncio
async def test_successful_finalize_materializes_and_refills_without_polling(
    auth_client, db_session
):
    _, novel, _ = await _seed(db_session, count=100)
    novel_id = novel.id
    created = await auth_client.post(
        f"/api/agent/novels/{novel_id}/chapter-batches",
        json={"chapter_start": 1, "chapter_end": 100, "concurrency_window": 4},
    )
    assert created.status_code == 202, created.text
    assert (created.json()["queued"], created.json()["pending"]) == (4, 96)

    run = await db_session.scalar(
        select(SkillRun)
        .where(SkillRun.novel_id == novel_id, SkillRun.origin == "chapter_batch")
        .order_by(SkillRun.id)
        .limit(1)
    )
    assert run is not None
    finalized = await auth_client.post(
        f"/api/agent/novels/{novel_id}/skill-runs/{run.id}/finalize",
        json={
            "stop_reason": "stop",
            "envelope": _chapter_analysis_envelope(run),
            "model_lineage": {"provider": "contract", "model": "stub"},
            "source_versions": {"novel": "contract"},
            "usage": {},
            "frozen_manifest": {
                "evidence_refs": ["evidence:1"],
                "tool_runs": [{"tool_name": "get_chapter", "calls": 1}],
            },
        },
    )
    assert finalized.status_code == 200, finalized.text
    assert finalized.json()["status"] == "completed"

    db_session.expire_all()
    rows = list(
        (
            await db_session.scalars(
                select(SkillRun)
                .where(SkillRun.novel_id == novel_id, SkillRun.origin == "chapter_batch")
                .order_by(SkillRun.id)
            )
        ).all()
    )
    assert len(rows) == 5
    assert sum(row.status in ("queued", "running") for row in rows) == 4
    completed = next(row for row in rows if row.id == run.id)
    assert completed.status == "completed"
    assert completed.status_reason == "skipped_digest_not_evidence"


@pytest.mark.asyncio
async def test_claim_contract_supports_chapter_batch_and_reader_chat(
    auth_client, db_session, client, monkeypatch
):
    owner, novel, version_id = await _seed(db_session, count=2)
    rows = []
    for origin in ("chapter_batch", "reader_chat"):
        payload = {"novel_id": novel.id, "question": f"分析{origin}"}
        rows.append(
            SkillRun(
                owner_id=owner.id,
                novel_id=novel.id,
                skill_version_id=version_id,
                status="queued",
                input=payload,
                input_hash=hashlib.sha256(
                    json.dumps(payload, sort_keys=True).encode()
                ).hexdigest(),
                frozen_manifest={},
                budget_snapshot={},
                internal_token_hash="a" * 64,
                origin=origin,
            )
        )
    db_session.add_all(rows)
    await db_session.commit()

    async def allow_gateway_token():
        return None

    from app.main import app

    app.dependency_overrides[require_gateway_token] = allow_gateway_token
    try:
        listed = await client.get("/api/agent/queued-runs")
        assert listed.status_code == 200, listed.text
        listed_by_id = {item["run_id"]: item for item in listed.json()["items"]}
        assert {listed_by_id[row.id]["origin"] for row in rows} == {
            "chapter_batch",
            "reader_chat",
        }
        for row in rows:
            claimed = await client.post(f"/api/agent/queued-runs/{row.id}/claim")
            assert claimed.status_code == 200, claimed.text
            assert claimed.json()["origin"] == row.origin
            assert claimed.json()["input"]["question"]
    finally:
        app.dependency_overrides.pop(require_gateway_token, None)
