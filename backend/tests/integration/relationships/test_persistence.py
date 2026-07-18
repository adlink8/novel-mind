"""Phase 09 relationship observation persistence authority contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models.character import CharacterRelation
from app.models.relationship import (
    CharacterIdentityOverride,
    RelationshipBuildRun,
    RelationshipEvidenceLink,
    RelationshipObservation,
    RelationshipObservationCandidate,
    RelationshipObservationJudgment,
    RelationshipOverride,
    RelationshipProjectionAudit,
)
from app.schemas.relationship import (
    AcceptedObservationContract,
    CharacterIdentityOverrideCreate,
    NarrativeInterval,
    RelationshipEdgeType,
    RelationshipGraphEdge,
    RelationshipGraphEnvelope,
    RelationshipOverrideCreate,
    RelationshipSemanticJudgment,
    RelationshipVersionSource,
)
from tests.integration.conftest import run_alembic

pytestmark = pytest.mark.integration

HEX64 = "a" * 64
HEX64_B = "b" * 64

AUTHORITY_TABLES = {
    "relationship_build_runs",
    "relationship_observation_candidates",
    "relationship_observation_judgments",
    "relationship_observations",
    "relationship_evidence_links",
    "character_identity_overrides",
    "relationship_overrides",
    "relationship_projection_audits",
}

APPEND_ONLY_TABLES = {
    "relationship_observations",
    "character_identity_overrides",
    "relationship_overrides",
}


# ---------------------------------------------------------------------------
# Contract tests (no live DB required)
# ---------------------------------------------------------------------------


def test_contract_metadata_contains_eight_authority_tables():
    tables = set(RelationshipObservation.metadata.tables)
    assert AUTHORITY_TABLES <= tables
    assert "character_relations" in tables  # legacy remains, unused as authority


def test_contract_eight_orm_classes_exportable():
    classes = [
        RelationshipBuildRun,
        RelationshipObservationCandidate,
        RelationshipObservationJudgment,
        RelationshipObservation,
        RelationshipEvidenceLink,
        CharacterIdentityOverride,
        RelationshipOverride,
        RelationshipProjectionAudit,
    ]
    assert len(classes) == 8
    assert RelationshipObservation.__tablename__ == "relationship_observations"
    assert CharacterRelation.__tablename__ == "character_relations"
    # Graph authority must not inherit or alias the legacy snapshot table.
    assert RelationshipObservation.__tablename__ != CharacterRelation.__tablename__


def test_contract_observation_lineage_and_idempotency_columns():
    columns = set(inspect(RelationshipObservation).columns.keys())
    required = {
        "owner_id",
        "novel_id",
        "analysis_version_id",
        "source_judgment_id",
        "judgment_id",
        "candidate_id",
        "source_character_id",
        "target_character_id",
        "relation_type",
        "transition",
        "valid_from_chapter",
        "valid_from_narrative_index",
        "valid_to_chapter",
        "valid_to_narrative_index",
        "evidence_checksum",
        "prompt_hash",
        "schema_hash",
        "policy_hash",
        "idempotency_key",
        "observation_checksum",
        "status",
    }
    assert required <= columns


def test_contract_observation_fks_to_version_and_source_judgment():
    fks = {
        (fk.parent.name, list(fk.column.table.name for _ in [0])[0], fk.column.name)
        for col in RelationshipObservation.__table__.columns
        for fk in col.foreign_keys
    }
    targets = {(parent, table) for parent, table, _ in fks}
    assert ("analysis_version_id", "analysis_versions") in targets
    assert ("source_judgment_id", "knowledge_relation_judgments") in targets
    assert ("judgment_id", "relationship_observation_judgments") in targets


def test_contract_relation_type_schema_accepts_only_five_fiction_edges():
    for edge in RelationshipEdgeType:
        payload = _accepted_observation_payload(relation_type=edge.value)
        model = AcceptedObservationContract.model_validate(payload)
        assert model.relation_type == edge

    for bad in ("causes", "precedes", "same_entity", "history", "friend", "unknown"):
        with pytest.raises(ValidationError):
            AcceptedObservationContract.model_validate(
                _accepted_observation_payload(relation_type=bad)
            )


def test_contract_semantic_judgment_rejects_timeline_and_identity_edges():
    base = {
        "candidate_key": "k1",
        "source_ref": "c1",
        "target_ref": "c2",
        "relation_type": "ally",
        "transition": "establish",
        "valid_from_evidence_id": "e1",
        "supporting_evidence_ids": ["e1"],
        "confidence": 0.9,
    }
    assert RelationshipSemanticJudgment.model_validate(base).relation_type.value == "ally"
    for bad in ("causes", "precedes", "same_entity"):
        with pytest.raises(ValidationError):
            RelationshipSemanticJudgment.model_validate({**base, "relation_type": bad})


def test_contract_interval_and_self_edge_validation():
    with pytest.raises(ValidationError):
        NarrativeInterval(
            valid_from_chapter=3,
            valid_from_narrative_index=0,
            valid_to_chapter=2,
            valid_to_narrative_index=0,
        )
    with pytest.raises(ValidationError):
        AcceptedObservationContract.model_validate(
            _accepted_observation_payload(
                source_character_id=7,
                target_character_id=7,
            )
        )


def test_contract_override_requires_author_reason_signature_and_state():
    identity = CharacterIdentityOverrideCreate.model_validate(
        {
            "novel_id": 1,
            "analysis_version_id": 2,
            "canonical_character_id": 10,
            "merged_character_ids": [11, 12],
            "author": "owner@example.com",
            "reason": "alias merge confirmed by chapter 3",
            "evidence_signature": "sig-" + HEX64[:16],
            "status": "active",
            "supersedes_id": None,
        }
    )
    assert identity.status.value == "active"

    rel = RelationshipOverrideCreate.model_validate(
        {
            "novel_id": 1,
            "analysis_version_id": 2,
            "logical_relationship_key": "10:11:ally",
            "field_name": "relation_type",
            "value": {"relation_type": "enemy"},
            "author": "owner@example.com",
            "reason": "tone shift at chapter 8",
            "evidence_signature": "sig-" + HEX64_B[:16],
            "status": "needs_relink",
            "supersedes_id": 3,
        }
    )
    assert rel.status.value == "needs_relink"
    assert rel.supersedes_id == 3

    with pytest.raises(ValidationError):
        CharacterIdentityOverrideCreate.model_validate(
            {
                "novel_id": 1,
                "analysis_version_id": 2,
                "canonical_character_id": 10,
                "merged_character_ids": [11],
                # missing author/reason/signature
            }
        )


def test_contract_graph_envelope_and_edge_shapes():
    envelope = RelationshipGraphEnvelope.model_validate(
        {
            "novel_id": 1,
            "version_id": 9,
            "source": RelationshipVersionSource.ACTIVE,
            "through_chapter": 4,
            "full_book": False,
            "cutoff_chapter": 4,
            "nodes": [
                {
                    "character_id": 1,
                    "name": "Alice",
                    "aliases": [],
                    "first_visible_chapter": 1,
                }
            ],
            "edges": [
                {
                    "observation_id": 100,
                    "source_character_id": 1,
                    "target_character_id": 2,
                    "relation_type": "mentor",
                    "transition": "establish",
                    "confidence": 0.91,
                    "valid_from_chapter": 2,
                    "provenance": "machine",
                    "evidence_count": 1,
                }
            ],
            "counts": {"nodes": 1, "edges": 1, "relation_types": {"mentor": 1}},
            "available_relation_types": ["mentor"],
            "available_character_ids": [1, 2],
            "degradation": {
                "mode": "normal",
                "node_count": 1,
                "edge_count": 1,
            },
        }
    )
    assert envelope.edges[0].relation_type == RelationshipEdgeType.MENTOR
    with pytest.raises(ValidationError):
        RelationshipGraphEdge.model_validate(
            {
                "observation_id": 1,
                "source_character_id": 1,
                "target_character_id": 2,
                "relation_type": "causes",
                "transition": "establish",
                "confidence": 0.9,
                "valid_from_chapter": 1,
            }
        )


def test_contract_override_columns_include_supersession_and_relink():
    identity_cols = set(inspect(CharacterIdentityOverride).columns.keys())
    override_cols = set(inspect(RelationshipOverride).columns.keys())
    for cols in (identity_cols, override_cols):
        assert {"author", "reason", "evidence_signature", "supersedes_id", "status"} <= cols


# ---------------------------------------------------------------------------
# PostgreSQL migration + append-only enforcement
# ---------------------------------------------------------------------------


def test_migration_from_phase08_head_creates_relationship_tables(
    empty_postgres: str, require_postgres: None
):
    # Upgrade only through Phase 08 head first.
    run_alembic("upgrade", "10analysistime01", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    with engine.connect() as conn:
        pre_names = set(inspect(conn).get_table_names())
        assert "analysis_versions" in pre_names
        assert "relationship_observations" not in pre_names
        # Seed a legacy character_relations row so migration must leave it alone.
        # character_relations exists from earlier migrations; insert is optional
        # if the table is present after full upgrade path.
    engine.dispose()

    run_alembic("upgrade", "head", database_url=empty_postgres)
    current = run_alembic("current", database_url=empty_postgres)
    assert "11relobserve01" in (current.stdout + current.stderr)

    engine = create_engine(empty_postgres)
    with engine.connect() as conn:
        names = set(inspect(conn).get_table_names())
        assert AUTHORITY_TABLES <= names
        # Migration must not fabricate accepted facts.
        obs_count = conn.execute(
            text("SELECT COUNT(*) FROM relationship_observations")
        ).scalar()
        assert obs_count == 0
        # Legacy table remains and is not treated as relationship authority.
        assert "character_relations" in names
        legacy_count = conn.execute(
            text("SELECT COUNT(*) FROM character_relations")
        ).scalar()
        assert legacy_count == 0

        index_names = {
            idx["name"]
            for table in (
                "relationship_observations",
                "relationship_evidence_links",
            )
            for idx in inspect(conn).get_indexes(table)
        }
        assert "idx_rel_observations_version_interval" in index_names
        assert "idx_rel_evidence_scope_chapter" in index_names
        assert "idx_rel_observations_pair_type" in index_names
    engine.dispose()


def test_postgres_append_only_rejects_update_and_delete(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_minimal_observation_graph(engine)

    def _expect_append_only(sql: str, params: dict) -> None:
        # Each attempt uses its own transaction so a blocked statement does not
        # poison later checks with InFailedSqlTransaction.
        with engine.begin() as conn:
            with pytest.raises(DBAPIError) as exc_info:
                conn.execute(text(sql), params)
            assert "append_only_violation" in str(exc_info.value).lower()

    _expect_append_only(
        "UPDATE relationship_observations SET confidence = 0.1 WHERE id = :id",
        {"id": ids["observation_id"]},
    )
    _expect_append_only(
        "DELETE FROM relationship_observations WHERE id = :id",
        {"id": ids["observation_id"]},
    )
    _expect_append_only(
        "UPDATE relationship_overrides SET status = 'superseded' WHERE id = :id",
        {"id": ids["override_id"]},
    )
    _expect_append_only(
        "UPDATE character_identity_overrides SET status = 'superseded' WHERE id = :id",
        {"id": ids["identity_override_id"]},
    )

    with engine.begin() as conn:
        # Prior accepted row remains unchanged after failed mutations.
        conf = conn.execute(
            text(
                "SELECT confidence, observation_checksum "
                "FROM relationship_observations WHERE id = :id"
            ),
            {"id": ids["observation_id"]},
        ).one()
        assert conf.confidence == pytest.approx(0.91)
        assert conf.observation_checksum == HEX64

        # Supersession is append-only INSERT of a new override row.
        conn.execute(
            text(
                """
                INSERT INTO relationship_overrides (
                    owner_id, novel_id, analysis_version_id, observation_id,
                    logical_relationship_key, field_name, value, author, reason,
                    evidence_signature, supersedes_id, status, provenance
                ) VALUES (
                    :owner_id, :novel_id, :version_id, :observation_id,
                    '1:2:ally', 'relation_type',
                    CAST(:value AS json), 'owner', 'supersede',
                    :sig, :supersedes_id, 'active', CAST('{}' AS json)
                )
                """
            ),
            {
                "owner_id": ids["owner_id"],
                "novel_id": ids["novel_id"],
                "version_id": ids["version_id"],
                "observation_id": ids["observation_id"],
                "value": '{"relation_type":"enemy"}',
                "sig": "sig-supersede-" + HEX64[:8],
                "supersedes_id": ids["override_id"],
            },
        )
        prior = conn.execute(
            text(
                "SELECT status, reason FROM relationship_overrides WHERE id = :id"
            ),
            {"id": ids["override_id"]},
        ).one()
        assert prior.status == "active"
        assert prior.reason == "initial correction"
        total = conn.execute(text("SELECT COUNT(*) FROM relationship_overrides")).scalar()
        assert total == 2
    engine.dispose()


def test_postgres_duplicate_idempotency_key_rejected(
    empty_postgres: str, require_postgres: None
):
    run_alembic("upgrade", "head", database_url=empty_postgres)
    engine = create_engine(empty_postgres)
    ids = _seed_minimal_observation_graph(engine)
    with engine.begin() as conn:
        with pytest.raises((IntegrityError, DBAPIError)):
            conn.execute(
                text(
                    """
                    INSERT INTO relationship_observations (
                        owner_id, novel_id, analysis_version_id, build_run_id,
                        candidate_id, judgment_id, source_judgment_id,
                        source_character_id, target_character_id, relation_type,
                        transition, status, valid_from_chapter,
                        valid_from_narrative_index, valid_from_evidence_id,
                        confidence, evidence_checksum, observation_checksum,
                        prompt_hash, schema_hash, policy_hash, model_lineage,
                        idempotency_key
                    ) VALUES (
                        :owner_id, :novel_id, :version_id, :build_run_id,
                        :candidate_id, :judgment_id, :source_judgment_id,
                        :source_character_id, :target_character_id, 'enemy',
                        'change', 'accepted', 5, 0, 'e2',
                        0.9, :hash, :hash, :hash, :hash, :hash,
                        CAST('{}' AS json), :idem
                    )
                    """
                ),
                {
                    **ids,
                    "hash": HEX64_B,
                    "idem": ids["idempotency_key"],
                },
            )
    engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _accepted_observation_payload(**overrides):
    payload = {
        "owner_id": 1,
        "novel_id": 1,
        "analysis_version_id": 1,
        "source_judgment_id": 10,
        "candidate_id": 20,
        "judgment_id": 30,
        "source_character_id": 1,
        "target_character_id": 2,
        "relation_type": "ally",
        "transition": "establish",
        "interval": {
            "valid_from_chapter": 2,
            "valid_from_narrative_index": 0,
            "valid_to_chapter": None,
            "valid_to_narrative_index": None,
        },
        "evidence": [
            {
                "evidence_id": "e1",
                "chapter_id": 1,
                "source_start": 0,
                "source_end": 12,
                "content_hash": HEX64,
            }
        ],
        "evidence_checksum": HEX64,
        "prompt_hash": HEX64,
        "schema_hash": HEX64,
        "policy_hash": HEX64,
        "confidence": 0.91,
        "idempotency_key": "idem-" + HEX64[:16],
        "model_lineage": {"deployment": "rel-judge-v1"},
    }
    payload.update(overrides)
    return payload


def _seed_minimal_observation_graph(engine) -> dict[str, int | str]:
    """Insert owner/novel/version/judgment lineage and one accepted observation."""
    with engine.begin() as conn:
        owner_id = conn.execute(
            text(
                """
                INSERT INTO users (username, email, hashed_password, is_active, is_superuser)
                VALUES ('rel09user', 'rel09@example.com', 'x', true, false)
                RETURNING id
                """
            )
        ).scalar_one()
        novel_id = conn.execute(
            text(
                """
                INSERT INTO novels (title, author, owner_id, status, chapter_count, word_count)
                VALUES ('Rel Novel', 'Author', :owner_id, 'ready', 1, 24)
                RETURNING id
                """
            ),
            {"owner_id": owner_id},
        ).scalar_one()
        chapter_id = conn.execute(
            text(
                """
                INSERT INTO chapters (novel_id, chapter_number, title, content, word_count)
                VALUES (:novel_id, 1, 'C1', 'Alice met Bob as allies.', 24)
                RETURNING id
                """
            ),
            {"novel_id": novel_id},
        ).scalar_one()
        version_id = conn.execute(
            text(
                """
                INSERT INTO analysis_versions (
                    owner_id, novel_id, version_key, status,
                    source_snapshot_hash, hierarchy_build_id, hierarchy_checksum,
                    prompt_hash, schema_hash, model_lineage, decoding_hash,
                    config_hash, price_snapshot, manifest
                ) VALUES (
                    :owner_id, :novel_id, 'v1', 'candidate',
                    :h, 'hb1', :h, :h, :h, CAST('{}' AS json), :h,
                    :h, CAST('{}' AS json), CAST('{}' AS json)
                ) RETURNING id
                """
            ),
            {"owner_id": owner_id, "novel_id": novel_id, "h": HEX64},
        ).scalar_one()
        char_a = conn.execute(
            text(
                """
                INSERT INTO characters (novel_id, name, role)
                VALUES (:novel_id, 'Alice', 'protagonist')
                RETURNING id
                """
            ),
            {"novel_id": novel_id},
        ).scalar_one()
        char_b = conn.execute(
            text(
                """
                INSERT INTO characters (novel_id, name, role)
                VALUES (:novel_id, 'Bob', 'supporting')
                RETURNING id
                """
            ),
            {"novel_id": novel_id},
        ).scalar_one()
        # Optional legacy snapshot row — must never become observation truth.
        conn.execute(
            text(
                """
                INSERT INTO character_relations (
                    novel_id, source_character_id, target_character_id,
                    relation_type, strength, chapter_first_seen
                ) VALUES (
                    :novel_id, :a, :b, 'friend', 5, 1
                )
                """
            ),
            {"novel_id": novel_id, "a": char_a, "b": char_b},
        )
        run_id = conn.execute(
            text(
                """
                INSERT INTO knowledge_extraction_runs (
                    owner_id, novel_id, run_name, domain_profile, ontology_profile,
                    status, config_snapshot, metrics_summary
                ) VALUES (
                    :owner_id, :novel_id, 'seed', 'fiction', 'fiction.v1',
                    'completed', CAST('{}' AS json), CAST('{}' AS json)
                ) RETURNING id
                """
            ),
            {"owner_id": owner_id, "novel_id": novel_id},
        ).scalar_one()
        rel_candidate_id = conn.execute(
            text(
                """
                INSERT INTO knowledge_relation_candidates (
                    owner_id, novel_id, run_id, domain_profile, relation_type,
                    source_kind, source_id, target_kind, target_id,
                    recall_signals, package_snapshot, evidence_refs, status
                ) VALUES (
                    :owner_id, :novel_id, :run_id, 'fiction', 'ally',
                    'entity_candidate', :a, 'entity_candidate', :b,
                    CAST('{}' AS json), CAST('{}' AS json),
                    CAST('[]' AS json), 'accepted'
                ) RETURNING id
                """
            ),
            {
                "owner_id": owner_id,
                "novel_id": novel_id,
                "run_id": run_id,
                "a": char_a,
                "b": char_b,
            },
        ).scalar_one()
        source_judgment_id = conn.execute(
            text(
                """
                INSERT INTO knowledge_relation_judgments (
                    owner_id, novel_id, run_id, relation_candidate_id,
                    prompt_version, model_name, relation_type, confidence,
                    evidence_refs, raw_output, structured_output,
                    status, gate_status, gate_failures, risk_flags
                ) VALUES (
                    :owner_id, :novel_id, :run_id, :candidate_id,
                    'pv1', 'model', 'ally', 0.95,
                    CAST('[]' AS json), CAST('{}' AS json), CAST('{}' AS json),
                    'accepted', 'accepted', CAST('[]' AS json), CAST('[]' AS json)
                ) RETURNING id
                """
            ),
            {
                "owner_id": owner_id,
                "novel_id": novel_id,
                "run_id": run_id,
                "candidate_id": rel_candidate_id,
            },
        ).scalar_one()
        build_run_id = conn.execute(
            text(
                """
                INSERT INTO relationship_build_runs (
                    owner_id, novel_id, analysis_version_id, status,
                    checkpoint, progress, prompt_hash, schema_hash, policy_hash,
                    decoding_hash, model_lineage,
                    candidate_count, judgment_count, accepted_count,
                    review_count, rejected_count
                ) VALUES (
                    :owner_id, :novel_id, :version_id, 'completed',
                    CAST('{}' AS json), CAST('{}' AS json), :h, :h, :h, :h,
                    CAST('{}' AS json),
                    1, 1, 1, 0, 0
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
        candidate_id = conn.execute(
            text(
                """
                INSERT INTO relationship_observation_candidates (
                    owner_id, novel_id, analysis_version_id, build_run_id,
                    source_judgment_id, source_relation_candidate_id,
                    source_character_id, target_character_id, relation_type,
                    package_hash, package_snapshot, recall_signals,
                    evidence_refs, status
                ) VALUES (
                    :owner_id, :novel_id, :version_id, :build_run_id,
                    :source_judgment_id, :rel_candidate_id,
                    :a, :b, 'ally',
                    :h, CAST('{}' AS json), CAST('{}' AS json),
                    CAST('[]' AS json), 'accepted'
                ) RETURNING id
                """
            ),
            {
                "owner_id": owner_id,
                "novel_id": novel_id,
                "version_id": version_id,
                "build_run_id": build_run_id,
                "source_judgment_id": source_judgment_id,
                "rel_candidate_id": rel_candidate_id,
                "a": char_a,
                "b": char_b,
                "h": HEX64,
            },
        ).scalar_one()
        judgment_id = conn.execute(
            text(
                """
                INSERT INTO relationship_observation_judgments (
                    owner_id, novel_id, analysis_version_id, build_run_id,
                    candidate_id, prompt_hash, schema_hash, policy_hash,
                    model_name, model_lineage, relation_type, transition,
                    confidence, valid_from_evidence_id, supporting_evidence_ids,
                    structured_output, risk_flags, status, gate_status,
                    gate_failures, call_skipped
                ) VALUES (
                    :owner_id, :novel_id, :version_id, :build_run_id,
                    :candidate_id, :h, :h, :h,
                    'model', CAST('{}' AS json), 'ally', 'establish',
                    0.91, 'e1', CAST('["e1"]' AS json),
                    CAST('{}' AS json), CAST('[]' AS json), 'accepted', 'accepted',
                    CAST('[]' AS json), false
                ) RETURNING id
                """
            ),
            {
                "owner_id": owner_id,
                "novel_id": novel_id,
                "version_id": version_id,
                "build_run_id": build_run_id,
                "candidate_id": candidate_id,
                "h": HEX64,
            },
        ).scalar_one()
        idem = "idem-key-" + HEX64[:20]
        observation_id = conn.execute(
            text(
                """
                INSERT INTO relationship_observations (
                    owner_id, novel_id, analysis_version_id, build_run_id,
                    candidate_id, judgment_id, source_judgment_id,
                    source_character_id, target_character_id, relation_type,
                    transition, status, valid_from_chapter,
                    valid_from_narrative_index, valid_from_evidence_id,
                    confidence, evidence_checksum, observation_checksum,
                    prompt_hash, schema_hash, policy_hash, model_lineage,
                    idempotency_key
                ) VALUES (
                    :owner_id, :novel_id, :version_id, :build_run_id,
                    :candidate_id, :judgment_id, :source_judgment_id,
                    :a, :b, 'ally', 'establish', 'accepted', 1, 0, 'e1',
                    0.91, :h, :h, :h, :h, :h, CAST('{}' AS json), :idem
                ) RETURNING id
                """
            ),
            {
                "owner_id": owner_id,
                "novel_id": novel_id,
                "version_id": version_id,
                "build_run_id": build_run_id,
                "candidate_id": candidate_id,
                "judgment_id": judgment_id,
                "source_judgment_id": source_judgment_id,
                "a": char_a,
                "b": char_b,
                "h": HEX64,
                "idem": idem,
            },
        ).scalar_one()
        conn.execute(
            text(
                """
                INSERT INTO relationship_evidence_links (
                    observation_id, owner_id, novel_id, analysis_version_id,
                    evidence_id, chapter_id, source_start, source_end,
                    content_hash, sort_order
                ) VALUES (
                    :observation_id, :owner_id, :novel_id, :version_id,
                    'e1', :chapter_id, 0, 20, :h, 0
                )
                """
            ),
            {
                "observation_id": observation_id,
                "owner_id": owner_id,
                "novel_id": novel_id,
                "version_id": version_id,
                "chapter_id": chapter_id,
                "h": HEX64,
            },
        )
        identity_override_id = conn.execute(
            text(
                """
                INSERT INTO character_identity_overrides (
                    owner_id, novel_id, analysis_version_id,
                    canonical_character_id, merged_character_ids,
                    author, reason, evidence_signature, status, provenance
                ) VALUES (
                    :owner_id, :novel_id, :version_id,
                    :a, CAST(:merged AS json),
                    'owner', 'merge aliases', :sig, 'active', CAST('{}' AS json)
                ) RETURNING id
                """
            ),
            {
                "owner_id": owner_id,
                "novel_id": novel_id,
                "version_id": version_id,
                "a": char_a,
                "merged": f"[{char_b}]",
                "sig": "sig-identity-" + HEX64[:8],
            },
        ).scalar_one()
        override_id = conn.execute(
            text(
                """
                INSERT INTO relationship_overrides (
                    owner_id, novel_id, analysis_version_id, observation_id,
                    logical_relationship_key, field_name, value, author, reason,
                    evidence_signature, status, provenance
                ) VALUES (
                    :owner_id, :novel_id, :version_id, :observation_id,
                    :key, 'relation_type', CAST(:value AS json),
                    'owner', 'initial correction', :sig, 'active',
                    CAST('{}' AS json)
                ) RETURNING id
                """
            ),
            {
                "owner_id": owner_id,
                "novel_id": novel_id,
                "version_id": version_id,
                "observation_id": observation_id,
                "key": f"{char_a}:{char_b}:ally",
                "value": '{"relation_type":"ally"}',
                "sig": "sig-override-" + HEX64[:8],
            },
        ).scalar_one()

    return {
        "owner_id": owner_id,
        "novel_id": novel_id,
        "version_id": version_id,
        "build_run_id": build_run_id,
        "candidate_id": candidate_id,
        "judgment_id": judgment_id,
        "source_judgment_id": source_judgment_id,
        "source_character_id": char_a,
        "target_character_id": char_b,
        "observation_id": observation_id,
        "override_id": override_id,
        "identity_override_id": identity_override_id,
        "idempotency_key": idem,
        "chapter_id": chapter_id,
    }
