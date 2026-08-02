"""Phase 10 reader-chat migration, constraint and cascade authority tests."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests.integration.conftest import run_alembic

pytestmark = pytest.mark.integration

HEX64 = "a" * 64

AUTHORITY_TABLES = {
    "reader_conversations",
    "reader_messages",
    "reader_message_selections",
    "reader_context_manifests",
    "reader_context_evidence_refs",
    "reader_message_citations",
    "reader_generation_jobs",
    "reader_model_call_attempts",
    "reader_budget_ledgers",
    "reader_budget_reservations",
}

DOMAIN_FACT_TABLES = {
    "machine_timeline_events",
    "relationship_observations",
    "character_relations",
    "narrative_units",
}


def test_migration_from_phase09_head_creates_reader_chat_tables(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "11relobserve01", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    with engine.connect() as conn:
        pre = set(inspect(conn).get_table_names())
        assert "relationship_observations" in pre
        assert "reader_conversations" not in pre
    engine.dispose()

    run_alembic("upgrade", "head", database_url=empty_postgres)
    current = run_alembic("current", database_url=empty_postgres)
    out = current.stdout + current.stderr
    assert "27approval01" in out

    engine = create_engine(empty_postgres)
    with engine.connect() as conn:
        names = set(inspect(conn).get_table_names())
        assert AUTHORITY_TABLES <= names
        # Domain tables remain; chat does not replace them
        assert DOMAIN_FACT_TABLES <= names
        for table in AUTHORITY_TABLES:
            count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            assert count == 0

        indexes = {
            idx["name"]
            for table in (
                "reader_messages",
                "reader_generation_jobs",
                "reader_budget_ledgers",
                "reader_conversations",
            )
            for idx in inspect(conn).get_indexes(table)
        }
        assert "uq_reader_messages_conversation_sequence" in indexes or any(
            "sequence" in (n or "") for n in indexes
        )
        # Partial unique for nonterminal jobs
        job_indexes = inspect(conn).get_indexes("reader_generation_jobs")
        assert any(
            idx.get("unique") and "user_message_id" in (idx.get("column_names") or [])
            for idx in job_indexes
        )
    engine.dispose()


def test_alembic_single_head_after_reader_chat(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    heads = run_alembic("heads", database_url=empty_postgres)
    head_lines = [
        line.strip()
        for line in (heads.stdout + heads.stderr).splitlines()
        if line.strip() and not line.strip().startswith("INFO")
    ]
    # Exactly one head revision token
    revision_tokens = [line.split()[0] for line in head_lines if line]
    assert len(revision_tokens) == 1
    assert revision_tokens[0] == "27approval01"


def test_selection_offset_check_and_role_constraints(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_conversation_graph(engine)

    with engine.begin() as conn:
        # Negative/inverted offsets rejected
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    """
                    INSERT INTO reader_message_selections (
                        user_message_id, conversation_id, chapter_id,
                        source_start, source_end, selection_text,
                        selection_text_hash, chapter_content_hash,
                        hierarchy_build_id, hierarchy_checksum
                    ) VALUES (
                        :msg, :conv, :ch, 10, 5, 'bad',
                        :h, :h, 'hb1', :h
                    )
                    """
                ),
                {
                    "msg": ids["user_message_id"],
                    "conv": ids["conversation_id"],
                    "ch": ids["chapter_id"],
                    "h": HEX64,
                },
            )
    # Poisoned transaction recovered via begin() rollback

    with engine.begin() as conn:
        # Invalid message role rejected
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    """
                    INSERT INTO reader_messages (
                        conversation_id, owner_id, novel_id, sequence,
                        role, body, client_message_id
                    ) VALUES (
                        :conv, :owner, :novel, 99, 'system', 'x', 'c-sys'
                    )
                    """
                ),
                {
                    "conv": ids["conversation_id"],
                    "owner": ids["owner_id"],
                    "novel": ids["novel_id"],
                },
            )

    with engine.begin() as conn:
        # Duplicate sequence rejected
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    """
                    INSERT INTO reader_messages (
                        conversation_id, owner_id, novel_id, sequence,
                        role, body, client_message_id
                    ) VALUES (
                        :conv, :owner, :novel, 1, 'user', 'dup', 'c-dup'
                    )
                    """
                ),
                {
                    "conv": ids["conversation_id"],
                    "owner": ids["owner_id"],
                    "novel": ids["novel_id"],
                },
            )

    with engine.begin() as conn:
        # Duplicate client_message_id rejected
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    """
                    INSERT INTO reader_messages (
                        conversation_id, owner_id, novel_id, sequence,
                        role, body, client_message_id
                    ) VALUES (
                        :conv, :owner, :novel, 50, 'user', 'dup-client', 'client-1'
                    )
                    """
                ),
                {
                    "conv": ids["conversation_id"],
                    "owner": ids["owner_id"],
                    "novel": ids["novel_id"],
                },
            )
    engine.dispose()


def test_one_selection_and_manifest_per_user_message(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_conversation_graph(engine)

    with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    """
                    INSERT INTO reader_message_selections (
                        user_message_id, conversation_id, chapter_id,
                        source_start, source_end, selection_text,
                        selection_text_hash, chapter_content_hash,
                        hierarchy_build_id, hierarchy_checksum
                    ) VALUES (
                        :msg, :conv, :ch, 0, 4, 'dup',
                        :h, :h, 'hb1', :h
                    )
                    """
                ),
                {
                    "msg": ids["user_message_id"],
                    "conv": ids["conversation_id"],
                    "ch": ids["chapter_id"],
                    "h": HEX64,
                },
            )

    with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    """
                    INSERT INTO reader_context_manifests (
                        user_message_id, conversation_id, reading_progress_snapshot,
                        full_book, cutoff_chapter_number, hierarchy_build_id,
                        hierarchy_checksum, manifest_checksum, prompt_inputs
                    ) VALUES (
                        :msg, :conv, CAST('{}' AS json), false, 1, 'hb1', :h, :h2,
                        CAST('{}' AS json)
                    )
                    """
                ),
                {
                    "msg": ids["user_message_id"],
                    "conv": ids["conversation_id"],
                    "h": HEX64,
                    "h2": "c" * 64,
                },
            )

    # Assistant message cannot own a selection (role guard)
    with engine.begin() as conn:
        asst_id = conn.execute(
            text(
                """
                INSERT INTO reader_messages (
                    conversation_id, owner_id, novel_id, sequence,
                    role, body, client_message_id, reply_to_message_id
                ) VALUES (
                    :conv, :owner, :novel, 2, 'assistant', 'answer', NULL, :user_msg
                ) RETURNING id
                """
            ),
            {
                "conv": ids["conversation_id"],
                "owner": ids["owner_id"],
                "novel": ids["novel_id"],
                "user_msg": ids["user_message_id"],
            },
        ).scalar_one()
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    """
                    INSERT INTO reader_message_selections (
                        user_message_id, conversation_id, chapter_id,
                        source_start, source_end, selection_text,
                        selection_text_hash, chapter_content_hash,
                        hierarchy_build_id, hierarchy_checksum
                    ) VALUES (
                        :msg, :conv, :ch, 0, 4, 'asst',
                        :h, :h, 'hb1', :h
                    )
                    """
                ),
                {
                    "msg": asst_id,
                    "conv": ids["conversation_id"],
                    "ch": ids["chapter_id"],
                    "h": HEX64,
                },
            )
    engine.dispose()


def test_citation_fk_restricted_to_evidence_refs(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_conversation_graph(engine)

    with engine.begin() as conn:
        asst_id = conn.execute(
            text(
                """
                INSERT INTO reader_messages (
                    conversation_id, owner_id, novel_id, sequence,
                    role, body, reply_to_message_id
                ) VALUES (
                    :conv, :owner, :novel, 2, 'assistant', 'ans', :user_msg
                ) RETURNING id
                """
            ),
            {
                "conv": ids["conversation_id"],
                "owner": ids["owner_id"],
                "novel": ids["novel_id"],
                "user_msg": ids["user_message_id"],
            },
        ).scalar_one()
        # Valid citation to allowlisted evidence ref
        conn.execute(
            text(
                """
                INSERT INTO reader_message_citations (
                    assistant_message_id, block_id, context_evidence_ref_id
                ) VALUES (:asst, 'b1', :ref)
                """
            ),
            {"asst": asst_id, "ref": ids["evidence_ref_id"]},
        )
        # Unknown evidence ref id rejected
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    """
                    INSERT INTO reader_message_citations (
                        assistant_message_id, block_id, context_evidence_ref_id
                    ) VALUES (:asst, 'b2', 999999)
                    """
                ),
                {"asst": asst_id},
            )
    engine.dispose()


def test_one_nonterminal_generation_job_per_user_message(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_conversation_graph(engine)

    with engine.begin() as conn:
        # Seed already created one queued job; second nonterminal must fail
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    """
                    INSERT INTO reader_generation_jobs (
                        conversation_id, owner_id, novel_id, user_message_id,
                        status, cancel_requested, retry_count,
                        prompt_hash, schema_hash, context_manifest_checksum,
                        model_lineage, decoding_hash, config_hash, price_snapshot
                    ) VALUES (
                        :conv, :owner, :novel, :msg,
                        'running', false, 0,
                        :h, :h, :h, CAST('{}' AS json), :h, :h, CAST('{}' AS json)
                    )
                    """
                ),
                {
                    "conv": ids["conversation_id"],
                    "owner": ids["owner_id"],
                    "novel": ids["novel_id"],
                    "msg": ids["user_message_id"],
                    "h": HEX64,
                },
            )

    # Terminal job allows a new nonterminal only after first is terminal
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE reader_generation_jobs SET status = 'completed' WHERE id = :id"
            ),
            {"id": ids["job_id"]},
        )
        # completed is terminal — a new queued job is allowed (retry path)
        new_id = conn.execute(
            text(
                """
                INSERT INTO reader_generation_jobs (
                    conversation_id, owner_id, novel_id, user_message_id,
                    status, cancel_requested, retry_count,
                    prompt_hash, schema_hash, context_manifest_checksum,
                    model_lineage, decoding_hash, config_hash, price_snapshot
                ) VALUES (
                    :conv, :owner, :novel, :msg,
                    'queued', false, 1,
                    :h, :h, :h, CAST('{}' AS json), :h, :h, CAST('{}' AS json)
                ) RETURNING id
                """
            ),
            {
                "conv": ids["conversation_id"],
                "owner": ids["owner_id"],
                "novel": ids["novel_id"],
                "msg": ids["user_message_id"],
                "h": HEX64,
            },
        ).scalar_one()
        assert new_id is not None
    engine.dispose()


def test_conversation_and_novel_budget_ledgers_coexist(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_conversation_graph(engine)

    with engine.begin() as conn:
        conv_ledger = conn.execute(
            text(
                """
                INSERT INTO reader_budget_ledgers (
                    scope_type, owner_id, novel_id, conversation_id,
                    max_calls, max_input_tokens, max_output_tokens, max_cost_usd,
                    reserved_calls, reserved_input_tokens, reserved_output_tokens,
                    reserved_cost_usd, settled_calls, settled_input_tokens,
                    settled_output_tokens, settled_cost_usd
                ) VALUES (
                    'conversation', :owner, :novel, :conv,
                    10, 10000, 4000, 1.0,
                    1, 100, 50, 0.01, 0, 0, 0, 0
                ) RETURNING id
                """
            ),
            {
                "owner": ids["owner_id"],
                "novel": ids["novel_id"],
                "conv": ids["conversation_id"],
            },
        ).scalar_one()
        novel_ledger = conn.execute(
            text(
                """
                INSERT INTO reader_budget_ledgers (
                    scope_type, owner_id, novel_id, conversation_id,
                    max_calls, max_input_tokens, max_output_tokens, max_cost_usd,
                    reserved_calls, reserved_input_tokens, reserved_output_tokens,
                    reserved_cost_usd, settled_calls, settled_input_tokens,
                    settled_output_tokens, settled_cost_usd
                ) VALUES (
                    'novel', :owner, :novel, NULL,
                    100, 100000, 40000, 10.0,
                    1, 100, 50, 0.01, 0, 0, 0, 0
                ) RETURNING id
                """
            ),
            {"owner": ids["owner_id"], "novel": ids["novel_id"]},
        ).scalar_one()
        assert conv_ledger != novel_ledger

    # Duplicate conversation scope rejected in its own transaction so the
    # committed dual ledgers remain available for reservation checks.
    with engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    """
                    INSERT INTO reader_budget_ledgers (
                        scope_type, owner_id, novel_id, conversation_id,
                        max_calls, max_input_tokens, max_output_tokens, max_cost_usd
                    ) VALUES (
                        'conversation', :owner, :novel, :conv, 1, 1, 1, 1.0
                    )
                    """
                ),
                {
                    "owner": ids["owner_id"],
                    "novel": ids["novel_id"],
                    "conv": ids["conversation_id"],
                },
            )

    with engine.begin() as conn:
        # Reservation retains worst-case and settled usage
        res_id = conn.execute(
            text(
                """
                INSERT INTO reader_budget_reservations (
                    ledger_id, reservation_key, status,
                    calls, input_tokens, output_tokens, cost_usd, settled_usage
                ) VALUES (
                    :ledger, 'job-1:attempt-1', 'reserved',
                    1, 100, 50, 0.01, CAST('{}' AS json)
                ) RETURNING id
                """
            ),
            {"ledger": conv_ledger},
        ).scalar_one()
        conn.execute(
            text(
                """
                UPDATE reader_budget_reservations
                SET status = 'settled',
                    settled_usage = CAST(:usage AS json)
                WHERE id = :id
                """
            ),
            {
                "id": res_id,
                "usage": '{"calls":1,"input_tokens":80,"output_tokens":40,"cost_usd":0.008}',
            },
        )
        row = conn.execute(
            text(
                "SELECT status, settled_usage FROM reader_budget_reservations WHERE id = :id"
            ),
            {"id": res_id},
        ).one()
        assert row.status == "settled"
        assert row.settled_usage["input_tokens"] == 80
    engine.dispose()


def test_hard_delete_conversation_cascades_private_content(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_conversation_graph(engine)

    # Attach conversation-scoped ledger + attempt lineage
    with engine.begin() as conn:
        ledger_id = conn.execute(
            text(
                """
                INSERT INTO reader_budget_ledgers (
                    scope_type, owner_id, novel_id, conversation_id,
                    max_calls, max_input_tokens, max_output_tokens, max_cost_usd
                ) VALUES (
                    'conversation', :owner, :novel, :conv, 5, 5000, 2000, 1.0
                ) RETURNING id
                """
            ),
            {
                "owner": ids["owner_id"],
                "novel": ids["novel_id"],
                "conv": ids["conversation_id"],
            },
        ).scalar_one()
        conn.execute(
            text(
                """
                INSERT INTO reader_budget_reservations (
                    ledger_id, reservation_key, status,
                    calls, input_tokens, output_tokens, cost_usd
                ) VALUES (:ledger, 'k1', 'reserved', 1, 10, 5, 0.01)
                """
            ),
            {"ledger": ledger_id},
        )
        conn.execute(
            text(
                """
                INSERT INTO reader_model_call_attempts (
                    generation_job_id, attempt_number, status, request_hash
                ) VALUES (:job, 1, 'started', :h)
                """
            ),
            {"job": ids["job_id"], "h": HEX64},
        )
        asst_id = conn.execute(
            text(
                """
                INSERT INTO reader_messages (
                    conversation_id, owner_id, novel_id, sequence,
                    role, body, reply_to_message_id
                ) VALUES (
                    :conv, :owner, :novel, 2, 'assistant', 'ans', :user_msg
                ) RETURNING id
                """
            ),
            {
                "conv": ids["conversation_id"],
                "owner": ids["owner_id"],
                "novel": ids["novel_id"],
                "user_msg": ids["user_message_id"],
            },
        ).scalar_one()
        conn.execute(
            text(
                """
                INSERT INTO reader_message_citations (
                    assistant_message_id, block_id, context_evidence_ref_id
                ) VALUES (:asst, 'b1', :ref)
                """
            ),
            {"asst": asst_id, "ref": ids["evidence_ref_id"]},
        )

        # Novel-scoped ledger must survive conversation delete
        novel_ledger = conn.execute(
            text(
                """
                INSERT INTO reader_budget_ledgers (
                    scope_type, owner_id, novel_id, conversation_id,
                    max_calls, max_input_tokens, max_output_tokens, max_cost_usd
                ) VALUES (
                    'novel', :owner, :novel, NULL, 50, 50000, 20000, 5.0
                ) RETURNING id
                """
            ),
            {"owner": ids["owner_id"], "novel": ids["novel_id"]},
        ).scalar_one()

        conn.execute(
            text("DELETE FROM reader_conversations WHERE id = :id"),
            {"id": ids["conversation_id"]},
        )

        assert conn.execute(text("SELECT COUNT(*) FROM reader_messages")).scalar() == 0
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM reader_message_selections")
            ).scalar()
            == 0
        )
        assert (
            conn.execute(text("SELECT COUNT(*) FROM reader_context_manifests")).scalar()
            == 0
        )
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM reader_context_evidence_refs")
            ).scalar()
            == 0
        )
        assert (
            conn.execute(text("SELECT COUNT(*) FROM reader_message_citations")).scalar()
            == 0
        )
        assert (
            conn.execute(text("SELECT COUNT(*) FROM reader_generation_jobs")).scalar()
            == 0
        )
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM reader_model_call_attempts")
            ).scalar()
            == 0
        )
        assert (
            conn.execute(
                text(
                    "SELECT COUNT(*) FROM reader_budget_ledgers "
                    "WHERE scope_type = 'conversation'"
                )
            ).scalar()
            == 0
        )
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM reader_budget_ledgers WHERE id = :id"),
                {"id": novel_ledger},
            ).scalar()
            == 1
        )
        # Domain tables untouched
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM novels WHERE id = :id"),
                {"id": ids["novel_id"]},
            ).scalar()
            == 1
        )
        assert "relationship_observations" in set(inspect(conn).get_table_names())
    engine.dispose()


def test_no_chat_fk_into_domain_tables(empty_postgres: str, require_postgres: None):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    with engine.connect() as conn:
        insp = inspect(conn)
        for table in AUTHORITY_TABLES:
            for fk in insp.get_foreign_keys(table):
                referred = fk.get("referred_table")
                assert referred not in DOMAIN_FACT_TABLES, (
                    f"{table} must not FK to domain fact table {referred}"
                )
                # Also forbid FKs that would make chat a parent of domain rows
                assert not str(referred).startswith("machine_timeline")
                assert not str(referred).startswith("relationship_observation")
    engine.dispose()


def test_downgrade_removes_reader_chat_tables(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    run_alembic("downgrade", "11relobserve01", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    with engine.connect() as conn:
        names = set(inspect(conn).get_table_names())
        assert not (AUTHORITY_TABLES & names)
        assert "relationship_observations" in names
    engine.dispose()


def _seed_conversation_graph(engine) -> dict[str, int]:
    """Insert owner/novel/chapter + one user message graph with selection/manifest/job."""
    with engine.begin() as conn:
        owner_id = conn.execute(
            text(
                """
                INSERT INTO users (username, email, hashed_password, is_active, is_superuser)
                VALUES ('chat10user', 'chat10@example.com', 'x', true, false)
                RETURNING id
                """
            )
        ).scalar_one()
        novel_id = conn.execute(
            text(
                """
                INSERT INTO novels (title, author, owner_id, status, chapter_count, word_count)
                VALUES ('Chat Novel', 'Author', :owner_id, 'ready', 1, 20)
                RETURNING id
                """
            ),
            {"owner_id": owner_id},
        ).scalar_one()
        chapter_id = conn.execute(
            text(
                """
                INSERT INTO chapters (novel_id, chapter_number, title, content, word_count)
                VALUES (:novel_id, 1, 'C1', 'The envoy arrives at dawn.', 20)
                RETURNING id
                """
            ),
            {"novel_id": novel_id},
        ).scalar_one()
        conversation_id = conn.execute(
            text(
                """
                INSERT INTO reader_conversations (
                    owner_id, novel_id, title, status, next_sequence
                ) VALUES (:owner, :novel, 'Session chat', 'active', 2)
                RETURNING id
                """
            ),
            {"owner": owner_id, "novel": novel_id},
        ).scalar_one()
        user_message_id = conn.execute(
            text(
                """
                INSERT INTO reader_messages (
                    conversation_id, owner_id, novel_id, sequence,
                    role, body, client_message_id
                ) VALUES (
                    :conv, :owner, :novel, 1, 'user', 'Who is the envoy?', 'client-1'
                ) RETURNING id
                """
            ),
            {
                "conv": conversation_id,
                "owner": owner_id,
                "novel": novel_id,
            },
        ).scalar_one()
        conn.execute(
            text(
                """
                INSERT INTO reader_message_selections (
                    user_message_id, conversation_id, chapter_id,
                    source_start, source_end, selection_text,
                    selection_text_hash, chapter_content_hash,
                    hierarchy_build_id, hierarchy_checksum
                ) VALUES (
                    :msg, :conv, :ch, 0, 10, 'The envoy ',
                    :h, :h, 'hb1', :h
                )
                """
            ),
            {
                "msg": user_message_id,
                "conv": conversation_id,
                "ch": chapter_id,
                "h": HEX64,
            },
        )
        manifest_id = conn.execute(
            text(
                """
                INSERT INTO reader_context_manifests (
                    user_message_id, conversation_id, reading_progress_snapshot,
                    full_book, cutoff_chapter_number, hierarchy_build_id,
                    hierarchy_checksum, manifest_checksum, prompt_inputs
                ) VALUES (
                    :msg, :conv, CAST(:progress AS json), false, 1, 'hb1', :h, :h,
                    CAST('{}' AS json)
                ) RETURNING id
                """
            ),
            {
                "msg": user_message_id,
                "conv": conversation_id,
                "progress": '{"chapter_id": 1, "timeline_full_book": false}',
                "h": HEX64,
            },
        ).scalar_one()
        evidence_ref_id = conn.execute(
            text(
                """
                INSERT INTO reader_context_evidence_refs (
                    manifest_id, evidence_key, source_type, source_id,
                    chapter_id, chapter_number, source_start, source_end,
                    content_hash, excerpt, sort_order, version_lineage
                ) VALUES (
                    :manifest, 'selection:1', 'selection', 'sel-1',
                    :ch, 1, 0, 10, :h, 'The envoy ', 0, CAST('{}' AS json)
                ) RETURNING id
                """
            ),
            {"manifest": manifest_id, "ch": chapter_id, "h": HEX64},
        ).scalar_one()
        job_id = conn.execute(
            text(
                """
                INSERT INTO reader_generation_jobs (
                    conversation_id, owner_id, novel_id, user_message_id,
                    status, cancel_requested, retry_count,
                    prompt_hash, schema_hash, context_manifest_checksum,
                    model_lineage, decoding_hash, config_hash, price_snapshot
                ) VALUES (
                    :conv, :owner, :novel, :msg,
                    'queued', false, 0,
                    :h, :h, :h, CAST('{}' AS json), :h, :h, CAST('{}' AS json)
                ) RETURNING id
                """
            ),
            {
                "conv": conversation_id,
                "owner": owner_id,
                "novel": novel_id,
                "msg": user_message_id,
                "h": HEX64,
            },
        ).scalar_one()
    return {
        "owner_id": owner_id,
        "novel_id": novel_id,
        "chapter_id": chapter_id,
        "conversation_id": conversation_id,
        "user_message_id": user_message_id,
        "manifest_id": manifest_id,
        "evidence_ref_id": evidence_ref_id,
        "job_id": job_id,
    }
