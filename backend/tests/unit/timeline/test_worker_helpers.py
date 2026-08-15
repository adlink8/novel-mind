"""Timeline worker helper functions (transports, prices, reason clipping, reads)."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

import pytest

from app.models.analysis import AnalysisVersion
from app.models.novel import Novel
from app.models.timeline import (
    MachineTimelineEvent,
    TimelineEvidenceRef,
    TimelineParticipant,
)
from app.models.user import User
from app.services.timeline.model_gateway import ModelDeployment
from app.services.timeline.worker import (
    _LiteLLMTransport,
    _clip_status_reason,
    _load_persisted_candidates,
    _prices,
    production_runtime,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_clip_status_reason_limits_length_and_cleans_whitespace():
    assert _clip_status_reason(None) is None
    assert _clip_status_reason("ok") == "ok"
    assert _clip_status_reason("multi\nline") == "multi line"
    long_reason = "x" * 200
    clipped = _clip_status_reason(long_reason)
    assert len(clipped) == 128
    assert clipped.endswith("…")


def test_prices_snapshots_deployment():
    deployment = ModelDeployment(
        provider="openai",
        model_id="gpt-x",
        revision="rev-1",
        supports_structured_output=True,
        input_price_per_million=Decimal("1.5"),
        output_price_per_million=Decimal("3"),
    )
    prices = _prices(deployment)
    assert prices == {
        "provider": "openai",
        "model_id": "gpt-x",
        "revision": "rev-1",
        "input_price_per_million": "1.5",
        "output_price_per_million": "3",
    }


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_litellm_transport_normalizes_usage():
    transport = _LiteLLMTransport()
    message = SimpleNamespace(content="litellm answer")
    usage = SimpleNamespace(
        model_dump=lambda: {"prompt_tokens": 5, "completion_tokens": 2}
    )
    response = SimpleNamespace(
        id="ll-1", usage=usage, choices=[SimpleNamespace(message=message)]
    )
    with mock.patch(
        "litellm.acompletion", new=mock.AsyncMock(return_value=response)
    ) as acompl:
        out = await transport.complete(model="gpt-x", messages=[], temperature=0.1)
    assert out["content"] == "litellm answer"
    assert out["usage"]["prompt_tokens"] == 5
    assert acompl.call_args.kwargs["temperature"] == 0.1


@pytest.mark.asyncio
async def test_litellm_transport_plain_usage_namespace():
    transport = _LiteLLMTransport()
    message = SimpleNamespace(content="x")
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
    response = SimpleNamespace(
        id="ll-2", usage=usage, choices=[SimpleNamespace(message=message)]
    )
    with mock.patch("litellm.acompletion", new=mock.AsyncMock(return_value=response)):
        out = await transport.complete(model="gpt-x", messages=[])
    # usage without model_dump is passed through as-is (SimpleNamespace)
    assert out["usage"].prompt_tokens == 1
    assert out["usage"].completion_tokens == 1


# ---------------------------------------------------------------------------
# production_runtime
# ---------------------------------------------------------------------------


def test_production_runtime_uses_configured_litellm_provider(monkeypatch):
    monkeypatch.setattr("app.config.settings.chat_provider", "gemini")
    monkeypatch.setattr("app.config.settings.default_chat_model", "gemini-2.5-flash")
    runtime = production_runtime()
    assert runtime.extraction_deployment.provider == "gemini"
    assert runtime.extraction_deployment.model_id == "gemini-2.5-flash"
    assert isinstance(runtime.gateway.transport, _LiteLLMTransport)
    assert runtime.extraction_prompt


# ---------------------------------------------------------------------------
# _load_persisted_candidates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_persisted_candidates_rebuilds_events(db_session):
    from app.models.novel import Chapter

    owner = User(
        username="wm-worker", email="wm-worker@example.com", hashed_password="x"
    )
    db_session.add(owner)
    await db_session.flush()
    novel = Novel(owner_id=owner.id, title="工人书", status="ready")
    db_session.add(novel)
    await db_session.flush()
    chapter = Chapter(
        novel_id=novel.id, chapter_number=1, title="第一章", content="正文"
    )
    db_session.add(chapter)
    await db_session.flush()
    version = AnalysisVersion(
        owner_id=owner.id,
        novel_id=novel.id,
        version_key="worker-v1",
        status="candidate",
        source_snapshot_hash="a" * 64,
        hierarchy_build_id="build-1",
        hierarchy_checksum="b" * 64,
        prompt_hash="c" * 64,
        schema_hash="d" * 64,
        model_lineage={},
        decoding_hash="e" * 64,
        config_hash="f" * 64,
        price_snapshot={},
        manifest={},
    )
    db_session.add(version)
    await db_session.flush()
    event = MachineTimelineEvent(
        version_id=version.id,
        owner_id=owner.id,
        novel_id=novel.id,
        logical_event_id="1:e1",
        title="事件",
        description="描述",
        event_type="plot",
        time_precision="unknown",
        time_expression=None,
        narrative_chapter_number=1,
        narrative_index=0,
        story_rank=None,
        story_constraints=[],
        confidence=0.9,
        prompt_hash="c" * 64,
        schema_hash="d" * 64,
        model_lineage={"stage": "chapter_extract"},
        publication_status="provisional",
    )
    db_session.add(event)
    await db_session.flush()
    db_session.add(
        TimelineParticipant(event_id=event.id, entity_id=None, mention="阿宁")
    )
    db_session.add(
        TimelineEvidenceRef(
            event_id=event.id,
            chapter_id=chapter.id,
            evidence_id="ev-1",
            source_start=0,
            source_end=1,
            content_hash="0" * 64,
        )
    )
    await db_session.commit()

    candidates = await _load_persisted_candidates(db_session, version.id)
    assert len(candidates) == 1
    assert candidates[0].candidate_id == "1:e1"
    assert candidates[0].participants[0].mention == "阿宁"
    assert candidates[0].evidence[0].evidence_id == "ev-1"
    assert candidates[0].story_time.precision.value == "unknown"
