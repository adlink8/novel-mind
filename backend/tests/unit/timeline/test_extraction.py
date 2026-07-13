"""Evidence-bound chapter extraction and exact-cache contracts."""

from decimal import Decimal

import pytest

from app.services.timeline.budget import BudgetGate, BudgetPolicy
from app.services.timeline.evidence import EvidencePackage, EvidenceUnit
from app.services.timeline.extraction import ExactCacheKey, InMemoryExtractionStore, TimelineChapterExtractor
from app.services.timeline.model_gateway import ModelDeployment, TimelineModelGateway

pytestmark = pytest.mark.unit


class FakeTransport:
    def __init__(self, content):
        self.content = content
        self.calls = 0

    async def complete(self, **_):
        self.calls += 1
        return {"content": self.content, "usage": {"input_tokens": 20, "output_tokens": 10}}


def package():
    text = "At dawn, Mira opened the western gate."
    return EvidencePackage.create(
        owner_id=3, novel_id=8, chapter_id=5, unit_id="scene-5-1",
        source_snapshot_hash="1" * 64, hierarchy_build_id="build-8",
        hierarchy_checksum="2" * 64,
        units=[EvidenceUnit.create("ev-1", 0, len(text), text)],
    )


def output(p):
    unit = p.units[0]
    return """{"events":[{"candidate_id":"c5:e1","title":"Mira opens the gate","description":"Mira opens the western gate at dawn.","event_type":"plot","narrative_chapter_number":5,"narrative_index":0,"participants":[{"mention":"Mira","entity_id":null}],"story_time":{"precision":"fuzzy","expression":"At dawn"},"evidence":[{"chapter_id":5,"evidence_id":"ev-1","source_start":0,"source_end":%d,"content_hash":"%s"}],"confidence":0.9}],"story_time_constraints":[]}""" % (unit.source_end, unit.content_hash)


def extractor(transport, store):
    deployment = ModelDeployment("openai", "balanced-test", "r1", True, Decimal("1"), Decimal("1"))
    budget = BudgetGate(BudgetPolicy(3, 10_000, 2_000, Decimal("1")))
    return TimelineChapterExtractor(
        TimelineModelGateway(transport), store, deployment=deployment, budget=budget,
        prompt="extract only evidence-backed events", prompt_hash="3" * 64,
        schema_hash="4" * 64, decoding_hash="5" * 64, config_hash="6" * 64,
    )


@pytest.mark.asyncio
async def test_valid_candidate_is_automatically_published_as_partial():
    p = package()
    transport = FakeTransport(output(p))
    store = InMemoryExtractionStore()
    result = await extractor(transport, store).extract(run_id=1, version_id=2, package=p)
    assert transport.calls == 1
    assert result.events[0].publication_status == "provisional"
    assert result.events[0].owner_id == 3 and result.events[0].novel_id == 8
    assert store.published[2][0].candidate.candidate_id == "c5:e1"
    assert store.audits[0].status == "succeeded"
    assert store.audits[0].gateway_attempt.cost_usd == Decimal("0.000030")


@pytest.mark.asyncio
async def test_exact_cache_hit_skips_provider_but_writes_lineage_audit():
    p = package()
    transport = FakeTransport(output(p))
    store = InMemoryExtractionStore()
    service = extractor(transport, store)
    first = await service.extract(run_id=1, version_id=2, package=p)
    second = await service.extract(run_id=2, version_id=3, package=p)
    assert transport.calls == 1
    assert second.cache_hit is True
    assert store.audits[-1].status == "call-skipped"
    assert store.audits[-1].cache_source_attempt_id == first.source_attempt_id
    assert store.audits[-1].artifact_checksum == first.artifact_checksum


def test_cache_key_contains_every_frozen_identity_component():
    key = ExactCacheKey.for_package(
        package(), stage="chapter_extract", prompt_hash="3" * 64, schema_hash="4" * 64,
        model_provider="openai", model_id="balanced-test", model_revision="r1",
        decoding_hash="5" * 64, config_hash="6" * 64,
    )
    assert list(key.as_tuple()) == [
        "chapter_extract", "1" * 64, "build-8", "2" * 64, "scene-5-1",
        package().package_hash, "3" * 64, "4" * 64, "openai", "balanced-test", "r1",
        "5" * 64, "6" * 64,
    ]


@pytest.mark.asyncio
async def test_invalid_output_is_not_cached_or_published():
    p = package()
    transport = FakeTransport('{"events":[],"unexpected":true}')
    store = InMemoryExtractionStore()
    with pytest.raises(Exception):
        await extractor(transport, store).extract(run_id=1, version_id=2, package=p)
    assert store.cache == {} and store.published == {}
