"""Adversarial future-metadata, tenant/version, and citation-tamper safety."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.narrative_memory.candidate_reader import (
    clear_visible_cache,
    load_eligible_version,
    load_visible_set_for_route,
    peek_cache_public,
    scope_from_eligible,
)
from app.services.narrative_memory.citations import resolve_citations
from app.services.narrative_memory.descent import ProposedLeaf, run_descent
from app.services.narrative_memory.experiments import (
    experiment_request_from_fixture,
    run_retrieval_experiment,
    sanitize_public_report,
)
from app.services.narrative_memory.retrieval_contracts import build_question
from app.services.narrative_memory.routing import (
    ROUTING_POLICY_HASH,
    ROUTING_POLICY_VERSION,
    decide_route,
)
from tests.integration.conftest import run_alembic
from tests.integration.narrative_memory.test_retrieval_candidates_pg import (
    HEX_A,
    HEX_B,
    _seed_eligible_candidate,
)


pytestmark = pytest.mark.integration


@pytest.fixture
async def adv_pg(empty_postgres: str, pg_async_url: str):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    sync = create_engine(empty_postgres)
    try:
        seed = _seed_eligible_candidate(sync)
    finally:
        sync.dispose()
    engine = create_async_engine(pg_async_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    clear_visible_cache()
    try:
        async with factory() as session:
            yield session, seed
            await session.rollback()
    finally:
        clear_visible_cache()
        await engine.dispose()


def _public_blob(report: dict) -> str:
    return sanitize_public_report(report)


@pytest.mark.asyncio
async def test_future_metadata_zero_leakage_on_full_experiment(adv_pg):
    session, seed = adv_pg
    req = experiment_request_from_fixture(
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        version_id=seed["version_id"],
        raw_question="跨章因果是什么",
        cutoff_chapter=2,
        cutoff_snapshot_hash=HEX_A,
        expected_manifest_checksum=HEX_A,
    )
    a = await run_retrieval_experiment(session, req, enabled=True)
    b = await run_retrieval_experiment(session, req, enabled=True)
    assert _public_blob(a.report) == _public_blob(b.report)
    blob = _public_blob(a.report)
    for token in (
        "FUTURE",
        "FOREIGN",
        "arc-fut",
        "ch-3",
        "claim-future",
        "FUTURE_GLOBAL_TITLE",
        "FUTURE_ARC_TITLE",
        "hidden_future",
        "display_label",
    ):
        assert token not in blob


@pytest.mark.asyncio
async def test_cross_tenant_version_idor_fails(adv_pg):
    session, seed = adv_pg
    req = experiment_request_from_fixture(
        owner_id=seed["other_owner"],
        novel_id=seed["novel_id"],
        version_id=seed["version_id"],
        raw_question="角色在哪里",
        cutoff_chapter=2,
        cutoff_snapshot_hash=HEX_A,
    )
    result = await run_retrieval_experiment(session, req, enabled=True)
    assert result.status.value == "blocked"
    assert result.report.get("blocked_reason") == "candidate_ineligible"


@pytest.mark.asyncio
async def test_cache_identity_not_replayable_across_cutoff(adv_pg):
    session, seed = adv_pg
    eligible = await load_eligible_version(
        session,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        version_id=seed["version_id"],
    )
    scope_a = scope_from_eligible(
        eligible,
        cutoff_chapter=2,
        cutoff_snapshot_hash=HEX_A,
        full_book_authorized=False,
        policy_version=ROUTING_POLICY_VERSION,
        policy_hash=ROUTING_POLICY_HASH,
    )
    scope_b = scope_from_eligible(
        eligible,
        cutoff_chapter=1,
        cutoff_snapshot_hash=HEX_B,
        full_book_authorized=False,
        policy_version=ROUTING_POLICY_VERSION,
        policy_hash=ROUTING_POLICY_HASH,
    )
    q = build_question("角色状态")
    route = decide_route(q)
    clear_visible_cache()
    va = await load_visible_set_for_route(session, scope_a, route, q)
    vb = await load_visible_set_for_route(session, scope_b, route, q)
    assert va.cache is not None and vb.cache is not None
    assert va.cache.identity_hash != vb.cache.identity_hash
    # peek by wrong identity returns nothing useful for other scope
    assert peek_cache_public(vb.cache.identity_hash) is not None
    # public payload never includes raw key field name
    pub = peek_cache_public(va.cache.identity_hash)
    assert pub is not None
    assert "identity_hash" not in json.dumps(pub)


@pytest.mark.asyncio
async def test_corrupt_leaf_lineage_fails_closed(adv_pg):
    session, seed = adv_pg
    eligible = await load_eligible_version(
        session,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        version_id=seed["version_id"],
    )
    scope = scope_from_eligible(
        eligible,
        cutoff_chapter=2,
        cutoff_snapshot_hash=HEX_A,
        full_book_authorized=False,
        policy_version=ROUTING_POLICY_VERSION,
        policy_hash=ROUTING_POLICY_HASH,
    )
    corrupt = [
        ProposedLeaf(
            hierarchy_build_id="ret-build-1",
            evidence_node_id="leaf-1",
            chapter_id=seed["chapter_ids"][0],
            chapter_number=1,
            source_start=0,
            source_end=8,
            content_hash="f" * 64,
            source_snapshot_hash=HEX_A,
            link_id=None,
            claim_id=None,
        ),
        ProposedLeaf(
            hierarchy_build_id="wrong-build",
            evidence_node_id="leaf-1",
            chapter_id=seed["chapter_ids"][0],
            chapter_number=1,
            source_start=0,
            source_end=8,
            content_hash=HEX_A,
            source_snapshot_hash=HEX_A,
        ),
        ProposedLeaf(
            hierarchy_build_id="ret-build-1",
            evidence_node_id="leaf-1",
            chapter_id=seed["chapter_ids"][0],
            chapter_number=3,  # future chapter number
            source_start=0,
            source_end=8,
            content_hash=HEX_A,
            source_snapshot_hash=HEX_A,
        ),
    ]
    outcome = await resolve_citations(
        session, scope, corrupt, require_minimum=1
    )
    assert outcome.citations == ()
    assert outcome.blocked is True
    assert outcome.dropped == 3


@pytest.mark.asyncio
async def test_descent_never_observes_future_arc_title(adv_pg):
    session, seed = adv_pg
    eligible = await load_eligible_version(
        session,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        version_id=seed["version_id"],
    )
    scope = scope_from_eligible(
        eligible,
        cutoff_chapter=2,
        cutoff_snapshot_hash=HEX_A,
        full_book_authorized=False,
        policy_version=ROUTING_POLICY_VERSION,
        policy_hash=ROUTING_POLICY_HASH,
    )
    route = decide_route(build_question("跨章因果是什么"))
    result = await run_descent(session, scope, route)
    serialized = json.dumps(
        [s.model_dump(mode="json") for s in result.traversal],
        ensure_ascii=False,
    )
    assert "FUTURE_ARC" not in serialized
    assert "FUTURE_GLOBAL" not in serialized
