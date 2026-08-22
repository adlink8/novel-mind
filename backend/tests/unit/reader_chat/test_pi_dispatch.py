from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.agent_runtime.reader_bridge import evaluate_backfill_recovery
from app.services.reader_chat import worker


pytestmark = pytest.mark.unit


def test_backfill_recovery_public_seam_distinguishes_ready_waiting_failure_and_timeout():
    now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    ready = evaluate_backfill_recovery(
        required_dimensions=("raw_text", "world_projection"),
        runs=[
            {
                "backfill_dimension": "raw_text",
                "status": "completed",
                "status_reason": "materialized:scene_candidate",
                "updated_at": now,
            },
            {
                "backfill_dimension": "world_projection",
                "status": "completed",
                "status_reason": "materialized:world_model_candidate",
                "updated_at": now,
            },
        ],
        now=now,
    )
    waiting = evaluate_backfill_recovery(
        required_dimensions=("raw_text",),
        runs=[
            {
                "backfill_dimension": "raw_text",
                "status": "running",
                "status_reason": None,
                "updated_at": now,
            }
        ],
        now=now,
    )
    failed = evaluate_backfill_recovery(
        required_dimensions=("raw_text",),
        runs=[
            {
                "backfill_dimension": "raw_text",
                "status": "failed",
                "status_reason": "worker_error",
                "updated_at": now,
            }
        ],
        now=now,
    )
    timed_out = evaluate_backfill_recovery(
        required_dimensions=("raw_text",),
        runs=[
            {
                "backfill_dimension": "raw_text",
                "status": "queued",
                "status_reason": None,
                "updated_at": now - timedelta(minutes=31),
            }
        ],
        now=now,
    )

    assert ready.state == "ready"
    assert waiting.state == "waiting"
    assert failed.state == "failed"
    assert failed.reason == "backfill_failed"
    assert timed_out.state == "failed"
    assert timed_out.reason == "backfill_timeout"


@pytest.mark.asyncio
async def test_reader_job_dispatch_uses_pi_skill_run_seam_not_gateway(monkeypatch):
    dispatched: list[int] = []

    async def fake_enqueue(job_id: int) -> None:
        dispatched.append(job_id)

    async def gateway_path_must_not_run(job_id: int):
        raise AssertionError("ordinary reader questions must not construct a gateway")

    monkeypatch.setattr(
        worker, "enqueue_reader_chat_skill_run", fake_enqueue, raising=False
    )
    monkeypatch.setattr(worker, "production_runtime", gateway_path_must_not_run)

    await worker.dispatch_reader_chat_job(41)

    assert dispatched == [41]
