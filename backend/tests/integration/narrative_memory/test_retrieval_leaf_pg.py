"""PostgreSQL leaf re-slice, Unicode, tamper, and descent integration."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.chunking.manifests import content_hash
from app.services.narrative_memory.candidate_reader import (
    load_eligible_version,
    scope_from_eligible,
)
from app.services.narrative_memory.citations import resolve_citations, validate_proposed_leaf
from app.services.narrative_memory.descent import ProposedLeaf, run_descent
from app.services.narrative_memory.retrieval_contracts import (
    RetrievalRunStatus,
    SafeSourceStatus,
    build_question,
)
from app.services.narrative_memory.retrieval_manifests import build_retrieval_manifest
from app.services.narrative_memory.routing import (
    ROUTING_POLICY_HASH,
    ROUTING_POLICY_VERSION,
    decide_route,
)
from tests.integration.conftest import run_alembic


pytestmark = pytest.mark.integration

HEX_A = "a" * 64


def _seed_unicode_candidate(engine) -> dict:
    """Chapter content with BMP Chinese + astral emoji for code-point slicing."""

    # 4 BMP + 1 astral (😀 is one Python code point) + more
    content = "角色在此😀结束后续不可见"
    ch_hash = content_hash(content)
    # evidence leaf: first 5 code points "角色在此😀"
    leaf_text = content[0:5]
    leaf_hash = content_hash(leaf_text)

    with engine.begin() as conn:
        owner_id = conn.execute(
            text(
                "INSERT INTO users (username,email,hashed_password,is_active,is_superuser) "
                "VALUES ('leaf-o','leaf@example.com','x',true,false) RETURNING id"
            )
        ).scalar_one()
        novel_id = conn.execute(
            text(
                "INSERT INTO novels (owner_id,title,status,chapter_count,word_count) "
                "VALUES (:o,'Leaf Novel','ready',1,20) RETURNING id"
            ),
            {"o": owner_id},
        ).scalar_one()
        chapter_id = conn.execute(
            text(
                "INSERT INTO chapters (novel_id,chapter_number,title,content,word_count) "
                "VALUES (:n,1,'One',:c,20) RETURNING id"
            ),
            {"n": novel_id, "c": content},
        ).scalar_one()
        conn.execute(
            text(
                """
                INSERT INTO chunk_builds (
                    build_id,novel_id,status,source_snapshot_hash,manifest_checksum,
                    chunker_name,chunker_version,chunker_config_hash,collection_name,
                    is_candidate,immutable,changed_chapter_ids,journal,vector_ids
                ) VALUES (
                    'leaf-build',:n,'committed',:snap,:hier,'semantic','1',:snap,'leaf',
                    false,true,'[]','[]','[]'
                )
                """
            ),
            {"n": novel_id, "snap": HEX_A, "hier": HEX_A},
        )
        conn.execute(
            text(
                """
                INSERT INTO chunk_hierarchy_nodes (
                    build_id,novel_id,node_id,level,chapter_id,chapter_number,
                    parent_id,child_ids,content,content_hash,source_start,source_end,
                    chunk_type,decision_lineage,order_index
                ) VALUES
                    ('leaf-build',:n,'leaf-u','evidence',:ch,1,NULL,'[]',:lt,:lh,0,5,
                     'paragraph','[]',0),
                    ('leaf-build',:n,'scene-1','scene',:ch,1,NULL,'[]',:c,:chash,0,:end,
                     'scene','[]',1)
                """
            ),
            {
                "n": novel_id,
                "ch": chapter_id,
                "lt": leaf_text,
                "lh": leaf_hash,
                "c": content,
                "chash": ch_hash,
                "end": len(content),
            },
        )
        version_id = conn.execute(
            text(
                """
                INSERT INTO narrative_memory_versions (
                    owner_id,novel_id,version_key,source_snapshot_hash,
                    hierarchy_build_id,hierarchy_checksum,eligibility_policy_version,
                    eligibility_report_checksum,prompt_hash,schema_hash,model_lineage,
                    decoding_hash,config_hash,policy_hash,optional_source_lineage
                ) VALUES (
                    :o,:n,'leaf-v1',:h,'leaf-build',:h,'audit-v1',:h,:h,:h,'{}',
                    :h,:h,:h,'{}'
                ) RETURNING id
                """
            ),
            {"o": owner_id, "n": novel_id, "h": HEX_A},
        ).scalar_one()
        node_id = conn.execute(
            text(
                """
                INSERT INTO narrative_memory_nodes (
                    owner_id,novel_id,version_id,node_key,node_kind,
                    chapter_start,chapter_end,schema_version,content_checksum,
                    model_lineage_checksum
                ) VALUES (
                    :o,:n,:v,'ch-1','chapter_state',1,1,'v1',:h,:h
                ) RETURNING id
                """
            ),
            {"o": owner_id, "n": novel_id, "v": version_id, "h": HEX_A},
        ).scalar_one()
        claim_id = conn.execute(
            text(
                """
                INSERT INTO narrative_memory_claims (
                    owner_id,novel_id,version_id,node_id,claim_key,claim_kind,
                    schema_version,typed_payload,uncertainty,confidence,
                    visible_from_chapter,claim_checksum,model_lineage_checksum
                ) VALUES (
                    :o,:n,:v,:node,'claim-1','event_fact','v1','{}','certain',1.0,
                    1,:h,:h
                ) RETURNING id
                """
            ),
            {
                "o": owner_id,
                "n": novel_id,
                "v": version_id,
                "node": node_id,
                "h": HEX_A,
            },
        ).scalar_one()
        link_id = conn.execute(
            text(
                """
                INSERT INTO narrative_memory_source_links (
                    owner_id,novel_id,version_id,claim_id,source_kind,
                    hierarchy_build_id,evidence_node_id,chapter_id,chapter_number,
                    source_start,source_end,content_hash,source_snapshot_hash,
                    link_checksum,model_lineage_checksum
                ) VALUES (
                    :o,:n,:v,:c,'hierarchy','leaf-build','leaf-u',:ch,1,0,5,:lh,:h,
                    :h,:h
                ) RETURNING id
                """
            ),
            {
                "o": owner_id,
                "n": novel_id,
                "v": version_id,
                "c": claim_id,
                "ch": chapter_id,
                "lh": leaf_hash,
                "h": HEX_A,
            },
        ).scalar_one()
        conn.execute(
            text(
                """
                INSERT INTO narrative_memory_manifests (
                    owner_id,novel_id,version_id,manifest_schema_version,
                    component_counts,component_hashes,manifest_checksum
                ) VALUES (:o,:n,:v,'v1','{}','{}',:h)
                """
            ),
            {"o": owner_id, "n": novel_id, "v": version_id, "h": HEX_A},
        )
        conn.execute(
            text(
                """
                INSERT INTO narrative_memory_validation_reports (
                    owner_id,novel_id,version_id,manifest_checksum,
                    validator_version,policy_version,verdict,reason_codes,
                    observed_counts,report_checksum
                ) VALUES (
                    :o,:n,:v,:h,'v1','p1','qualified_candidate','[]','{}',:h
                )
                """
            ),
            {"o": owner_id, "n": novel_id, "v": version_id, "h": HEX_A},
        )
        run_id = conn.execute(
            text(
                """
                INSERT INTO narrative_memory_build_runs (
                    owner_id,novel_id,version_id,eligibility_report_checksum,
                    eligibility_policy_version,status,progress,run_policy
                ) VALUES (:o,:n,:v,:h,'audit-v1','completed','{}','{}')
                RETURNING id
                """
            ),
            {"o": owner_id, "n": novel_id, "v": version_id, "h": HEX_A},
        ).scalar_one()
        for kind, key in (
            ("chapter_state", "cs"),
            ("arc_volume_plan", "ap"),
            ("arc_volume_aggregate", "aa"),
            ("global_aggregate", "g"),
            ("manifest_validation", "m"),
        ):
            conn.execute(
                text(
                    """
                    INSERT INTO narrative_memory_build_stages (
                        owner_id,novel_id,version_id,run_id,stage_key,stage_kind,
                        dependency_keys,status,checkpoint,artifact_checksum
                    ) VALUES (:o,:n,:v,:r,:k,:kind,'[]','completed','{}',:h)
                    """
                ),
                {
                    "o": owner_id,
                    "n": novel_id,
                    "v": version_id,
                    "r": run_id,
                    "k": key,
                    "kind": kind,
                    "h": HEX_A,
                },
            )
        conn.execute(
            text(
                """
                INSERT INTO narrative_memory_build_reports (
                    run_id,owner_id,novel_id,version_id,outcome,stage_counts,
                    dependency_closure,call_totals,source_statuses,
                    worker_artifact_checksum,database_manifest_checksum,
                    reason_codes,report_checksum,body
                ) VALUES (
                    :r,:o,:n,:v,'completed_candidate','{}','{}','{}','{}',
                    :h,:h,'[]',:h,'{}'
                )
                """
            ),
            {
                "r": run_id,
                "o": owner_id,
                "n": novel_id,
                "v": version_id,
                "h": HEX_A,
            },
        )

    return {
        "owner_id": owner_id,
        "novel_id": novel_id,
        "version_id": version_id,
        "chapter_id": chapter_id,
        "link_id": link_id,
        "claim_id": claim_id,
        "leaf_text": leaf_text,
        "leaf_hash": leaf_hash,
        "content": content,
    }


@pytest.fixture
async def leaf_pg(empty_postgres: str, pg_async_url: str):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    sync = create_engine(empty_postgres)
    try:
        seed = _seed_unicode_candidate(sync)
    finally:
        sync.dispose()
    engine = create_async_engine(pg_async_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session, seed
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_unicode_re_slice_round_trip(leaf_pg):
    session, seed = leaf_pg
    eligible = await load_eligible_version(
        session,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        version_id=seed["version_id"],
    )
    scope = scope_from_eligible(
        eligible,
        cutoff_chapter=1,
        cutoff_snapshot_hash=HEX_A,
        full_book_authorized=False,
        policy_version=ROUTING_POLICY_VERSION,
        policy_hash=ROUTING_POLICY_HASH,
    )
    leaf = ProposedLeaf(
        hierarchy_build_id="leaf-build",
        evidence_node_id="leaf-u",
        chapter_id=seed["chapter_id"],
        chapter_number=1,
        source_start=0,
        source_end=5,
        content_hash=seed["leaf_hash"],
        source_snapshot_hash=HEX_A,
        link_id=seed["link_id"],
        claim_id=seed["claim_id"],
    )
    citation = await validate_proposed_leaf(session, scope, leaf)
    assert citation is not None
    assert citation.excerpt == seed["leaf_text"]
    assert "😀" in citation.excerpt
    assert content_hash(citation.excerpt) == seed["leaf_hash"]


@pytest.mark.asyncio
async def test_tampered_hash_and_non_evidence_rejected(leaf_pg):
    session, seed = leaf_pg
    eligible = await load_eligible_version(
        session,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        version_id=seed["version_id"],
    )
    scope = scope_from_eligible(
        eligible,
        cutoff_chapter=1,
        cutoff_snapshot_hash=HEX_A,
        full_book_authorized=False,
        policy_version=ROUTING_POLICY_VERSION,
        policy_hash=ROUTING_POLICY_HASH,
    )
    bad_hash = ProposedLeaf(
        hierarchy_build_id="leaf-build",
        evidence_node_id="leaf-u",
        chapter_id=seed["chapter_id"],
        chapter_number=1,
        source_start=0,
        source_end=5,
        content_hash="f" * 64,
        source_snapshot_hash=HEX_A,
        link_id=seed["link_id"],
        claim_id=seed["claim_id"],
    )
    assert await validate_proposed_leaf(session, scope, bad_hash) is None

    scene = ProposedLeaf(
        hierarchy_build_id="leaf-build",
        evidence_node_id="scene-1",
        chapter_id=seed["chapter_id"],
        chapter_number=1,
        source_start=0,
        source_end=len(seed["content"]),
        content_hash=content_hash(seed["content"]),
        source_snapshot_hash=HEX_A,
        link_id=None,
        claim_id=None,
    )
    assert await validate_proposed_leaf(session, scope, scene) is None


@pytest.mark.asyncio
async def test_local_descent_to_validated_citation_and_manifest(leaf_pg):
    session, seed = leaf_pg
    eligible = await load_eligible_version(
        session,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        version_id=seed["version_id"],
    )
    scope = scope_from_eligible(
        eligible,
        cutoff_chapter=1,
        cutoff_snapshot_hash=HEX_A,
        full_book_authorized=False,
        policy_version=ROUTING_POLICY_VERSION,
        policy_hash=ROUTING_POLICY_HASH,
    )
    q = build_question("角色在哪里")
    route = decide_route(q)
    descent = await run_descent(session, scope, route)
    assert descent.proposed_leaves
    outcome = await resolve_citations(session, scope, descent.proposed_leaves)
    assert outcome.citations
    assert outcome.citations[0].excerpt == seed["leaf_text"]
    assert not outcome.blocked

    manifest = build_retrieval_manifest(
        scope=scope,
        question=q,
        route=route,
        traversal=descent.traversal,
        citations=outcome.citations,
        fallback_reason=descent.fallback_reason,
        source_status=SafeSourceStatus.OK,
        run_status=RetrievalRunStatus.COMPLETED,
        omitted_after_budget=descent.omitted_after_budget,
    )
    assert manifest.manifest_checksum
    assert len(manifest.citations) == 1
