"""Canonical retrieval manifest and safe-trace tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.narrative_memory.citations import cannot_build_from_summary
from app.services.narrative_memory.retrieval_contracts import (
    CutoffSnapshot,
    FallbackReasonCode,
    LeafCitation,
    RetrievalBudgets,
    RetrievalRunStatus,
    RetrievalScope,
    RouteDecision,
    RouteMode,
    RouteReasonCode,
    SafeSourceStatus,
    StartLevel,
    TraversalStep,
    build_question,
    canonical_retrieval_json,
)
from app.services.narrative_memory.retrieval_manifests import (
    build_retrieval_manifest,
    build_safe_trace,
    manifest_is_leak_free,
)
from app.services.narrative_memory.routing import (
    ROUTING_POLICY_HASH,
    ROUTING_POLICY_VERSION,
)


pytestmark = pytest.mark.unit

HEX = "a" * 64


def _scope() -> RetrievalScope:
    return RetrievalScope(
        owner_id=1,
        novel_id=2,
        version_id=3,
        source_snapshot_hash=HEX,
        hierarchy_build_id="build-1",
        hierarchy_checksum=HEX,
        candidate_manifest_checksum=HEX,
        cutoff=CutoffSnapshot(
            through_chapter=2, full_book_authorized=False, snapshot_hash=HEX
        ),
        policy_version=ROUTING_POLICY_VERSION,
        policy_hash=ROUTING_POLICY_HASH,
        budgets=RetrievalBudgets(),
    )


def _route() -> RouteDecision:
    return RouteDecision(
        mode=RouteMode.LOCAL,
        start_levels=(StartLevel.CHAPTER_STATE,),
        reason_codes=(RouteReasonCode.LOCAL_FACT_INTENT,),
        policy_version=ROUTING_POLICY_VERSION,
        policy_hash=ROUTING_POLICY_HASH,
    )


def _citation(**kw) -> LeafCitation:
    base = dict(
        chapter_id=1,
        chapter_number=1,
        evidence_node_id="leaf-1",
        hierarchy_build_id="build-1",
        source_start=0,
        source_end=3,
        content_hash=HEX,
        excerpt="abc",
        source_snapshot_hash=HEX,
        link_id=9,
        claim_id=8,
    )
    base.update(kw)
    return LeafCitation(**base)  # type: ignore[arg-type]


def test_manifest_checksum_stable_and_sensitive():
    scope = _scope()
    q = build_question("角色在哪里")
    route = _route()
    step = TraversalStep(
        level="chapter_state",
        candidate_key="start:local",
        parent_key=None,
        relation="start",
        visible_candidate_count=1,
        omitted_after_budget=0,
        outcome="admitted",
    )
    m1 = build_retrieval_manifest(
        scope=scope,
        question=q,
        route=route,
        traversal=[step],
        citations=[_citation()],
        fallback_reason=FallbackReasonCode.NONE,
        source_status=SafeSourceStatus.OK,
        run_status=RetrievalRunStatus.COMPLETED,
        omitted_after_budget=0,
    )
    m2 = build_retrieval_manifest(
        scope=scope,
        question=q,
        route=route,
        traversal=[step],
        citations=[_citation()],
        fallback_reason=FallbackReasonCode.NONE,
        source_status=SafeSourceStatus.OK,
        run_status=RetrievalRunStatus.COMPLETED,
        omitted_after_budget=0,
    )
    assert m1.manifest_checksum == m2.manifest_checksum
    assert canonical_retrieval_json(m1) == canonical_retrieval_json(m2)

    m3 = build_retrieval_manifest(
        scope=scope,
        question=q,
        route=route,
        traversal=[step],
        citations=[_citation(source_end=4, excerpt="abcd")],
        fallback_reason=FallbackReasonCode.NONE,
        source_status=SafeSourceStatus.OK,
        run_status=RetrievalRunStatus.COMPLETED,
        omitted_after_budget=0,
    )
    assert m3.manifest_checksum != m1.manifest_checksum


def test_manifest_and_trace_are_leak_free():
    scope = _scope()
    q = build_question("全书主题")
    route = _route()
    m = build_retrieval_manifest(
        scope=scope,
        question=q,
        route=route,
        traversal=[],
        citations=[],
        fallback_reason=FallbackReasonCode.NO_ANSWER,
        source_status=SafeSourceStatus.ABSENT,
        run_status=RetrievalRunStatus.BLOCKED,
        omitted_after_budget=0,
    )
    assert manifest_is_leak_free(m)
    text = canonical_retrieval_json(m)
    assert "display_label" not in text
    assert "cache_key" not in text
    assert q.normalized_text not in text  # raw/normalized question not embedded

    trace = build_safe_trace(
        route=route,
        source_status=SafeSourceStatus.OK,
        fallback_reason=FallbackReasonCode.NONE,
        visible_node_count=1,
        visible_claim_count=1,
        visible_leaf_count=1,
        omitted_after_budget=0,
        traversal=[],
        run_status=RetrievalRunStatus.COMPLETED,
    )
    with pytest.raises(ValidationError):
        type(trace).model_validate(
            {**trace.model_dump(mode="json"), "hidden_future_count": 1}
        )


def test_summary_cannot_construct_citation():
    with pytest.raises(Exception):
        cannot_build_from_summary("this is an upper summary")


def test_leaf_citation_requires_excerpt_from_re_slice_fields():
    with pytest.raises(ValidationError):
        LeafCitation(
            chapter_id=1,
            chapter_number=1,
            evidence_node_id="x",
            hierarchy_build_id="b",
            source_start=0,
            source_end=1,
            content_hash=HEX,
            excerpt="",
            source_snapshot_hash=HEX,
        )
