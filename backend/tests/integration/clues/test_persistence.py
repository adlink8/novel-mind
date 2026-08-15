"""Phase 11 clue authority: migration, append-only history, CAS pointer."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models.clue import (
    ClueActivePointer,
    ClueAnalysisRun,
    ClueAnalysisVersion,
    ClueBudgetLedger,
    ClueBudgetReservation,
    ClueEvidenceRef,
    ClueLifecycleEvent,
    ClueLink,
    ClueModelCallAttempt,
    ClueOverride,
    CluePointerJournal,
    MachineClue,
)
from app.schemas.clue import (
    ClueLifecycleState,
    LifecycleEventInput,
    replay_lifecycle,
)
from tests.integration.conftest import run_alembic

pytestmark = pytest.mark.integration

HEX64 = "a" * 64
HEX64_B = "b" * 64
HEX64_C = "c" * 64

AUTHORITY_TABLES = {
    "clue_analysis_versions",
    "clue_analysis_runs",
    "machine_clues",
    "clue_lifecycle_events",
    "clue_evidence_refs",
    "clue_links",
    "clue_overrides",
    "clue_budget_ledgers",
    "clue_budget_reservations",
    "clue_model_call_attempts",
    "clue_active_pointers",
    "clue_pointer_journal",
}

APPEND_ONLY_TABLES = {
    "clue_lifecycle_events",
    "clue_overrides",
    "clue_pointer_journal",
}


# ---------------------------------------------------------------------------
# Contract tests (no live DB)
# ---------------------------------------------------------------------------


def test_contract_metadata_contains_clue_authority_tables():
    tables = set(ClueLifecycleEvent.metadata.tables)
    assert AUTHORITY_TABLES <= tables
    # Adjacent domains remain separate.
    assert "reader_messages" in tables
    assert "relationship_observations" in tables
    assert "timeline_active_pointers" in tables


def test_contract_twelve_orm_classes_and_tablenames():
    classes = [
        ClueAnalysisVersion,
        ClueAnalysisRun,
        MachineClue,
        ClueEvidenceRef,
        ClueLifecycleEvent,
        ClueLink,
        ClueOverride,
        ClueBudgetLedger,
        ClueBudgetReservation,
        ClueModelCallAttempt,
        ClueActivePointer,
        CluePointerJournal,
    ]
    assert len(classes) == 12
    assert ClueLifecycleEvent.__tablename__ == "clue_lifecycle_events"
    assert MachineClue.__tablename__ == "machine_clues"
    assert ClueActivePointer.__tablename__ == "clue_active_pointers"


def test_contract_lifecycle_columns_and_pointer_cas():
    event_cols = set(inspect(ClueLifecycleEvent).columns.keys())
    assert {
        "owner_id",
        "novel_id",
        "version_id",
        "logical_clue_id",
        "from_status",
        "to_status",
        "actor_source",
        "event_key",
        "evidence_identities",
        "cue_chapter",
        "payoff_chapter",
    } <= event_cols

    pointer_cols = set(inspect(ClueActivePointer).columns.keys())
    assert {"version_id", "revision", "manifest_checksum"} <= pointer_cols

    override_cols = set(inspect(ClueOverride).columns.keys())
    assert {"supersedes_id", "status", "needs_relink", "action"} <= override_cols


def test_contract_link_exactly_one_target_check_present():
    names = {c.name for c in ClueLink.__table__.constraints if hasattr(c, "name")}
    assert "ck_clue_links_exactly_one_target" in names
    assert "ck_clue_lifecycle_legal_pair" in {
        c.name for c in ClueLifecycleEvent.__table__.constraints if hasattr(c, "name")
    }


def test_contract_no_mutable_current_status_on_machine_clue():
    cols = set(inspect(MachineClue).columns.keys())
    assert "current_status" not in cols
    assert "lifecycle_state" not in cols


# ---------------------------------------------------------------------------
# PostgreSQL migration + behavior
# ---------------------------------------------------------------------------


def _seed_scope(engine):
    """Minimal owner/novel/chapter graph for clue inserts."""
    with engine.begin() as conn:
        owner_id = conn.execute(
            text(
                """
                INSERT INTO users (username, email, hashed_password, is_active, is_superuser)
                VALUES ('clue11user', 'clue11@example.com', 'x', true, false)
                RETURNING id
                """
            )
        ).scalar_one()
        novel_id = conn.execute(
            text(
                """
                INSERT INTO novels (title, author, owner_id, status, chapter_count, word_count)
                VALUES ('Clue Novel', 'Author', :owner_id, 'ready', 2, 48)
                RETURNING id
                """
            ),
            {"owner_id": owner_id},
        ).scalar_one()
        chapter_id = conn.execute(
            text(
                """
                INSERT INTO chapters (novel_id, chapter_number, title, content, word_count)
                VALUES (:novel_id, 1, 'Ch1', 'seal breaks', 12)
                RETURNING id
                """
            ),
            {"novel_id": novel_id},
        ).scalar_one()
        chapter5_id = conn.execute(
            text(
                """
                INSERT INTO chapters (novel_id, chapter_number, title, content, word_count)
                VALUES (:novel_id, 5, 'Ch5', 'seal revealed', 12)
                RETURNING id
                """
            ),
            {"novel_id": novel_id},
        ).scalar_one()

        version_id = conn.execute(
            text(
                """
                INSERT INTO clue_analysis_versions (
                    owner_id, novel_id, version_key, status,
                    source_snapshot_hash, hierarchy_build_id, hierarchy_checksum,
                    prompt_hash, schema_hash, decoding_hash, config_hash, policy_hash,
                    model_lineage, price_snapshot, manifest
                ) VALUES (
                    :owner_id, :novel_id, 'v1', 'validated',
                    :h, 'build-1', :h, :h, :h, :h, :h, :h,
                    CAST('{}' AS json), CAST('{}' AS json), CAST('{}' AS json)
                ) RETURNING id
                """
            ),
            {"owner_id": owner_id, "novel_id": novel_id, "h": HEX64},
        ).scalar_one()

        machine_clue_id = conn.execute(
            text(
                """
                INSERT INTO machine_clues (
                    owner_id, novel_id, version_id, logical_clue_id, title, summary,
                    package_hash, package_snapshot, confidence, publication_status,
                    first_cue_chapter, first_cue_source_start
                ) VALUES (
                    :owner_id, :novel_id, :version_id, 'clue-seal', 'Broken seal', '',
                    :h, CAST('{}' AS json), 0.9, 'published', 1, 0
                ) RETURNING id
                """
            ),
            {
                "owner_id": owner_id,
                "novel_id": novel_id,
                "version_id": version_id,
                "h": HEX64,
            },
        ).scalar_one()

    return {
        "owner_id": owner_id,
        "novel_id": novel_id,
        "chapter_id": chapter_id,
        "chapter5_id": chapter5_id,
        "version_id": version_id,
        "machine_clue_id": machine_clue_id,
    }


def _insert_lifecycle(
    conn,
    ids,
    *,
    from_status: str,
    to_status: str,
    event_key: str,
    evidence_identities: list[str] | None = None,
    cue_chapter=None,
    cue_source_start=None,
    payoff_chapter=None,
    payoff_source_start=None,
    actor_source: str = "machine",
):
    return conn.execute(
        text(
            """
            INSERT INTO clue_lifecycle_events (
                owner_id, novel_id, version_id, logical_clue_id, machine_clue_id,
                from_status, to_status, actor_source, reason, event_key,
                evidence_identities, cue_chapter, cue_source_start,
                payoff_chapter, payoff_source_start, gate_audit
            ) VALUES (
                :owner_id, :novel_id, :version_id, 'clue-seal', :machine_clue_id,
                :from_status, :to_status, :actor_source, :reason, :event_key,
                CAST(:evidence AS json), :cue_chapter, :cue_source_start,
                :payoff_chapter, :payoff_source_start, CAST('{}' AS json)
            ) RETURNING id
            """
        ),
        {
            "owner_id": ids["owner_id"],
            "novel_id": ids["novel_id"],
            "version_id": ids["version_id"],
            "machine_clue_id": ids["machine_clue_id"],
            "from_status": from_status,
            "to_status": to_status,
            "actor_source": actor_source,
            "reason": f"{from_status}->{to_status}",
            "event_key": event_key,
            "evidence": json.dumps(evidence_identities or []),
            "cue_chapter": cue_chapter,
            "cue_source_start": cue_source_start,
            "payoff_chapter": payoff_chapter,
            "payoff_source_start": payoff_source_start,
        },
    ).scalar()


def _current_head(database_url: str) -> str:
    """Discover the single current alembic head dynamically."""
    heads = run_alembic("heads", database_url=database_url)
    head_lines = [
        line.strip()
        for line in (heads.stdout + heads.stderr).splitlines()
        if line.strip() and not line.strip().startswith("INFO")
    ]
    revision_tokens = [line.split()[0] for line in head_lines if line]
    assert len(revision_tokens) == 1, f"expected a single head, got {revision_tokens}"
    return revision_tokens[0]


def test_migration_from_reader_chat_head_creates_clue_tables(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "12readerchat01", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    with engine.connect() as conn:
        pre = set(inspect(conn).get_table_names())
        assert "reader_conversations" in pre
        assert "clue_lifecycle_events" not in pre
    engine.dispose()

    run_alembic("upgrade", "head", database_url=empty_postgres)
    current = run_alembic("current", database_url=empty_postgres)
    assert _current_head(empty_postgres) in (current.stdout + current.stderr)

    engine = create_engine(empty_postgres)
    with engine.connect() as conn:
        names = set(inspect(conn).get_table_names())
        assert AUTHORITY_TABLES <= names
        for table in AUTHORITY_TABLES:
            count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            assert count == 0
        # Adjacent tables untouched by migration fabrications.
        assert conn.execute(text("SELECT COUNT(*) FROM reader_messages")).scalar() == 0
        assert (
            conn.execute(
                text("SELECT COUNT(*) FROM relationship_observations")
            ).scalar()
            == 0
        )
    engine.dispose()


def test_postgres_append_only_rejects_lifecycle_and_override_mutation(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_scope(engine)

    with engine.begin() as conn:
        event_id = _insert_lifecycle(
            conn,
            ids,
            from_status="candidate",
            to_status="active",
            event_key="cand-active",
            evidence_identities=["ev-cue-1:1:0:20:" + HEX64],
        )
        override_id = conn.execute(
            text(
                """
                INSERT INTO clue_overrides (
                    owner_id, novel_id, version_id, logical_clue_id, action,
                    field_name, value, author, reason, status, needs_relink
                ) VALUES (
                    :owner_id, :novel_id, :version_id, 'clue-seal', 'confirm',
                    'disposition', CAST(:value AS json), 'owner', 'ok', 'active', false
                ) RETURNING id
                """
            ),
            {
                "owner_id": ids["owner_id"],
                "novel_id": ids["novel_id"],
                "version_id": ids["version_id"],
                "value": json.dumps({"confirmed": True}),
            },
        ).scalar()

    def _expect_append_only(sql: str, params: dict) -> None:
        with engine.begin() as conn:
            with pytest.raises(DBAPIError) as exc_info:
                conn.execute(text(sql), params)
            assert "append_only_violation" in str(exc_info.value).lower()

    _expect_append_only(
        "UPDATE clue_lifecycle_events SET reason = 'tamper' WHERE id = :id",
        {"id": event_id},
    )
    _expect_append_only(
        "DELETE FROM clue_lifecycle_events WHERE id = :id",
        {"id": event_id},
    )
    _expect_append_only(
        "UPDATE clue_overrides SET status = 'superseded' WHERE id = :id",
        {"id": override_id},
    )

    # Supersession is INSERT of a new override; prior row stays byte-stable.
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO clue_overrides (
                    owner_id, novel_id, version_id, logical_clue_id, action,
                    field_name, value, author, reason, status, supersedes_id,
                    needs_relink
                ) VALUES (
                    :owner_id, :novel_id, :version_id, 'clue-seal', 'annotate',
                    'note', CAST(:value AS json), 'owner', 'follow-up', 'active',
                    :supersedes_id, false
                )
                """
            ),
            {
                "owner_id": ids["owner_id"],
                "novel_id": ids["novel_id"],
                "version_id": ids["version_id"],
                "value": json.dumps({"note": "keep watching"}),
                "supersedes_id": override_id,
            },
        )
        prior = conn.execute(
            text("SELECT status, reason FROM clue_overrides WHERE id = :id"),
            {"id": override_id},
        ).one()
        assert prior.status == "active"
        assert prior.reason == "ok"
        assert conn.execute(text("SELECT COUNT(*) FROM clue_overrides")).scalar() == 2
    engine.dispose()


def test_postgres_duplicate_lifecycle_event_key_rejected(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_scope(engine)
    with engine.begin() as conn:
        _insert_lifecycle(
            conn,
            ids,
            from_status="candidate",
            to_status="active",
            event_key="same-key",
        )
        with pytest.raises((IntegrityError, DBAPIError)):
            _insert_lifecycle(
                conn,
                ids,
                from_status="candidate",
                to_status="dismissed",
                event_key="same-key",
            )
    engine.dispose()


def test_postgres_illegal_transition_and_paid_off_order(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_scope(engine)

    with engine.begin() as conn:
        # Illegal pair: candidate → paid_off
        with pytest.raises((IntegrityError, DBAPIError)):
            _insert_lifecycle(
                conn,
                ids,
                from_status="candidate",
                to_status="paid_off",
                event_key="bad-pair",
                cue_chapter=1,
                cue_source_start=0,
                payoff_chapter=5,
                payoff_source_start=10,
            )

    with engine.begin() as conn:
        # paid_off without coordinates
        with pytest.raises(DBAPIError) as exc:
            _insert_lifecycle(
                conn,
                ids,
                from_status="reinforced",
                to_status="paid_off",
                event_key="paid-no-coords",
            )
        assert "paid_off" in str(exc.value).lower()

    with engine.begin() as conn:
        # payoff not later than cue
        with pytest.raises(DBAPIError) as exc:
            _insert_lifecycle(
                conn,
                ids,
                from_status="reinforced",
                to_status="paid_off",
                event_key="paid-early",
                cue_chapter=5,
                cue_source_start=10,
                payoff_chapter=5,
                payoff_source_start=10,
            )
        assert (
            "later_payoff" in str(exc.value).lower()
            or "paid_off" in str(exc.value).lower()
        )

    with engine.begin() as conn:
        event_id = _insert_lifecycle(
            conn,
            ids,
            from_status="reinforced",
            to_status="paid_off",
            event_key="paid-ok",
            evidence_identities=["cue", "pay"],
            cue_chapter=1,
            cue_source_start=0,
            payoff_chapter=5,
            payoff_source_start=20,
        )
        assert event_id is not None
    engine.dispose()


def test_postgres_link_exactly_one_target_and_pointer_cas_journal(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_scope(engine)

    # character for link target
    with engine.begin() as conn:
        character_id = conn.execute(
            text(
                "INSERT INTO characters (novel_id, name, role) "
                "VALUES (:novel_id, 'Alice', 'protagonist') RETURNING id"
            ),
            {"novel_id": ids["novel_id"]},
        ).scalar_one()
        conn.execute(
            text(
                """
                INSERT INTO clue_links (
                    owner_id, novel_id, version_id, logical_clue_id, target_kind,
                    character_id, link_identity, supporting_evidence_ids,
                    validation_status
                ) VALUES (
                    :owner_id, :novel_id, :version_id, 'clue-seal', 'character',
                    :character_id, :ident, CAST('[]' AS json), 'valid'
                )
                """
            ),
            {
                "owner_id": ids["owner_id"],
                "novel_id": ids["novel_id"],
                "version_id": ids["version_id"],
                "character_id": character_id,
                "ident": f"char:{character_id}",
            },
        )
        with pytest.raises((IntegrityError, DBAPIError)):
            # Zero targets
            conn.execute(
                text(
                    """
                    INSERT INTO clue_links (
                        owner_id, novel_id, version_id, logical_clue_id, target_kind,
                        link_identity, supporting_evidence_ids, validation_status
                    ) VALUES (
                        :owner_id, :novel_id, :version_id, 'clue-seal', 'character',
                        'char:missing', CAST('[]' AS json), 'unresolved'
                    )
                    """
                ),
                {
                    "owner_id": ids["owner_id"],
                    "novel_id": ids["novel_id"],
                    "version_id": ids["version_id"],
                },
            )

    # Active pointer + CAS journal
    with engine.begin() as conn:
        version2 = conn.execute(
            text(
                """
                INSERT INTO clue_analysis_versions (
                    owner_id, novel_id, version_key, status,
                    source_snapshot_hash, hierarchy_build_id, hierarchy_checksum,
                    prompt_hash, schema_hash, decoding_hash, config_hash, policy_hash,
                    model_lineage, price_snapshot, manifest, parent_version_id
                ) VALUES (
                    :owner_id, :novel_id, 'v2', 'validated',
                    :h, 'build-1', :h, :h, :h, :h, :h, :h,
                    CAST('{}' AS json), CAST('{}' AS json), CAST('{}' AS json),
                    :parent
                ) RETURNING id
                """
            ),
            {
                "owner_id": ids["owner_id"],
                "novel_id": ids["novel_id"],
                "h": HEX64_B,
                "parent": ids["version_id"],
            },
        ).scalar()

        conn.execute(
            text(
                """
                INSERT INTO clue_active_pointers (
                    owner_id, novel_id, version_id, revision, manifest_checksum
                ) VALUES (
                    :owner_id, :novel_id, :version_id, 1, :h
                )
                """
            ),
            {
                "owner_id": ids["owner_id"],
                "novel_id": ids["novel_id"],
                "version_id": ids["version_id"],
                "h": HEX64,
            },
        )

        # CAS promote with expected_revision=1
        updated = conn.execute(
            text(
                """
                UPDATE clue_active_pointers
                SET version_id = :to_version, revision = revision + 1,
                    manifest_checksum = :h
                WHERE owner_id = :owner_id AND novel_id = :novel_id
                  AND revision = :expected
                RETURNING revision
                """
            ),
            {
                "to_version": version2,
                "h": HEX64_B,
                "owner_id": ids["owner_id"],
                "novel_id": ids["novel_id"],
                "expected": 1,
            },
        ).scalar()
        assert updated == 2

        # Stale CAS fails (0 rows)
        stale = conn.execute(
            text(
                """
                UPDATE clue_active_pointers
                SET version_id = :to_version, revision = revision + 1
                WHERE owner_id = :owner_id AND novel_id = :novel_id
                  AND revision = :expected
                RETURNING revision
                """
            ),
            {
                "to_version": ids["version_id"],
                "owner_id": ids["owner_id"],
                "novel_id": ids["novel_id"],
                "expected": 1,
            },
        ).scalar()
        assert stale is None

        conn.execute(
            text(
                """
                INSERT INTO clue_pointer_journal (
                    owner_id, novel_id, from_version_id, to_version_id, action,
                    expected_revision, resulting_revision, manifest
                ) VALUES (
                    :owner_id, :novel_id, :from_v, :to_v, 'promote',
                    1, 2, CAST(:manifest AS json)
                )
                """
            ),
            {
                "owner_id": ids["owner_id"],
                "novel_id": ids["novel_id"],
                "from_v": ids["version_id"],
                "to_v": version2,
                "manifest": json.dumps({"manifest_checksum": HEX64_B}),
            },
        )
        # Journal is append-only
        jid = conn.execute(text("SELECT id FROM clue_pointer_journal LIMIT 1")).scalar()
        with pytest.raises(DBAPIError) as exc:
            conn.execute(
                text(
                    "UPDATE clue_pointer_journal SET action = 'rollback' WHERE id = :id"
                ),
                {"id": jid},
            )
        assert "append_only_violation" in str(exc.value).lower()
    engine.dispose()


def test_postgres_lifecycle_insert_replay_matches_derived_state(
    empty_postgres: str, require_postgres: None
):
    """Persist legal chain and prove pure replay equals derived terminal state."""
    from app.schemas.clue import ClueEvidenceRef

    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_scope(engine)

    cue = ClueEvidenceRef.model_validate(
        {
            "evidence_id": "ev-cue",
            "role": "cue",
            "chapter_id": ids["chapter_id"],
            "narrative_chapter_number": 1,
            "source_start": 0,
            "source_end": 20,
            "content_hash": HEX64,
        }
    )
    reinf = ClueEvidenceRef.model_validate(
        {
            "evidence_id": "ev-r1",
            "role": "reinforcement",
            "chapter_id": ids["chapter_id"],
            "narrative_chapter_number": 2,
            "source_start": 0,
            "source_end": 15,
            "content_hash": HEX64_B,
        }
    )
    payoff = ClueEvidenceRef.model_validate(
        {
            "evidence_id": "ev-pay",
            "role": "payoff",
            "chapter_id": ids["chapter5_id"],
            "narrative_chapter_number": 5,
            "source_start": 10,
            "source_end": 40,
            "content_hash": HEX64_C,
        }
    )

    with engine.begin() as conn:
        _insert_lifecycle(
            conn,
            ids,
            from_status="candidate",
            to_status="active",
            event_key="e1",
            evidence_identities=[cue.identity_key()],
        )
        _insert_lifecycle(
            conn,
            ids,
            from_status="active",
            to_status="reinforced",
            event_key="e2",
            evidence_identities=[reinf.identity_key()],
        )
        _insert_lifecycle(
            conn,
            ids,
            from_status="reinforced",
            to_status="paid_off",
            event_key="e3",
            evidence_identities=[cue.identity_key(), payoff.identity_key()],
            cue_chapter=1,
            cue_source_start=0,
            payoff_chapter=5,
            payoff_source_start=10,
        )
        rows = conn.execute(
            text(
                "SELECT from_status, to_status, event_key "
                "FROM clue_lifecycle_events "
                "WHERE version_id = :v AND logical_clue_id = 'clue-seal' "
                "ORDER BY id"
            ),
            {"v": ids["version_id"]},
        ).all()
        assert [r.to_status for r in rows] == ["active", "reinforced", "paid_off"]

    events = [
        LifecycleEventInput(
            from_status=ClueLifecycleState.CANDIDATE,
            to_status=ClueLifecycleState.ACTIVE,
            actor_source="machine",
            reason="e1",
            evidence=[cue],
            event_key="e1",
        ),
        LifecycleEventInput(
            from_status=ClueLifecycleState.ACTIVE,
            to_status=ClueLifecycleState.REINFORCED,
            actor_source="machine",
            reason="e2",
            evidence=[reinf],
            event_key="e2",
        ),
        LifecycleEventInput(
            from_status=ClueLifecycleState.REINFORCED,
            to_status=ClueLifecycleState.PAID_OFF,
            actor_source="machine",
            reason="e3",
            evidence=[cue, payoff],
            event_key="e3",
        ),
    ]
    assert replay_lifecycle(events) == ClueLifecycleState.PAID_OFF
    engine.dispose()


def test_package_exports_and_metadata_importable():
    from app import models, schemas

    assert hasattr(models, "ClueLifecycleEvent")
    assert hasattr(models, "MachineClue")
    assert hasattr(schemas, "ClueLifecycleState")
    assert hasattr(schemas, "replay_lifecycle")
    assert "clue_lifecycle_events" in models.Base.metadata.tables
