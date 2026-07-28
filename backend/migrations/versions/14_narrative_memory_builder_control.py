"""Add durable narrative-memory builder control plane.

Revision ID: 14membuild01
Revises: 13memoryauth01
"""

from alembic import op
from sqlalchemy import text

revision = "14membuild01"
down_revision = "13memoryauth01"
branch_labels = None
depends_on = None

BUILDER_TABLES = (
    "narrative_memory_build_reports",
    "narrative_memory_build_model_call_attempts",
    "narrative_memory_build_budget_reservations",
    "narrative_memory_build_budget_ledgers",
    "narrative_memory_build_stages",
    "narrative_memory_build_runs",
)


def _create_builder_tables(bind) -> None:
    """Create revision-owned schema without consulting runtime metadata."""
    bind.execute(
        text(
            """
            CREATE TABLE narrative_memory_build_runs (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                novel_id INTEGER NOT NULL REFERENCES novels(id) ON DELETE RESTRICT,
                version_id INTEGER NOT NULL,
                eligibility_report_checksum VARCHAR(64) NOT NULL,
                eligibility_policy_version VARCHAR(80) NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'pending',
                status_reason VARCHAR(160) NULL,
                lease_id VARCHAR(64) NULL,
                lease_expires_at TIMESTAMPTZ NULL,
                heartbeat_at TIMESTAMPTZ NULL,
                cancel_requested BOOLEAN NOT NULL DEFAULT false,
                progress JSONB NOT NULL DEFAULT '{}'::jsonb,
                run_policy JSONB NOT NULL DEFAULT '{}'::jsonb,
                boundary_plan JSONB NULL,
                boundary_plan_checksum VARCHAR(64) NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_memory_build_runs_version
                    UNIQUE (owner_id, novel_id, version_id),
                CONSTRAINT uq_memory_build_runs_scope
                    UNIQUE (owner_id, novel_id, version_id, id),
                CONSTRAINT fk_memory_build_runs_version_scope FOREIGN KEY
                    (owner_id, novel_id, version_id) REFERENCES
                    narrative_memory_versions(owner_id, novel_id, id)
                    ON DELETE RESTRICT,
                CONSTRAINT ck_memory_build_runs_status CHECK (status IN (
                    'pending','running','partial','paused_budget',
                    'paused_dependency','cancelled','completed','failed'
                )),
                CONSTRAINT ck_memory_build_runs_eligibility_checksum
                    CHECK (length(eligibility_report_checksum) = 64)
            );
            CREATE INDEX idx_memory_build_runs_scope
                ON narrative_memory_build_runs(owner_id, novel_id);
            CREATE INDEX idx_memory_build_runs_status
                ON narrative_memory_build_runs(status);

            CREATE TABLE narrative_memory_build_stages (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                novel_id INTEGER NOT NULL,
                version_id INTEGER NOT NULL,
                run_id INTEGER NOT NULL,
                stage_key VARCHAR(180) NOT NULL,
                stage_kind VARCHAR(40) NOT NULL,
                chapter_start INTEGER NULL,
                chapter_end INTEGER NULL,
                dependency_keys JSONB NOT NULL DEFAULT '[]'::jsonb,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                status VARCHAR(32) NOT NULL DEFAULT 'pending',
                status_reason VARCHAR(160) NULL,
                package_checksum VARCHAR(64) NULL,
                cache_key VARCHAR(128) NULL,
                artifact_checksum VARCHAR(64) NULL,
                checkpoint JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_memory_build_stages_key UNIQUE (run_id, stage_key),
                CONSTRAINT fk_memory_build_stages_run_scope FOREIGN KEY
                    (owner_id, novel_id, version_id, run_id) REFERENCES
                    narrative_memory_build_runs(owner_id, novel_id, version_id, id)
                    ON DELETE CASCADE,
                CONSTRAINT ck_memory_build_stages_kind CHECK (stage_kind IN (
                    'chapter_state','arc_volume_plan','arc_volume_aggregate',
                    'global_aggregate','manifest_validation'
                )),
                CONSTRAINT ck_memory_build_stages_status CHECK (status IN (
                    'pending','running','completed','failed','blocked_dependency',
                    'cancelled','paused_budget','paused_dependency'
                ))
            );
            CREATE INDEX idx_memory_build_stages_run
                ON narrative_memory_build_stages(run_id);
            CREATE INDEX idx_memory_build_stages_status
                ON narrative_memory_build_stages(run_id, status);

            CREATE TABLE narrative_memory_build_budget_ledgers (
                id SERIAL PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES narrative_memory_build_runs(id)
                    ON DELETE CASCADE,
                max_calls INTEGER NOT NULL,
                max_input_tokens INTEGER NOT NULL,
                max_output_tokens INTEGER NOT NULL,
                max_cost_usd NUMERIC(18, 8) NOT NULL,
                reserved_calls INTEGER NOT NULL DEFAULT 0,
                reserved_input_tokens INTEGER NOT NULL DEFAULT 0,
                reserved_output_tokens INTEGER NOT NULL DEFAULT 0,
                reserved_cost_usd NUMERIC(18, 8) NOT NULL DEFAULT 0,
                settled_calls INTEGER NOT NULL DEFAULT 0,
                settled_input_tokens INTEGER NOT NULL DEFAULT 0,
                settled_output_tokens INTEGER NOT NULL DEFAULT 0,
                settled_cost_usd NUMERIC(18, 8) NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_memory_build_budget_run UNIQUE (run_id)
            );

            CREATE TABLE narrative_memory_build_budget_reservations (
                id SERIAL PRIMARY KEY,
                ledger_id INTEGER NOT NULL REFERENCES
                    narrative_memory_build_budget_ledgers(id) ON DELETE CASCADE,
                reservation_key VARCHAR(180) NOT NULL,
                status VARCHAR(24) NOT NULL DEFAULT 'reserved',
                calls INTEGER NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cost_usd NUMERIC(18, 8) NOT NULL,
                settled_usage JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_memory_build_budget_reservation
                    UNIQUE (ledger_id, reservation_key),
                CONSTRAINT ck_memory_build_budget_reservation_status CHECK
                    (status IN ('reserved','settled','released','failed'))
            );

            CREATE TABLE narrative_memory_build_model_call_attempts (
                id SERIAL PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES narrative_memory_build_runs(id)
                    ON DELETE CASCADE,
                reservation_id INTEGER NULL REFERENCES
                    narrative_memory_build_budget_reservations(id) ON DELETE SET NULL,
                stage_key VARCHAR(180) NOT NULL,
                attempt_number INTEGER NOT NULL,
                status VARCHAR(32) NOT NULL,
                cache_key VARCHAR(128) NULL,
                cache_source_attempt_id INTEGER NULL,
                provider_request_id VARCHAR(160) NULL,
                request_hash VARCHAR(64) NOT NULL,
                response_hash VARCHAR(64) NULL,
                deployment_lineage JSONB NOT NULL DEFAULT '{}'::jsonb,
                usage JSONB NOT NULL DEFAULT '{}'::jsonb,
                cost_usd NUMERIC(18, 8) NULL,
                latency_ms INTEGER NULL,
                error_code VARCHAR(80) NULL,
                validated_output JSONB NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_memory_build_model_call_attempt
                    UNIQUE (run_id, stage_key, attempt_number),
                CONSTRAINT ck_memory_build_model_call_attempt_status CHECK
                    (status IN (
                        'started','succeeded','failed','cache_hit',
                        'cancelled','outcome_unknown','budget_rejected'
                    )),
                CONSTRAINT ck_memory_build_model_call_request_hash
                    CHECK (length(request_hash) = 64),
                CONSTRAINT fk_memory_build_model_call_cache_source FOREIGN KEY
                    (cache_source_attempt_id) REFERENCES
                    narrative_memory_build_model_call_attempts(id) ON DELETE SET NULL
            );
            CREATE INDEX idx_memory_build_model_call_run
                ON narrative_memory_build_model_call_attempts(run_id);
            CREATE INDEX idx_memory_build_model_call_cache
                ON narrative_memory_build_model_call_attempts(cache_key);

            CREATE TABLE narrative_memory_build_reports (
                id SERIAL PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES narrative_memory_build_runs(id)
                    ON DELETE CASCADE,
                owner_id INTEGER NOT NULL,
                novel_id INTEGER NOT NULL,
                version_id INTEGER NOT NULL,
                outcome VARCHAR(32) NOT NULL,
                stage_counts JSONB NOT NULL DEFAULT '{}'::jsonb,
                dependency_closure JSONB NOT NULL DEFAULT '{}'::jsonb,
                call_totals JSONB NOT NULL DEFAULT '{}'::jsonb,
                source_statuses JSONB NOT NULL DEFAULT '{}'::jsonb,
                worker_artifact_checksum VARCHAR(64) NULL,
                database_manifest_checksum VARCHAR(64) NULL,
                reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
                report_checksum VARCHAR(64) NOT NULL,
                body JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at_immutable TIMESTAMPTZ NOT NULL DEFAULT now(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_memory_build_reports_checksum
                    UNIQUE (run_id, report_checksum),
                CONSTRAINT ck_memory_build_reports_outcome CHECK (outcome IN (
                    'completed_candidate','partial','paused','cancelled','failed'
                )),
                CONSTRAINT ck_memory_build_reports_checksum
                    CHECK (length(report_checksum) = 64)
            );
            CREATE INDEX idx_memory_build_reports_run
                ON narrative_memory_build_reports(run_id);
            """
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    _create_builder_tables(bind)

    bind.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION narrative_memory_build_attempt_append_only()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'append_only_violation: % does not allow %',
                    TG_TABLE_NAME, TG_OP
                    USING ERRCODE = 'integrity_constraint_violation';
            END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION narrative_memory_build_report_append_only()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'append_only_violation: % does not allow %',
                    TG_TABLE_NAME, TG_OP
                    USING ERRCODE = 'integrity_constraint_violation';
            END;
            $$ LANGUAGE plpgsql;

            CREATE OR REPLACE FUNCTION narrative_memory_build_completed_stage_guard()
            RETURNS trigger AS $$
            BEGIN
                IF TG_OP = 'UPDATE' AND OLD.status = 'completed' THEN
                    IF NEW.status IS DISTINCT FROM OLD.status
                       OR NEW.artifact_checksum IS DISTINCT FROM OLD.artifact_checksum
                       OR NEW.package_checksum IS DISTINCT FROM OLD.package_checksum
                       OR NEW.cache_key IS DISTINCT FROM OLD.cache_key
                       OR NEW.checkpoint IS DISTINCT FROM OLD.checkpoint
                       OR NEW.stage_kind IS DISTINCT FROM OLD.stage_kind
                       OR NEW.stage_key IS DISTINCT FROM OLD.stage_key
                       OR NEW.dependency_keys IS DISTINCT FROM OLD.dependency_keys
                       OR NEW.chapter_start IS DISTINCT FROM OLD.chapter_start
                       OR NEW.chapter_end IS DISTINCT FROM OLD.chapter_end
                    THEN
                        RAISE EXCEPTION
                            'completed_stage_immutable: narrative_memory_build_stages'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                END IF;
                IF TG_OP = 'DELETE' AND OLD.status = 'completed' THEN
                    RAISE EXCEPTION
                        'completed_stage_immutable: narrative_memory_build_stages'
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER trg_memory_build_attempt_no_update
                BEFORE UPDATE ON narrative_memory_build_model_call_attempts
                FOR EACH ROW EXECUTE FUNCTION
                    narrative_memory_build_attempt_append_only();

            CREATE TRIGGER trg_memory_build_attempt_no_delete
                BEFORE DELETE ON narrative_memory_build_model_call_attempts
                FOR EACH ROW EXECUTE FUNCTION
                    narrative_memory_build_attempt_append_only();

            CREATE TRIGGER trg_memory_build_report_no_update
                BEFORE UPDATE ON narrative_memory_build_reports
                FOR EACH ROW EXECUTE FUNCTION
                    narrative_memory_build_report_append_only();

            CREATE TRIGGER trg_memory_build_report_no_delete
                BEFORE DELETE ON narrative_memory_build_reports
                FOR EACH ROW EXECUTE FUNCTION
                    narrative_memory_build_report_append_only();

            CREATE TRIGGER trg_memory_build_completed_stage_guard
                BEFORE UPDATE OR DELETE ON narrative_memory_build_stages
                FOR EACH ROW EXECUTE FUNCTION
                    narrative_memory_build_completed_stage_guard();
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text(
            """
            DROP TRIGGER IF EXISTS trg_memory_build_completed_stage_guard
                ON narrative_memory_build_stages;
            DROP TRIGGER IF EXISTS trg_memory_build_report_no_delete
                ON narrative_memory_build_reports;
            DROP TRIGGER IF EXISTS trg_memory_build_report_no_update
                ON narrative_memory_build_reports;
            DROP TRIGGER IF EXISTS trg_memory_build_attempt_no_delete
                ON narrative_memory_build_model_call_attempts;
            DROP TRIGGER IF EXISTS trg_memory_build_attempt_no_update
                ON narrative_memory_build_model_call_attempts;
            DROP FUNCTION IF EXISTS narrative_memory_build_completed_stage_guard();
            DROP FUNCTION IF EXISTS narrative_memory_build_report_append_only();
            DROP FUNCTION IF EXISTS narrative_memory_build_attempt_append_only();
            """
        )
    )
    for table_name in BUILDER_TABLES:
        op.drop_table(table_name)
