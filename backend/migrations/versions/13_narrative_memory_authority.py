"""Add candidate-only narrative memory authority.

Revision ID: 13memoryauth01
Revises: 11cluetrack01
"""

from alembic import op
from sqlalchemy import text

revision = "13memoryauth01"
down_revision = "11cluetrack01"
branch_labels = None
depends_on = None

AUTHORITY_TABLES = (
    "narrative_memory_versions",
    "narrative_memory_nodes",
    "narrative_memory_claims",
    "narrative_memory_edges",
    "narrative_memory_source_links",
    "narrative_memory_manifests",
    "narrative_memory_validation_reports",
)
CONTENT_TABLES = (
    "narrative_memory_nodes",
    "narrative_memory_claims",
    "narrative_memory_edges",
    "narrative_memory_source_links",
)


def _create_frozen_tables(bind) -> None:
    """Create the revision-owned schema without consulting runtime metadata."""
    bind.execute(
        text(
            """
            CREATE TABLE narrative_memory_versions (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                novel_id INTEGER NOT NULL REFERENCES novels(id) ON DELETE RESTRICT,
                version_key VARCHAR(120) NOT NULL,
                source_snapshot_hash VARCHAR(64) NOT NULL,
                hierarchy_build_id VARCHAR(64) NOT NULL,
                hierarchy_checksum VARCHAR(64) NOT NULL,
                eligibility_policy_version VARCHAR(80) NOT NULL,
                eligibility_report_checksum VARCHAR(64) NOT NULL,
                prompt_hash VARCHAR(64) NOT NULL,
                schema_hash VARCHAR(64) NOT NULL,
                model_lineage JSONB NOT NULL,
                decoding_hash VARCHAR(64) NOT NULL,
                config_hash VARCHAR(64) NOT NULL,
                policy_hash VARCHAR(64) NOT NULL,
                optional_source_lineage JSONB NOT NULL,
                parent_version_id INTEGER NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_memory_versions_scope UNIQUE (owner_id, novel_id, id),
                CONSTRAINT uq_memory_versions_key UNIQUE (owner_id, novel_id, version_key),
                CONSTRAINT fk_memory_versions_parent_scope FOREIGN KEY
                    (owner_id, novel_id, parent_version_id) REFERENCES
                    narrative_memory_versions(owner_id, novel_id, id) ON DELETE RESTRICT,
                CONSTRAINT ck_memory_versions_snapshot_hash
                    CHECK (length(source_snapshot_hash) = 64),
                CONSTRAINT ck_memory_versions_hierarchy_checksum
                    CHECK (length(hierarchy_checksum) = 64),
                CONSTRAINT ck_memory_versions_eligibility_checksum
                    CHECK (length(eligibility_report_checksum) = 64),
                CONSTRAINT ck_memory_versions_prompt CHECK (length(prompt_hash) = 64),
                CONSTRAINT ck_memory_versions_schema CHECK (length(schema_hash) = 64),
                CONSTRAINT ck_memory_versions_decoding CHECK (length(decoding_hash) = 64),
                CONSTRAINT ck_memory_versions_config CHECK (length(config_hash) = 64),
                CONSTRAINT ck_memory_versions_policy CHECK (length(policy_hash) = 64)
            );
            CREATE INDEX idx_memory_versions_scope
                ON narrative_memory_versions(owner_id, novel_id);
            CREATE INDEX idx_memory_versions_hierarchy
                ON narrative_memory_versions(hierarchy_build_id);

            CREATE TABLE narrative_memory_nodes (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                novel_id INTEGER NOT NULL,
                version_id INTEGER NOT NULL,
                node_key VARCHAR(160) NOT NULL,
                node_kind VARCHAR(32) NOT NULL,
                chapter_start INTEGER NOT NULL,
                chapter_end INTEGER NOT NULL,
                schema_version VARCHAR(40) NOT NULL,
                content_checksum VARCHAR(64) NOT NULL,
                model_lineage_checksum VARCHAR(64) NOT NULL,
                display_label TEXT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_memory_nodes_scope UNIQUE (owner_id, novel_id, version_id, id),
                CONSTRAINT uq_memory_nodes_key UNIQUE
                    (owner_id, novel_id, version_id, node_key),
                CONSTRAINT fk_memory_nodes_version_scope FOREIGN KEY
                    (owner_id, novel_id, version_id) REFERENCES
                    narrative_memory_versions(owner_id, novel_id, id) ON DELETE RESTRICT,
                CONSTRAINT ck_memory_nodes_kind CHECK
                    (node_kind IN ('chapter_state','story_arc','volume','global_story')),
                CONSTRAINT ck_memory_nodes_range CHECK
                    (chapter_start > 0 AND chapter_end >= chapter_start),
                CONSTRAINT ck_memory_nodes_chapter_singleton CHECK
                    (node_kind <> 'chapter_state' OR chapter_start = chapter_end),
                CONSTRAINT ck_memory_nodes_content_checksum CHECK
                    (length(content_checksum) = 64),
                CONSTRAINT ck_memory_nodes_lineage_checksum CHECK
                    (length(model_lineage_checksum) = 64)
            );
            CREATE INDEX idx_memory_nodes_version_kind
                ON narrative_memory_nodes(version_id, node_kind);
            CREATE INDEX idx_memory_nodes_version_range
                ON narrative_memory_nodes(version_id, chapter_start, chapter_end);

            CREATE TABLE narrative_memory_claims (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                novel_id INTEGER NOT NULL,
                version_id INTEGER NOT NULL,
                node_id INTEGER NOT NULL,
                claim_key VARCHAR(180) NOT NULL,
                claim_kind VARCHAR(40) NOT NULL,
                schema_version VARCHAR(40) NOT NULL,
                typed_payload JSONB NOT NULL,
                uncertainty VARCHAR(24) NOT NULL,
                confidence DOUBLE PRECISION NOT NULL,
                visible_from_chapter INTEGER NOT NULL,
                claim_checksum VARCHAR(64) NOT NULL,
                model_lineage_checksum VARCHAR(64) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_memory_claims_scope UNIQUE
                    (owner_id, novel_id, version_id, id),
                CONSTRAINT uq_memory_claims_key UNIQUE
                    (owner_id, novel_id, version_id, claim_key),
                CONSTRAINT fk_memory_claims_version_scope FOREIGN KEY
                    (owner_id, novel_id, version_id) REFERENCES
                    narrative_memory_versions(owner_id, novel_id, id) ON DELETE RESTRICT,
                CONSTRAINT fk_memory_claims_node_scope FOREIGN KEY
                    (owner_id, novel_id, version_id, node_id) REFERENCES
                    narrative_memory_nodes(owner_id, novel_id, version_id, id)
                    ON DELETE RESTRICT,
                CONSTRAINT ck_memory_claims_kind CHECK (claim_kind IN
                    ('entity_state','event_fact','relationship_delta','clue_delta',
                     'world_state_delta','open_loop_delta')),
                CONSTRAINT ck_memory_claims_uncertainty CHECK
                    (uncertainty IN ('certain','likely','uncertain','unknown')),
                CONSTRAINT ck_memory_claims_confidence CHECK
                    (confidence >= 0 AND confidence <= 1),
                CONSTRAINT ck_memory_claims_visibility CHECK (visible_from_chapter > 0),
                CONSTRAINT ck_memory_claims_checksum CHECK (length(claim_checksum) = 64),
                CONSTRAINT ck_memory_claims_lineage_checksum CHECK
                    (length(model_lineage_checksum) = 64)
            );
            CREATE INDEX idx_memory_claims_version_node
                ON narrative_memory_claims(version_id, node_id);
            CREATE INDEX idx_memory_claims_version_kind
                ON narrative_memory_claims(version_id, claim_kind);
            CREATE INDEX idx_memory_claims_visibility
                ON narrative_memory_claims(version_id, visible_from_chapter);
            CREATE INDEX idx_memory_claims_checksum ON narrative_memory_claims(claim_checksum);

            CREATE TABLE narrative_memory_edges (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                novel_id INTEGER NOT NULL,
                version_id INTEGER NOT NULL,
                source_node_id INTEGER NOT NULL,
                target_node_id INTEGER NOT NULL,
                edge_type VARCHAR(32) NOT NULL,
                edge_checksum VARCHAR(64) NOT NULL,
                model_lineage_checksum VARCHAR(64) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_memory_edges_identity UNIQUE
                    (owner_id, novel_id, version_id, source_node_id, target_node_id, edge_type),
                CONSTRAINT fk_memory_edges_version_scope FOREIGN KEY
                    (owner_id, novel_id, version_id) REFERENCES
                    narrative_memory_versions(owner_id, novel_id, id) ON DELETE RESTRICT,
                CONSTRAINT fk_memory_edges_source_scope FOREIGN KEY
                    (owner_id, novel_id, version_id, source_node_id) REFERENCES
                    narrative_memory_nodes(owner_id, novel_id, version_id, id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_memory_edges_target_scope FOREIGN KEY
                    (owner_id, novel_id, version_id, target_node_id) REFERENCES
                    narrative_memory_nodes(owner_id, novel_id, version_id, id)
                    ON DELETE RESTRICT,
                CONSTRAINT ck_memory_edges_distinct CHECK (source_node_id <> target_node_id),
                CONSTRAINT ck_memory_edges_type CHECK
                    (edge_type IN ('contains','derives_from')),
                CONSTRAINT ck_memory_edges_checksum CHECK (length(edge_checksum) = 64),
                CONSTRAINT ck_memory_edges_lineage_checksum CHECK
                    (length(model_lineage_checksum) = 64)
            );
            CREATE INDEX idx_memory_edges_source
                ON narrative_memory_edges(version_id, source_node_id);
            CREATE INDEX idx_memory_edges_target
                ON narrative_memory_edges(version_id, target_node_id);

            CREATE TABLE narrative_memory_source_links (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                novel_id INTEGER NOT NULL,
                version_id INTEGER NOT NULL,
                claim_id INTEGER NOT NULL,
                source_kind VARCHAR(24) NOT NULL,
                hierarchy_build_id VARCHAR(64) NOT NULL,
                evidence_node_id VARCHAR(64) NOT NULL,
                chapter_id INTEGER NOT NULL,
                chapter_number INTEGER NOT NULL,
                source_start INTEGER NOT NULL,
                source_end INTEGER NOT NULL,
                content_hash VARCHAR(64) NOT NULL,
                source_snapshot_hash VARCHAR(64) NOT NULL,
                optional_source_ref JSONB NULL,
                link_checksum VARCHAR(64) NOT NULL,
                model_lineage_checksum VARCHAR(64) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_memory_links_identity UNIQUE
                    (owner_id, novel_id, version_id, claim_id, hierarchy_build_id,
                     evidence_node_id, source_start, source_end),
                CONSTRAINT fk_memory_links_version_scope FOREIGN KEY
                    (owner_id, novel_id, version_id) REFERENCES
                    narrative_memory_versions(owner_id, novel_id, id) ON DELETE RESTRICT,
                CONSTRAINT fk_memory_links_claim_scope FOREIGN KEY
                    (owner_id, novel_id, version_id, claim_id) REFERENCES
                    narrative_memory_claims(owner_id, novel_id, version_id, id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_memory_links_evidence_leaf FOREIGN KEY
                    (hierarchy_build_id, evidence_node_id) REFERENCES
                    chunk_hierarchy_nodes(build_id, node_id) ON DELETE RESTRICT,
                CONSTRAINT fk_memory_links_chapter FOREIGN KEY (chapter_id)
                    REFERENCES chapters(id) ON DELETE RESTRICT,
                CONSTRAINT ck_memory_links_source_kind CHECK
                    (source_kind IN ('hierarchy','timeline','relationship','clue')),
                CONSTRAINT ck_memory_links_chapter_number CHECK (chapter_number > 0),
                CONSTRAINT ck_memory_links_offsets CHECK
                    (source_start >= 0 AND source_end > source_start),
                CONSTRAINT ck_memory_links_content_hash CHECK (length(content_hash) = 64),
                CONSTRAINT ck_memory_links_snapshot_hash CHECK
                    (length(source_snapshot_hash) = 64),
                CONSTRAINT ck_memory_links_checksum CHECK (length(link_checksum) = 64),
                CONSTRAINT ck_memory_links_lineage_checksum CHECK
                    (length(model_lineage_checksum) = 64)
            );
            CREATE INDEX idx_memory_links_claim
                ON narrative_memory_source_links(version_id, claim_id);
            CREATE INDEX idx_memory_links_evidence
                ON narrative_memory_source_links(hierarchy_build_id, evidence_node_id);

            CREATE TABLE narrative_memory_manifests (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                novel_id INTEGER NOT NULL,
                version_id INTEGER NOT NULL,
                manifest_schema_version VARCHAR(40) NOT NULL,
                component_counts JSONB NOT NULL,
                component_hashes JSONB NOT NULL,
                manifest_checksum VARCHAR(64) NOT NULL,
                sealed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_memory_manifests_version UNIQUE
                    (owner_id, novel_id, version_id),
                CONSTRAINT uq_memory_manifests_scope_checksum UNIQUE
                    (owner_id, novel_id, version_id, manifest_checksum),
                CONSTRAINT fk_memory_manifests_version_scope FOREIGN KEY
                    (owner_id, novel_id, version_id) REFERENCES
                    narrative_memory_versions(owner_id, novel_id, id) ON DELETE RESTRICT,
                CONSTRAINT ck_memory_manifests_checksum CHECK
                    (length(manifest_checksum) = 64)
            );
            CREATE INDEX idx_memory_manifests_checksum
                ON narrative_memory_manifests(manifest_checksum);

            CREATE TABLE narrative_memory_validation_reports (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                novel_id INTEGER NOT NULL,
                version_id INTEGER NOT NULL,
                manifest_checksum VARCHAR(64) NOT NULL,
                validator_version VARCHAR(80) NOT NULL,
                policy_version VARCHAR(80) NOT NULL,
                verdict VARCHAR(32) NOT NULL,
                reason_codes JSONB NOT NULL,
                observed_counts JSONB NOT NULL,
                report_checksum VARCHAR(64) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_memory_reports_scope_checksum UNIQUE
                    (owner_id, novel_id, version_id, report_checksum),
                CONSTRAINT fk_memory_reports_version_scope FOREIGN KEY
                    (owner_id, novel_id, version_id) REFERENCES
                    narrative_memory_versions(owner_id, novel_id, id) ON DELETE RESTRICT,
                CONSTRAINT fk_memory_reports_manifest_scope FOREIGN KEY
                    (owner_id, novel_id, version_id, manifest_checksum) REFERENCES
                    narrative_memory_manifests
                    (owner_id, novel_id, version_id, manifest_checksum) ON DELETE RESTRICT,
                CONSTRAINT ck_memory_reports_verdict CHECK
                    (verdict IN ('qualified_candidate','blocked')),
                CONSTRAINT ck_memory_reports_checksum CHECK (length(report_checksum) = 64)
            );
            CREATE INDEX idx_memory_reports_version
                ON narrative_memory_validation_reports(version_id);
            CREATE INDEX idx_memory_reports_manifest
                ON narrative_memory_validation_reports(manifest_checksum);
            """
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    _create_frozen_tables(bind)

    bind.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION narrative_memory_append_only_guard()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'append_only_violation: % does not allow %',
                    TG_TABLE_NAME, TG_OP
                    USING ERRCODE = 'integrity_constraint_violation';
            END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION narrative_memory_version_scope_guard()
            RETURNS trigger AS $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM novels n
                    WHERE n.id = NEW.novel_id AND n.owner_id = NEW.owner_id
                ) THEN
                    RAISE EXCEPTION 'memory_version_scope_mismatch'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION narrative_memory_seal_guard()
            RETURNS trigger AS $$
            BEGIN
                PERFORM 1 FROM narrative_memory_versions v
                 WHERE v.id = NEW.version_id
                   AND v.owner_id = NEW.owner_id
                   AND v.novel_id = NEW.novel_id
                 FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'memory_content_version_scope_mismatch'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF EXISTS (
                    SELECT 1 FROM narrative_memory_manifests m
                    WHERE m.owner_id = NEW.owner_id
                      AND m.novel_id = NEW.novel_id
                      AND m.version_id = NEW.version_id
                ) THEN
                    RAISE EXCEPTION 'sealed_candidate_violation: %', TG_TABLE_NAME
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION narrative_memory_manifest_lock_guard()
            RETURNS trigger AS $$
            BEGIN
                PERFORM 1 FROM narrative_memory_versions v
                 WHERE v.id = NEW.version_id
                   AND v.owner_id = NEW.owner_id
                   AND v.novel_id = NEW.novel_id
                 FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'memory_manifest_version_scope_mismatch'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION narrative_memory_source_link_scope_guard()
            RETURNS trigger AS $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1
                    FROM narrative_memory_versions v
                    JOIN novels novel
                      ON novel.id = v.novel_id AND novel.owner_id = v.owner_id
                    JOIN chunk_builds build
                      ON build.build_id = v.hierarchy_build_id
                     AND build.novel_id = v.novel_id
                     AND build.source_snapshot_hash = v.source_snapshot_hash
                     AND build.immutable IS TRUE
                    JOIN chunk_hierarchy_nodes leaf
                      ON leaf.build_id = build.build_id
                     AND leaf.node_id = NEW.evidence_node_id
                     AND leaf.novel_id = v.novel_id
                    WHERE v.id = NEW.version_id
                      AND v.owner_id = NEW.owner_id
                      AND v.novel_id = NEW.novel_id
                      AND v.hierarchy_build_id = NEW.hierarchy_build_id
                      AND v.source_snapshot_hash = NEW.source_snapshot_hash
                      AND leaf.level = 'evidence'
                      AND leaf.chapter_id = NEW.chapter_id
                      AND leaf.chapter_number = NEW.chapter_number
                      AND leaf.source_start = NEW.source_start
                      AND leaf.source_end = NEW.source_end
                      AND leaf.content_hash = NEW.content_hash
                ) THEN
                    RAISE EXCEPTION 'source_link_scope_or_snapshot_mismatch'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION narrative_memory_edge_graph_guard()
            RETURNS trigger AS $$
            DECLARE
                source_kind text;
                target_kind text;
                source_start integer;
                source_end integer;
                target_start integer;
                target_end integer;
            BEGIN
                PERFORM 1 FROM narrative_memory_versions v
                 WHERE v.id = NEW.version_id
                   AND v.owner_id = NEW.owner_id
                   AND v.novel_id = NEW.novel_id
                 FOR UPDATE;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'memory_edge_version_scope_mismatch'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                SELECT node_kind, chapter_start, chapter_end
                  INTO source_kind, source_start, source_end
                  FROM narrative_memory_nodes
                 WHERE owner_id = NEW.owner_id AND novel_id = NEW.novel_id
                   AND version_id = NEW.version_id AND id = NEW.source_node_id;
                SELECT node_kind, chapter_start, chapter_end
                  INTO target_kind, target_start, target_end
                  FROM narrative_memory_nodes
                 WHERE owner_id = NEW.owner_id AND novel_id = NEW.novel_id
                   AND version_id = NEW.version_id AND id = NEW.target_node_id;

                IF source_kind IS NULL OR target_kind IS NULL THEN
                    RAISE EXCEPTION 'memory_edge_scope_mismatch'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF source_start > target_start OR source_end < target_end THEN
                    RAISE EXCEPTION 'memory_edge_range_violation'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF NOT (
                    (source_kind = 'global_story'
                     AND target_kind IN ('story_arc', 'volume'))
                    OR (source_kind IN ('story_arc', 'volume')
                        AND target_kind = 'chapter_state')
                ) THEN
                    RAISE EXCEPTION 'memory_edge_transition_violation: % -> %',
                        source_kind, target_kind
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                IF EXISTS (
                    WITH RECURSIVE reachable(node_id) AS (
                        SELECT e.target_node_id
                        FROM narrative_memory_edges e
                        WHERE e.owner_id = NEW.owner_id
                          AND e.novel_id = NEW.novel_id
                          AND e.version_id = NEW.version_id
                          AND e.source_node_id = NEW.target_node_id
                        UNION
                        SELECT e.target_node_id
                        FROM narrative_memory_edges e
                        JOIN reachable r ON e.source_node_id = r.node_id
                        WHERE e.owner_id = NEW.owner_id
                          AND e.novel_id = NEW.novel_id
                          AND e.version_id = NEW.version_id
                    )
                    SELECT 1 FROM reachable WHERE node_id = NEW.source_node_id
                ) THEN
                    RAISE EXCEPTION 'memory_edge_cycle_violation'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )

    for table_name in AUTHORITY_TABLES:
        bind.execute(
            text(
                f"CREATE TRIGGER trg_{table_name}_append_only "
                f"BEFORE UPDATE OR DELETE ON {table_name} FOR EACH ROW "
                "EXECUTE FUNCTION narrative_memory_append_only_guard()"
            )
        )
    for table_name in CONTENT_TABLES:
        bind.execute(
            text(
                f"CREATE TRIGGER trg_{table_name}_seal_guard "
                f"BEFORE INSERT ON {table_name} FOR EACH ROW "
                "EXECUTE FUNCTION narrative_memory_seal_guard()"
            )
        )
    bind.execute(
        text(
            """
            CREATE TRIGGER trg_narrative_memory_versions_scope
            BEFORE INSERT ON narrative_memory_versions
            FOR EACH ROW EXECUTE FUNCTION narrative_memory_version_scope_guard();

            CREATE TRIGGER trg_narrative_memory_source_links_scope
            BEFORE INSERT ON narrative_memory_source_links
            FOR EACH ROW
            EXECUTE FUNCTION narrative_memory_source_link_scope_guard();

            CREATE TRIGGER trg_narrative_memory_manifests_version_lock
            BEFORE INSERT ON narrative_memory_manifests
            FOR EACH ROW
            EXECUTE FUNCTION narrative_memory_manifest_lock_guard();

            CREATE CONSTRAINT TRIGGER trg_narrative_memory_edges_graph
            AFTER INSERT ON narrative_memory_edges
            DEFERRABLE INITIALLY IMMEDIATE
            FOR EACH ROW EXECUTE FUNCTION narrative_memory_edge_graph_guard();
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text(
            "DROP TRIGGER IF EXISTS trg_narrative_memory_edges_graph "
            "ON narrative_memory_edges"
        )
    )
    bind.execute(
        text(
            "DROP TRIGGER IF EXISTS trg_narrative_memory_manifests_version_lock "
            "ON narrative_memory_manifests"
        )
    )
    bind.execute(
        text(
            "DROP TRIGGER IF EXISTS trg_narrative_memory_source_links_scope "
            "ON narrative_memory_source_links"
        )
    )
    bind.execute(
        text(
            "DROP TRIGGER IF EXISTS trg_narrative_memory_versions_scope "
            "ON narrative_memory_versions"
        )
    )
    for table_name in CONTENT_TABLES:
        bind.execute(
            text(f"DROP TRIGGER IF EXISTS trg_{table_name}_seal_guard ON {table_name}")
        )
    for table_name in AUTHORITY_TABLES:
        bind.execute(
            text(f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only ON {table_name}")
        )
    for function_name in (
        "narrative_memory_edge_graph_guard",
        "narrative_memory_source_link_scope_guard",
        "narrative_memory_manifest_lock_guard",
        "narrative_memory_seal_guard",
        "narrative_memory_version_scope_guard",
        "narrative_memory_append_only_guard",
    ):
        bind.execute(text(f"DROP FUNCTION IF EXISTS {function_name}()"))

    for table_name in reversed(AUTHORITY_TABLES):
        op.drop_table(table_name)
