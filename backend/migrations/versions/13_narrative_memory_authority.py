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


def upgrade() -> None:
    from app.models import Base

    bind = op.get_bind()
    for table_name in AUTHORITY_TABLES:
        Base.metadata.tables[table_name].create(bind=bind, checkfirst=True)

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
                IF NEW.edge_type = 'contains' AND NOT (
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
        "narrative_memory_seal_guard",
        "narrative_memory_version_scope_guard",
        "narrative_memory_append_only_guard",
    ):
        bind.execute(text(f"DROP FUNCTION IF EXISTS {function_name}()"))

    from app.models import Base

    for table_name in reversed(AUTHORITY_TABLES):
        Base.metadata.tables[table_name].drop(bind=bind, checkfirst=True)
