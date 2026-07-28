"""PostgreSQL visible-set loaders: cutoff-first, explicit version, cache isolation."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.narrative_memory.candidate_reader import (
    UnsealedCandidateError,
    clear_visible_cache,
    load_eligible_version,
    load_visible_nodes,
    load_visible_set_for_route,
    scope_from_eligible,
)
from app.services.narrative_memory.retrieval_contracts import (
    RetrievalBudgets,
    build_question,
    canonical_retrieval_json,
)
from app.services.narrative_memory.routing import (
    ROUTING_POLICY_HASH,
    ROUTING_POLICY_VERSION,
    decide_route,
)
from tests.integration.conftest import run_alembic


pytestmark = pytest.mark.integration

HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64


def _seed_eligible_candidate(engine, *, cutoff_chapters: int = 2) -> dict:
    """Seed a sealed, build-complete candidate with visible + future rows."""

    with engine.begin() as conn:
        owner_id = conn.execute(
            text(
                "INSERT INTO users (username,email,hashed_password,is_active,is_superuser) "
                "VALUES ('ret-owner','ret@example.com','x',true,false) RETURNING id"
            )
        ).scalar_one()
        other_owner = conn.execute(
            text(
                "INSERT INTO users (username,email,hashed_password,is_active,is_superuser) "
                "VALUES ('ret-other','ret-o@example.com','x',true,false) RETURNING id"
            )
        ).scalar_one()
        novel_id = conn.execute(
            text(
                "INSERT INTO novels (owner_id,title,status,chapter_count,word_count) "
                "VALUES (:o,'Ret Novel','ready',3,30) RETURNING id"
            ),
            {"o": owner_id},
        ).scalar_one()
        other_novel = conn.execute(
            text(
                "INSERT INTO novels (owner_id,title,status,chapter_count,word_count) "
                "VALUES (:o,'Other','ready',1,1) RETURNING id"
            ),
            {"o": other_owner},
        ).scalar_one()

        ch_ids = []
        for n in (1, 2, 3):
            ch_ids.append(
                conn.execute(
                    text(
                        "INSERT INTO chapters (novel_id,chapter_number,title,content,word_count) "
                        "VALUES (:n,:num,:t,:c,10) RETURNING id"
                    ),
                    {
                        "n": novel_id,
                        "num": n,
                        "t": f"Ch{n}",
                        "c": f"chapter{n}text",
                    },
                ).scalar_one()
            )

        conn.execute(
            text(
                """
                INSERT INTO chunk_builds (
                    build_id,novel_id,status,source_snapshot_hash,manifest_checksum,
                    chunker_name,chunker_version,chunker_config_hash,collection_name,
                    is_candidate,immutable,changed_chapter_ids,journal,vector_ids
                ) VALUES (
                    'ret-build-1',:n,'committed',:h,:h,'semantic','1',:h,'ret',
                    false,true,'[]','[]','[]'
                )
                """
            ),
            {"n": novel_id, "h": HEX_A},
        )
        for i, ch_id in enumerate(ch_ids, start=1):
            conn.execute(
                text(
                    """
                    INSERT INTO chunk_hierarchy_nodes (
                        build_id,novel_id,node_id,level,chapter_id,chapter_number,
                        parent_id,child_ids,content,content_hash,source_start,source_end,
                        chunk_type,decision_lineage,order_index
                    ) VALUES (
                        'ret-build-1',:n,:nid,'evidence',:ch,:num,NULL,'[]',:c,:h,0,8,
                        'paragraph','[]',0
                    )
                    """
                ),
                {
                    "n": novel_id,
                    "nid": f"leaf-{i}",
                    "ch": ch_id,
                    "num": i,
                    "c": f"chapter{i}",
                    "h": HEX_A,
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
                    :o,:n,'ret-v1',:h,'ret-build-1',:h,'audit-v1',:h,:h,:h,'{}',
                    :h,:h,:h,'{}'
                ) RETURNING id
                """
            ),
            {"o": owner_id, "n": novel_id, "h": HEX_A},
        ).scalar_one()

        def add_node(
            key: str, kind: str, start: int, end: int, label: str | None = None
        ) -> int:
            return conn.execute(
                text(
                    """
                    INSERT INTO narrative_memory_nodes (
                        owner_id,novel_id,version_id,node_key,node_kind,
                        chapter_start,chapter_end,schema_version,content_checksum,
                        model_lineage_checksum,display_label
                    ) VALUES (
                        :o,:n,:v,:k,:kind,:s,:e,'v1',:h,:h,:label
                    ) RETURNING id
                    """
                ),
                {
                    "o": owner_id,
                    "n": novel_id,
                    "v": version_id,
                    "k": key,
                    "kind": kind,
                    "s": start,
                    "e": end,
                    "h": HEX_A,
                    "label": label,
                },
            ).scalar_one()

        global_id = add_node("global", "global_story", 1, 3, "FUTURE_GLOBAL_TITLE")
        arc_visible = add_node("arc-vis", "story_arc", 1, 2, "Visible Arc")
        arc_future = add_node("arc-fut", "story_arc", 2, 3, "FUTURE_ARC_TITLE")
        vol_vis = add_node("vol-vis", "volume", 1, 2, "Visible Vol")
        ch1 = add_node("ch-1", "chapter_state", 1, 1, "Ch1 state")
        ch2 = add_node("ch-2", "chapter_state", 2, 2, "Ch2 state")
        ch3 = add_node("ch-3", "chapter_state", 3, 3, "FUTURE_CH3")

        def add_claim(node_id: int, key: str, vis: int, conf: float = 0.9) -> int:
            return conn.execute(
                text(
                    """
                    INSERT INTO narrative_memory_claims (
                        owner_id,novel_id,version_id,node_id,claim_key,claim_kind,
                        schema_version,typed_payload,uncertainty,confidence,
                        visible_from_chapter,claim_checksum,model_lineage_checksum
                    ) VALUES (
                        :o,:n,:v,:node,:k,'event_fact','v1','{}','certain',:conf,
                        :vis,:h,:h
                    ) RETURNING id
                    """
                ),
                {
                    "o": owner_id,
                    "n": novel_id,
                    "v": version_id,
                    "node": node_id,
                    "k": key,
                    "vis": vis,
                    "conf": conf,
                    "h": HEX_A,
                },
            ).scalar_one()

        claim_ch1 = add_claim(ch1, "claim-ch1", 1)
        claim_ch2 = add_claim(ch2, "claim-ch2", 2)
        claim_future = add_claim(ch3, "claim-future", 3, conf=0.99)
        claim_arc = add_claim(arc_visible, "claim-arc", 2)

        def add_edge(src: int, tgt: int) -> int:
            return conn.execute(
                text(
                    """
                    INSERT INTO narrative_memory_edges (
                        owner_id,novel_id,version_id,source_node_id,target_node_id,
                        edge_type,edge_checksum,model_lineage_checksum
                    ) VALUES (:o,:n,:v,:s,:t,'contains',:h,:h) RETURNING id
                    """
                ),
                {
                    "o": owner_id,
                    "n": novel_id,
                    "v": version_id,
                    "s": src,
                    "t": tgt,
                    "h": HEX_A,
                },
            ).scalar_one()

        add_edge(global_id, arc_visible)
        add_edge(global_id, arc_future)
        add_edge(arc_visible, ch1)
        add_edge(arc_visible, ch2)
        add_edge(arc_future, ch2)
        add_edge(arc_future, ch3)
        add_edge(vol_vis, ch1)

        def add_link(claim_id: int, leaf: str, chapter_id: int, num: int) -> int:
            return conn.execute(
                text(
                    """
                    INSERT INTO narrative_memory_source_links (
                        owner_id,novel_id,version_id,claim_id,source_kind,
                        hierarchy_build_id,evidence_node_id,chapter_id,chapter_number,
                        source_start,source_end,content_hash,source_snapshot_hash,
                        link_checksum,model_lineage_checksum
                    ) VALUES (
                        :o,:n,:v,:c,'hierarchy','ret-build-1',:leaf,:ch,:num,
                        0,8,:h,:h,:h,:h
                    ) RETURNING id
                    """
                ),
                {
                    "o": owner_id,
                    "n": novel_id,
                    "v": version_id,
                    "c": claim_id,
                    "leaf": leaf,
                    "ch": chapter_id,
                    "num": num,
                    "h": HEX_A,
                },
            ).scalar_one()

        add_link(claim_ch1, "leaf-1", ch_ids[0], 1)
        add_link(claim_ch2, "leaf-2", ch_ids[1], 2)
        add_link(claim_future, "leaf-3", ch_ids[2], 3)
        add_link(claim_arc, "leaf-2", ch_ids[1], 2)

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
                    :o,:n,:v,:h,'validator-v1','policy-v1','qualified_candidate',
                    '[]','{}',:h
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
            ("chapter_state", "chapter_state:all"),
            ("arc_volume_plan", "arc_plan"),
            ("arc_volume_aggregate", "arc_agg"),
            ("global_aggregate", "global"),
            ("manifest_validation", "manifest"),
        ):
            conn.execute(
                text(
                    """
                    INSERT INTO narrative_memory_build_stages (
                        owner_id,novel_id,version_id,run_id,stage_key,stage_kind,
                        dependency_keys,status,checkpoint,artifact_checksum
                    ) VALUES (
                        :o,:n,:v,:r,:k,:kind,'[]','completed','{}',:h
                    )
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

        # Foreign-scope adversary rows (should never appear)
        foreign_version = conn.execute(
            text(
                """
                INSERT INTO narrative_memory_versions (
                    owner_id,novel_id,version_key,source_snapshot_hash,
                    hierarchy_build_id,hierarchy_checksum,eligibility_policy_version,
                    eligibility_report_checksum,prompt_hash,schema_hash,model_lineage,
                    decoding_hash,config_hash,policy_hash,optional_source_lineage
                ) VALUES (
                    :o,:n,'foreign-v',:h,'ret-build-1',:h,'audit-v1',:h,:h,:h,'{}',
                    :h,:h,:h,'{}'
                ) RETURNING id
                """
            ),
            {"o": other_owner, "n": other_novel, "h": HEX_B},
        ).scalar_one()
        conn.execute(
            text(
                """
                INSERT INTO narrative_memory_nodes (
                    owner_id,novel_id,version_id,node_key,node_kind,
                    chapter_start,chapter_end,schema_version,content_checksum,
                    model_lineage_checksum,display_label
                ) VALUES (
                    :o,:n,:v,'foreign-global','global_story',1,1,'v1',:h,:h,
                    'FOREIGN_TITLE'
                )
                """
            ),
            {
                "o": other_owner,
                "n": other_novel,
                "v": foreign_version,
                "h": HEX_B,
            },
        )

    return {
        "owner_id": owner_id,
        "other_owner": other_owner,
        "novel_id": novel_id,
        "other_novel": other_novel,
        "version_id": version_id,
        "chapter_ids": ch_ids,
        "ids": {
            "global": global_id,
            "arc_visible": arc_visible,
            "arc_future": arc_future,
            "vol_vis": vol_vis,
            "ch1": ch1,
            "ch2": ch2,
            "ch3": ch3,
            "claim_ch1": claim_ch1,
            "claim_ch2": claim_ch2,
            "claim_future": claim_future,
            "claim_arc": claim_arc,
        },
        "cutoff_chapters": cutoff_chapters,
    }


@pytest.fixture
async def ret_pg(empty_postgres: str, pg_async_url: str):
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


@pytest.mark.asyncio
async def test_eligible_version_requires_seal_and_complete_build(ret_pg):
    session, seed = ret_pg
    eligible = await load_eligible_version(
        session,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        version_id=seed["version_id"],
        expected_manifest_checksum=HEX_A,
    )
    assert eligible.source_status.value == "ok"
    assert eligible.manifest.manifest_checksum == HEX_A

    with pytest.raises(Exception):
        await load_eligible_version(
            session,
            owner_id=seed["other_owner"],
            novel_id=seed["novel_id"],
            version_id=seed["version_id"],
        )


@pytest.mark.asyncio
async def test_cutoff_first_filters_future_nodes_before_counts(ret_pg):
    session, seed = ret_pg
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
    nodes, omitted = await load_visible_nodes(session, scope)
    keys = {n.node_key for n in nodes}
    assert "ch-1" in keys and "ch-2" in keys
    assert "ch-3" not in keys
    assert "arc-fut" not in keys
    assert "global" not in keys  # chapter_end=3 > cutoff 2
    assert "arc-vis" in keys
    assert all(n.chapter_end <= 2 for n in nodes)
    # labels of future nodes must not appear in any serializable candidate field
    public = json.dumps([n.node_key for n in nodes], ensure_ascii=False, sort_keys=True)
    assert "FUTURE" not in public
    assert omitted == 0


@pytest.mark.asyncio
async def test_distinct_routes_load_distinct_start_levels(ret_pg):
    session, seed = ret_pg
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

    q_local = build_question(
        "这章角色是谁", selected_chapter=1, selected_start=0, selected_end=2
    )
    q_arc = build_question("跨章因果是什么")
    q_mixed = build_question("随便问问剧情")

    local_route = decide_route(q_local)
    arc_route = decide_route(q_arc)
    mixed_route = decide_route(q_mixed)

    local_set = await load_visible_set_for_route(session, scope, local_route, q_local)
    arc_set = await load_visible_set_for_route(session, scope, arc_route, q_arc)
    mixed_set = await load_visible_set_for_route(session, scope, mixed_route, q_mixed)

    local_kinds = {n.node_kind.value for n in local_set.nodes if n.node_kind}
    arc_kinds = {n.node_kind.value for n in arc_set.nodes if n.node_kind}
    mixed_kinds = {n.node_kind.value for n in mixed_set.nodes if n.node_kind}

    assert local_kinds == {"chapter_state"}
    assert "story_arc" in arc_kinds or "volume" in arc_kinds
    assert "chapter_state" not in arc_kinds
    assert "chapter_state" in mixed_kinds
    assert local_set.public_counts() != arc_set.public_counts() or {
        c.stable_key for c in local_set.nodes
    } != {c.stable_key for c in arc_set.nodes}


@pytest.mark.asyncio
async def test_future_and_foreign_rows_do_not_affect_serialized_output(ret_pg):
    """Seed already contains future/foreign rows; re-query must be byte-identical
    and must never surface future labels/keys or foreign-scope identities.
    """

    session, seed = ret_pg
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
        budgets=RetrievalBudgets(
            max_nodes=32, max_claims=64, max_leaves=16, max_depth=6, max_fanout=8
        ),
    )
    q = build_question("角色状态如何")
    route = decide_route(q)

    def serialize(visible):
        return json.dumps(
            {
                "counts": visible.public_counts(),
                "nodes": [c.model_dump(mode="json") for c in visible.nodes],
                "claims": [c.model_dump(mode="json") for c in visible.claims],
                "links": [c.model_dump(mode="json") for c in visible.source_links],
                "cache": visible.cache.model_dump(mode="json")
                if visible.cache
                else None,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    clear_visible_cache()
    before = await load_visible_set_for_route(session, scope, route, q)
    before_json = serialize(before)
    clear_visible_cache()
    after = await load_visible_set_for_route(session, scope, route, q)
    after_json = serialize(after)
    assert before_json == after_json

    # Future / foreign metadata already present in DB must not leak.
    assert "FUTURE" not in after_json
    assert "FOREIGN" not in after_json
    assert "arc-fut" not in after_json
    assert "ch-3" not in after_json
    assert "claim-future" not in after_json
    assert "foreign-global" not in after_json
    # VisibleCandidate forbids display_label / confidence / titles
    for node in after.nodes:
        dumped = node.model_dump(mode="json")
        assert "display_label" not in dumped
        assert "confidence" not in dumped
        assert "title" not in dumped


@pytest.mark.asyncio
async def test_incomplete_unsealed_fail_closed(empty_postgres: str, pg_async_url: str):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    sync = create_engine(empty_postgres)
    try:
        with sync.begin() as conn:
            owner_id = conn.execute(
                text(
                    "INSERT INTO users (username,email,hashed_password,is_active,is_superuser) "
                    "VALUES ('u2','u2@example.com','x',true,false) RETURNING id"
                )
            ).scalar_one()
            novel_id = conn.execute(
                text(
                    "INSERT INTO novels (owner_id,title,status,chapter_count,word_count) "
                    "VALUES (:o,'N','ready',0,0) RETURNING id"
                ),
                {"o": owner_id},
            ).scalar_one()
            conn.execute(
                text(
                    """
                    INSERT INTO chunk_builds (
                        build_id,novel_id,status,source_snapshot_hash,manifest_checksum,
                        chunker_name,chunker_version,chunker_config_hash,collection_name,
                        is_candidate,immutable,changed_chapter_ids,journal,vector_ids
                    ) VALUES (
                        'b2',:n,'committed',:h,:h,'semantic','1',:h,'c',false,true,
                        '[]','[]','[]'
                    )
                    """
                ),
                {"n": novel_id, "h": HEX_A},
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
                        :o,:n,'v-unsealed',:h,'b2',:h,'p',:h,:h,:h,'{}',:h,:h,:h,'{}'
                    ) RETURNING id
                    """
                ),
                {"o": owner_id, "n": novel_id, "h": HEX_A},
            ).scalar_one()
    finally:
        sync.dispose()

    engine = create_async_engine(pg_async_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            with pytest.raises(UnsealedCandidateError):
                await load_eligible_version(
                    session,
                    owner_id=owner_id,
                    novel_id=novel_id,
                    version_id=version_id,
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cache_identity_includes_scope_and_cutoff(ret_pg):
    session, seed = ret_pg
    eligible = await load_eligible_version(
        session,
        owner_id=seed["owner_id"],
        novel_id=seed["novel_id"],
        version_id=seed["version_id"],
    )
    scope1 = scope_from_eligible(
        eligible,
        cutoff_chapter=2,
        cutoff_snapshot_hash=HEX_A,
        full_book_authorized=False,
        policy_version=ROUTING_POLICY_VERSION,
        policy_hash=ROUTING_POLICY_HASH,
    )
    scope2 = scope_from_eligible(
        eligible,
        cutoff_chapter=1,
        cutoff_snapshot_hash=HEX_B,
        full_book_authorized=False,
        policy_version=ROUTING_POLICY_VERSION,
        policy_hash=ROUTING_POLICY_HASH,
    )
    q = build_question("角色在哪里")
    route = decide_route(q)
    clear_visible_cache()
    a = await load_visible_set_for_route(session, scope1, route, q)
    b = await load_visible_set_for_route(session, scope2, route, q)
    assert a.cache is not None and b.cache is not None
    assert a.cache.identity_hash != b.cache.identity_hash
    assert "raw" not in canonical_retrieval_json(a.cache)
    # no cache_key field
    dumped = a.cache.model_dump(mode="json")
    assert "cache_key" not in dumped
    assert set(dumped.keys()) == {
        "identity_hash",
        "scope_hash",
        "route_hash",
        "query_hash",
        "budget_hash",
        "source_status",
    }
