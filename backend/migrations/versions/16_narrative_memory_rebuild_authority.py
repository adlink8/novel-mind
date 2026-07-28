"""Add candidate-only narrative memory rebuild authority.

Revision ID: 16memrebuild01
Revises: 14membuild01
"""

from alembic import op
from sqlalchemy import text

revision = "16memrebuild01"
down_revision = "14membuild01"
branch_labels = None
depends_on = None

REBUILD_TABLES = (
    "narrative_memory_reuse_reports",
    "narrative_memory_rebuild_items",
    "narrative_memory_rebuild_plans",
)


def _create_rebuild_tables(bind) -> None:
    """Create revision-owned schema without consulting runtime metadata."""
    bind.execute(
        text(
            """
            CREATE TABLE narrative_memory_rebuild_plans (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                novel_id INTEGER NOT NULL REFERENCES novels(id) ON DELETE RESTRICT,
                parent_version_id INTEGER NOT NULL,
                target_version_id INTEGER NOT NULL,
                old_source_snapshot_hash VARCHAR(64) NOT NULL,
                new_source_snapshot_hash VARCHAR(64) NOT NULL,
                old_hierarchy_build_id VARCHAR(64) NOT NULL,
                new_hierarchy_build_id VARCHAR(64) NOT NULL,
                old_hierarchy_checksum VARCHAR(64) NOT NULL,
                new_hierarchy_checksum VARCHAR(64) NOT NULL,
                boundary_plan JSONB NOT NULL,
                boundary_plan_checksum VARCHAR(64) NOT NULL,
                oracle_policy_version VARCHAR(80) NOT NULL,
                oracle_policy_checksum VARCHAR(64) NOT NULL,
                compatibility_policy_checksum VARCHAR(64) NOT NULL,
                graph_checksum VARCHAR(64) NOT NULL,
                plan_checksum VARCHAR(64) NOT NULL,
                change_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
                eligibility_report_checksum VARCHAR(64) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_memory_rebuild_plans_scope
                    UNIQUE (owner_id, novel_id, parent_version_id, target_version_id),
                CONSTRAINT uq_memory_rebuild_plans_id_scope
                    UNIQUE (owner_id, novel_id, parent_version_id, target_version_id, id),
                CONSTRAINT uq_memory_rebuild_plans_owner_novel_id
                    UNIQUE (owner_id, novel_id, id),
                CONSTRAINT uq_memory_rebuild_plans_checksum
                    UNIQUE (owner_id, novel_id, plan_checksum),
                CONSTRAINT fk_memory_rebuild_plans_parent FOREIGN KEY
                    (owner_id, novel_id, parent_version_id) REFERENCES
                    narrative_memory_versions(owner_id, novel_id, id)
                    ON DELETE RESTRICT,
                CONSTRAINT fk_memory_rebuild_plans_target FOREIGN KEY
                    (owner_id, novel_id, target_version_id) REFERENCES
                    narrative_memory_versions(owner_id, novel_id, id)
                    ON DELETE RESTRICT,
                CONSTRAINT ck_memory_rebuild_plans_distinct_versions
                    CHECK (parent_version_id <> target_version_id),
                CONSTRAINT ck_memory_rebuild_plans_old_snapshot
                    CHECK (length(old_source_snapshot_hash) = 64),
                CONSTRAINT ck_memory_rebuild_plans_new_snapshot
                    CHECK (length(new_source_snapshot_hash) = 64),
                CONSTRAINT ck_memory_rebuild_plans_old_hierarchy
                    CHECK (length(old_hierarchy_checksum) = 64),
                CONSTRAINT ck_memory_rebuild_plans_new_hierarchy
                    CHECK (length(new_hierarchy_checksum) = 64),
                CONSTRAINT ck_memory_rebuild_plans_boundary
                    CHECK (length(boundary_plan_checksum) = 64),
                CONSTRAINT ck_memory_rebuild_plans_oracle_policy
                    CHECK (length(oracle_policy_checksum) = 64),
                CONSTRAINT ck_memory_rebuild_plans_compat_policy
                    CHECK (length(compatibility_policy_checksum) = 64),
                CONSTRAINT ck_memory_rebuild_plans_graph
                    CHECK (length(graph_checksum) = 64),
                CONSTRAINT ck_memory_rebuild_plans_plan
                    CHECK (length(plan_checksum) = 64),
                CONSTRAINT ck_memory_rebuild_plans_eligibility
                    CHECK (length(eligibility_report_checksum) = 64)
            );
            CREATE INDEX idx_memory_rebuild_plans_scope
                ON narrative_memory_rebuild_plans(owner_id, novel_id);
            CREATE INDEX idx_memory_rebuild_plans_versions
                ON narrative_memory_rebuild_plans(parent_version_id, target_version_id);

            CREATE TABLE narrative_memory_rebuild_items (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                novel_id INTEGER NOT NULL,
                plan_id INTEGER NOT NULL,
                asset_key VARCHAR(180) NOT NULL,
                asset_kind VARCHAR(40) NOT NULL,
                chapter_start INTEGER NULL,
                chapter_end INTEGER NULL,
                decision VARCHAR(32) NOT NULL,
                direct_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
                propagated_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
                predecessor_keys JSONB NOT NULL DEFAULT '[]'::jsonb,
                old_content_checksum VARCHAR(64) NULL,
                new_content_checksum VARCHAR(64) NULL,
                dependency_checksum VARCHAR(64) NULL,
                stage_key VARCHAR(180) NULL,
                detail JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_memory_rebuild_items_key UNIQUE (plan_id, asset_key),
                CONSTRAINT uq_memory_rebuild_items_scope
                    UNIQUE (owner_id, novel_id, plan_id, id),
                CONSTRAINT fk_memory_rebuild_items_plan_scope FOREIGN KEY
                    (owner_id, novel_id, plan_id) REFERENCES
                    narrative_memory_rebuild_plans(owner_id, novel_id, id)
                    ON DELETE CASCADE,
                CONSTRAINT ck_memory_rebuild_items_kind CHECK (asset_kind IN (
                    'source_chapter','evidence_leaf','chapter_state','story_arc',
                    'volume','global_story','boundary_plan','optional_source'
                )),
                CONSTRAINT ck_memory_rebuild_items_decision CHECK (decision IN (
                    'dirty','carried','stale_blocked','not_applicable'
                )),
                CONSTRAINT ck_memory_rebuild_items_range CHECK (
                    chapter_start IS NULL OR (
                        chapter_start > 0 AND chapter_end IS NOT NULL
                        AND chapter_end >= chapter_start
                    )
                ),
                CONSTRAINT ck_memory_rebuild_items_old_cs CHECK (
                    old_content_checksum IS NULL OR length(old_content_checksum) = 64
                ),
                CONSTRAINT ck_memory_rebuild_items_new_cs CHECK (
                    new_content_checksum IS NULL OR length(new_content_checksum) = 64
                ),
                CONSTRAINT ck_memory_rebuild_items_dep_cs CHECK (
                    dependency_checksum IS NULL OR length(dependency_checksum) = 64
                )
            );
            CREATE INDEX idx_memory_rebuild_items_plan
                ON narrative_memory_rebuild_items(plan_id);
            CREATE INDEX idx_memory_rebuild_items_decision
                ON narrative_memory_rebuild_items(plan_id, decision);

            CREATE TABLE narrative_memory_reuse_reports (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                novel_id INTEGER NOT NULL,
                plan_id INTEGER NOT NULL,
                parent_version_id INTEGER NOT NULL,
                target_version_id INTEGER NOT NULL,
                plan_checksum VARCHAR(64) NOT NULL,
                parent_manifest_checksum VARCHAR(64) NULL,
                target_manifest_checksum VARCHAR(64) NULL,
                rebuilt_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
                carried_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
                stale_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
                dirty_ranges JSONB NOT NULL DEFAULT '[]'::jsonb,
                observed_actual JSONB NOT NULL DEFAULT '{}'::jsonb,
                full_rebuild_upper_bound JSONB NOT NULL DEFAULT '{}'::jsonb,
                avoided_upper_bound JSONB NOT NULL DEFAULT '{}'::jsonb,
                cache_reuse JSONB NOT NULL DEFAULT '{}'::jsonb,
                carry_reuse JSONB NOT NULL DEFAULT '{}'::jsonb,
                formula_inputs JSONB NOT NULL DEFAULT '{}'::jsonb,
                report_checksum VARCHAR(64) NOT NULL,
                body JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at_immutable TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_memory_reuse_reports_checksum
                    UNIQUE (owner_id, novel_id, plan_id, report_checksum),
                CONSTRAINT fk_memory_reuse_reports_plan_scope FOREIGN KEY
                    (owner_id, novel_id, plan_id) REFERENCES
                    narrative_memory_rebuild_plans(owner_id, novel_id, id)
                    ON DELETE RESTRICT,
                CONSTRAINT ck_memory_reuse_reports_checksum
                    CHECK (length(report_checksum) = 64),
                CONSTRAINT ck_memory_reuse_reports_plan_cs
                    CHECK (length(plan_checksum) = 64)
            );
            CREATE INDEX idx_memory_reuse_reports_plan
                ON narrative_memory_reuse_reports(plan_id);
            """
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    _create_rebuild_tables(bind)
    bind.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION narrative_memory_rebuild_append_only()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'append_only_violation: % does not allow %',
                    TG_TABLE_NAME, TG_OP
                    USING ERRCODE = 'integrity_constraint_violation';
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER trg_memory_rebuild_plans_no_update
                BEFORE UPDATE ON narrative_memory_rebuild_plans
                FOR EACH ROW EXECUTE FUNCTION narrative_memory_rebuild_append_only();
            CREATE TRIGGER trg_memory_rebuild_plans_no_delete
                BEFORE DELETE ON narrative_memory_rebuild_plans
                FOR EACH ROW EXECUTE FUNCTION narrative_memory_rebuild_append_only();

            CREATE TRIGGER trg_memory_rebuild_items_no_update
                BEFORE UPDATE ON narrative_memory_rebuild_items
                FOR EACH ROW EXECUTE FUNCTION narrative_memory_rebuild_append_only();
            CREATE TRIGGER trg_memory_rebuild_items_no_delete
                BEFORE DELETE ON narrative_memory_rebuild_items
                FOR EACH ROW EXECUTE FUNCTION narrative_memory_rebuild_append_only();

            CREATE TRIGGER trg_memory_reuse_reports_no_update
                BEFORE UPDATE ON narrative_memory_reuse_reports
                FOR EACH ROW EXECUTE FUNCTION narrative_memory_rebuild_append_only();
            CREATE TRIGGER trg_memory_reuse_reports_no_delete
                BEFORE DELETE ON narrative_memory_reuse_reports
                FOR EACH ROW EXECUTE FUNCTION narrative_memory_rebuild_append_only();
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text(
            """
            DROP TRIGGER IF EXISTS trg_memory_reuse_reports_no_delete
                ON narrative_memory_reuse_reports;
            DROP TRIGGER IF EXISTS trg_memory_reuse_reports_no_update
                ON narrative_memory_reuse_reports;
            DROP TRIGGER IF EXISTS trg_memory_rebuild_items_no_delete
                ON narrative_memory_rebuild_items;
            DROP TRIGGER IF EXISTS trg_memory_rebuild_items_no_update
                ON narrative_memory_rebuild_items;
            DROP TRIGGER IF EXISTS trg_memory_rebuild_plans_no_delete
                ON narrative_memory_rebuild_plans;
            DROP TRIGGER IF EXISTS trg_memory_rebuild_plans_no_update
                ON narrative_memory_rebuild_plans;
            DROP FUNCTION IF EXISTS narrative_memory_rebuild_append_only();
            """
        )
    )
    for table_name in REBUILD_TABLES:
        op.drop_table(table_name)
