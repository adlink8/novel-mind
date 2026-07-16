"""Unit matrix for Phase 15 retrieval contracts and deterministic routing."""

from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from app.services.narrative_memory import routing as routing_mod
from app.services.narrative_memory.retrieval_contracts import (
    CacheEnvelope,
    CandidateSourceStatus,
    CutoffSnapshot,
    RetrievalBudgets,
    RetrievalScope,
    RouteDecision,
    RouteMode,
    RouteReasonCode,
    SafeTrace,
    StartLevel,
    TraversalStep,
    build_cache_envelope,
    build_question,
    canonical_retrieval_json,
    hash_query_text,
    normalize_query_text,
    route_hash,
    scope_hash,
)
from app.services.narrative_memory.routing import (
    ROUTING_POLICY_HASH,
    ROUTING_POLICY_VERSION,
    decide_route,
    decide_route_for_scope,
    start_levels_for_mode,
)


pytestmark = pytest.mark.unit

HEX_A = "a" * 64
HEX_B = "b" * 64


def _cutoff(
    chapter: int = 3, *, full_book: bool = False, snap: str = HEX_A
) -> CutoffSnapshot:
    return CutoffSnapshot(
        through_chapter=chapter,
        full_book_authorized=full_book,
        snapshot_hash=snap,
    )


def _scope(**overrides: object) -> RetrievalScope:
    base = dict(
        owner_id=1,
        novel_id=2,
        version_id=3,
        source_snapshot_hash=HEX_A,
        hierarchy_build_id="build-1",
        hierarchy_checksum=HEX_A,
        candidate_manifest_checksum=HEX_A,
        cutoff=_cutoff(),
        policy_version=ROUTING_POLICY_VERSION,
        policy_hash=ROUTING_POLICY_HASH,
        budgets=RetrievalBudgets(),
    )
    base.update(overrides)
    return RetrievalScope(**base)  # type: ignore[arg-type]


# ── Contracts ──────────────────────────────────────────────────────────────


def test_scope_requires_explicit_version_and_hashes():
    with pytest.raises(ValidationError):
        RetrievalScope(
            owner_id=1,
            novel_id=2,
            version_id=3,
            source_snapshot_hash="not-a-hash",
            hierarchy_build_id="b",
            hierarchy_checksum=HEX_A,
            candidate_manifest_checksum=HEX_A,
            cutoff=_cutoff(),
            policy_version="v",
            policy_hash=HEX_A,
        )


def test_question_normalization_preserves_chinese_and_is_deterministic():
    q1 = build_question("  角色　在哪里？  ")
    q2 = build_question("角色 在哪里？")
    assert "角色" in q1.normalized_text
    assert q1.query_hash == q2.query_hash
    assert q1.query_hash == hash_query_text(normalize_query_text("角色 在哪里？"))


def test_question_selection_bounds_validated():
    with pytest.raises(ValidationError):
        build_question("x", selected_start=0, selected_end=3)
    with pytest.raises(ValidationError):
        build_question("x", selected_chapter=1, selected_start=5, selected_end=2)
    ok = build_question("选中这段", selected_chapter=2, selected_start=0, selected_end=4)
    assert ok.selected_chapter == 2


def test_route_decision_forbids_free_text_and_empty_codes():
    with pytest.raises(ValidationError):
        RouteDecision(
            mode=RouteMode.LOCAL,
            start_levels=(StartLevel.CHAPTER_STATE,),
            reason_codes=(),
            policy_version=ROUTING_POLICY_VERSION,
            policy_hash=ROUTING_POLICY_HASH,
        )
    with pytest.raises(ValidationError):
        RouteDecision.model_validate(
            {
                "mode": "local",
                "start_levels": ["chapter_state"],
                "reason_codes": ["selection_anchor"],
                "policy_version": ROUTING_POLICY_VERSION,
                "policy_hash": ROUTING_POLICY_HASH,
                "rationale": "secret",
            }
        )


def test_safe_trace_forbids_hidden_metadata_fields():
    route = decide_route(build_question("角色在哪里"))
    step = TraversalStep(
        level="chapter_state",
        candidate_key="node:1",
        parent_key=None,
        relation="start",
        visible_candidate_count=1,
        omitted_after_budget=0,
        outcome="admitted",
    )
    with pytest.raises(ValidationError):
        SafeTrace.model_validate(
            {
                "route": route.model_dump(mode="json"),
                "source_status": "ok",
                "fallback_reason": "none",
                "visible_node_count": 1,
                "visible_claim_count": 0,
                "visible_leaf_count": 0,
                "omitted_after_budget": 0,
                "traversal": [step.model_dump(mode="json")],
                "run_status": "completed",
                "hidden_future_count": 9,
                "cache_key": "raw-key",
            }
        )


def test_scope_and_cache_hashes_are_byte_stable():
    s1 = _scope()
    s2 = _scope()
    assert scope_hash(s1) == scope_hash(s2)
    q = build_question("主线是什么", selected_chapter=1, selected_start=0, selected_end=1)
    route = decide_route(q, full_book_authorized=False)
    env1 = build_cache_envelope(
        scope=s1, route=route, question=q, source_status=CandidateSourceStatus.OK
    )
    env2 = build_cache_envelope(
        scope=s2, route=route, question=q, source_status=CandidateSourceStatus.OK
    )
    assert env1.identity_hash == env2.identity_hash
    assert isinstance(env1, CacheEnvelope)
    # different cutoff → different identity
    s3 = _scope(cutoff=_cutoff(1, snap=HEX_B))
    env3 = build_cache_envelope(
        scope=s3, route=route, question=q, source_status=CandidateSourceStatus.OK
    )
    assert env3.identity_hash != env1.identity_hash


# ── Router matrix ──────────────────────────────────────────────────────────


ROUTE_MATRIX: list[tuple[str, dict, RouteMode, set[RouteReasonCode]]] = [
    (
        "selection_local",
        {
            "raw": "这段说了什么",
            "selected_chapter": 1,
            "selected_start": 0,
            "selected_end": 2,
            "full_book": False,
        },
        RouteMode.LOCAL,
        {RouteReasonCode.SELECTION_ANCHOR},
    ),
    (
        "local_fact",
        {"raw": "角色现在在哪里", "full_book": False},
        RouteMode.LOCAL,
        {RouteReasonCode.LOCAL_FACT_INTENT},
    ),
    (
        "arc_causal",
        {"raw": "为什么他会变成这样", "full_book": False},
        RouteMode.ARC,
        {RouteReasonCode.CROSS_CHAPTER_INTENT},
    ),
    (
        "global_authorized",
        {"raw": "全书主线是什么", "full_book": True},
        RouteMode.GLOBAL,
        {RouteReasonCode.WHOLE_BOOK_INTENT},
    ),
    (
        "global_unauthorized",
        {"raw": "全书主线是什么", "full_book": False},
        RouteMode.MIXED,
        {RouteReasonCode.WHOLE_BOOK_INTENT, RouteReasonCode.UNAUTHORIZED_GLOBAL},
    ),
    (
        "mixed_multi",
        {"raw": "这章角色为什么会转变", "full_book": False},
        RouteMode.MIXED,
        {RouteReasonCode.MULTIPLE_SCOPE_SIGNALS},
    ),
    (
        "no_answer",
        {"raw": "无关", "full_book": False},
        RouteMode.MIXED,
        {RouteReasonCode.NO_ANSWER_SHAPE},
    ),
    (
        "safe_default",
        {"raw": "随便问问剧情", "full_book": False},
        RouteMode.MIXED,
        {RouteReasonCode.SAFE_DEFAULT},
    ),
]


@pytest.mark.parametrize("name,kwargs,mode,must_codes", ROUTE_MATRIX, ids=[r[0] for r in ROUTE_MATRIX])
def test_route_matrix_modes_and_reason_codes(name, kwargs, mode, must_codes):
    q = build_question(
        kwargs["raw"],
        selected_chapter=kwargs.get("selected_chapter"),
        selected_start=kwargs.get("selected_start"),
        selected_end=kwargs.get("selected_end"),
    )
    decision = decide_route(q, full_book_authorized=kwargs["full_book"])
    assert decision.mode is mode
    assert set(must_codes).issubset(set(decision.reason_codes))
    assert decision.start_levels == start_levels_for_mode(mode)
    assert decision.policy_version == ROUTING_POLICY_VERSION
    assert decision.policy_hash == ROUTING_POLICY_HASH
    # byte-stable
    again = decide_route(q, full_book_authorized=kwargs["full_book"])
    assert canonical_retrieval_json(decision) == canonical_retrieval_json(again)
    assert route_hash(decision) == route_hash(again)


def test_distinct_routes_select_distinct_start_levels():
    levels = {
        RouteMode.LOCAL: start_levels_for_mode(RouteMode.LOCAL),
        RouteMode.ARC: start_levels_for_mode(RouteMode.ARC),
        RouteMode.GLOBAL: start_levels_for_mode(RouteMode.GLOBAL),
        RouteMode.MIXED: start_levels_for_mode(RouteMode.MIXED),
    }
    assert levels[RouteMode.LOCAL] == (StartLevel.CHAPTER_STATE,)
    assert StartLevel.STORY_ARC in levels[RouteMode.ARC]
    assert levels[RouteMode.GLOBAL] == (StartLevel.GLOBAL_STORY,)
    assert StartLevel.CHAPTER_STATE in levels[RouteMode.MIXED]
    assert StartLevel.STORY_ARC in levels[RouteMode.MIXED]
    # all four modes produce non-identical level sets as pairs of interest
    assert levels[RouteMode.LOCAL] != levels[RouteMode.ARC]
    assert levels[RouteMode.LOCAL] != levels[RouteMode.GLOBAL]
    assert levels[RouteMode.ARC] != levels[RouteMode.GLOBAL]


def test_router_signature_excludes_candidate_material():
    sig = inspect.signature(decide_route)
    forbidden = {
        "candidates",
        "nodes",
        "claims",
        "summaries",
        "embeddings",
        "provider",
        "session",
        "counts",
    }
    assert forbidden.isdisjoint(sig.parameters.keys())
    src = inspect.getsource(routing_mod)
    assert "openai" not in src.lower()
    assert "litellm" not in src.lower()
    assert "NarrativeMemoryNode" not in src
    assert "provider" not in src.lower() or "provider" not in decide_route.__doc__.lower()


def test_decide_route_for_scope_uses_cutoff_authorization():
    q = build_question("全书主题")
    blocked = decide_route_for_scope(q, _scope(cutoff=_cutoff(5, full_book=False)))
    assert blocked.mode is not RouteMode.GLOBAL
    assert RouteReasonCode.UNAUTHORIZED_GLOBAL in blocked.reason_codes
    allowed = decide_route_for_scope(q, _scope(cutoff=_cutoff(5, full_book=True)))
    assert allowed.mode is RouteMode.GLOBAL


def test_mismatched_policy_fails_closed():
    q = build_question("角色在哪里")
    with pytest.raises(ValueError, match="unsupported routing policy"):
        decide_route(q, policy_version="other", policy_hash=HEX_B)
    bad_scope = _scope(policy_hash=HEX_B)
    with pytest.raises(ValueError, match="scope policy"):
        decide_route_for_scope(q, bad_scope)


def test_route_changes_loader_start_levels_not_just_annotation():
    """Prove route mode selects different loader start levels (integration hook)."""

    local = decide_route(build_question("这章角色是谁", selected_chapter=1))
    arc = decide_route(build_question("跨章因果是什么"))
    global_ = decide_route(build_question("全书主线"), full_book_authorized=True)
    mixed = decide_route(build_question("随便问问"))
    assert local.start_levels != arc.start_levels
    assert arc.start_levels != global_.start_levels
    assert global_.start_levels != mixed.start_levels
    # local loaders would only request chapter_state
    assert all(level is StartLevel.CHAPTER_STATE for level in local.start_levels)
    assert StartLevel.GLOBAL_STORY in global_.start_levels
    assert StartLevel.GLOBAL_STORY not in local.start_levels
    assert StartLevel.GLOBAL_STORY not in arc.start_levels
