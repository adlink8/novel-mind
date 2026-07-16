"""Add candidate-only narrative memory qualification authority.

Revision ID: 17memqual01
Revises: 16memrebuild01
"""

from alembic import op
from sqlalchemy import text

revision = "17memqual01"
down_revision = "16memrebuild01"
branch_labels = None
depends_on = None

QUAL_TABLES = (
    "narrative_memory_qualification_reports",
    "narrative_memory_qualification_case_results",
    "narrative_memory_qualification_runs",
)


def _create_tables(bind) -> None:
    bind.execute(
        text(
            """
            CREATE TABLE narrative_memory_qualification_runs (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                novel_id INTEGER NOT NULL REFERENCES novels(id) ON DELETE RESTRICT,
                version_id INTEGER NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'running',
                fixture_checksum VARCHAR(64) NOT NULL,
                policy_checksum VARCHAR(64) NOT NULL,
                source_snapshot_hash VARCHAR(64) NOT NULL,
                hierarchy_build_id VARCHAR(64) NOT NULL,
                hierarchy_checksum VARCHAR(64) NOT NULL,
                candidate_manifest_checksum VARCHAR(64) NOT NULL,
                generator_lineage JSONB NOT NULL DEFAULT '{}'::jsonb,
                judge_lineage JSONB NOT NULL DEFAULT '{}'::jsonb,
                pricing_checksum VARCHAR(64) NOT NULL,
                budget_checksum VARCHAR(64) NOT NULL,
                pointer_before_digest VARCHAR(64) NOT NULL,
                lineage JSONB NOT NULL DEFAULT '{}'::jsonb,
                completed_at TIMESTAMPTZ NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_nm_qual_runs_identity UNIQUE (
                    owner_id, novel_id, version_id, fixture_checksum, policy_checksum
                ),
                CONSTRAINT uq_nm_qual_runs_owner_novel_id UNIQUE (owner_id, novel_id, id),
                CONSTRAINT fk_nm_qual_runs_version FOREIGN KEY
                    (owner_id, novel_id, version_id) REFERENCES
                    narrative_memory_versions(owner_id, novel_id, id)
                    ON DELETE RESTRICT,
                CONSTRAINT ck_nm_qual_runs_status CHECK (
                    status IN ('running','completed','blocked')
                ),
                CONSTRAINT ck_nm_qual_runs_fx CHECK (length(fixture_checksum) = 64),
                CONSTRAINT ck_nm_qual_runs_pol CHECK (length(policy_checksum) = 64),
                CONSTRAINT ck_nm_qual_runs_snap CHECK (length(source_snapshot_hash) = 64),
                CONSTRAINT ck_nm_qual_runs_hier CHECK (length(hierarchy_checksum) = 64),
                CONSTRAINT ck_nm_qual_runs_man CHECK (
                    length(candidate_manifest_checksum) = 64
                ),
                CONSTRAINT ck_nm_qual_runs_ptr CHECK (length(pointer_before_digest) = 64),
                CONSTRAINT ck_nm_qual_runs_price CHECK (length(pricing_checksum) = 64),
                CONSTRAINT ck_nm_qual_runs_budget CHECK (length(budget_checksum) = 64)
            );
            CREATE INDEX idx_nm_qual_runs_scope
                ON narrative_memory_qualification_runs(owner_id, novel_id, version_id);

            CREATE TABLE narrative_memory_qualification_case_results (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                novel_id INTEGER NOT NULL,
                run_id INTEGER NOT NULL,
                case_key VARCHAR(180) NOT NULL,
                strategy VARCHAR(40) NOT NULL,
                bucket VARCHAR(40) NOT NULL,
                artifact_checksum VARCHAR(64) NOT NULL,
                usage_checksum VARCHAR(64) NOT NULL,
                sanitized_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
                artifact JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_nm_qual_cases_identity UNIQUE (run_id, case_key, strategy),
                CONSTRAINT uq_nm_qual_cases_scope UNIQUE (owner_id, novel_id, run_id, id),
                CONSTRAINT fk_nm_qual_cases_run FOREIGN KEY
                    (owner_id, novel_id, run_id) REFERENCES
                    narrative_memory_qualification_runs(owner_id, novel_id, id)
                    ON DELETE CASCADE,
                CONSTRAINT ck_nm_qual_cases_strategy CHECK (
                    strategy IN ('hierarchical_candidate','leaf_raw_baseline')
                ),
                CONSTRAINT ck_nm_qual_cases_art CHECK (length(artifact_checksum) = 64),
                CONSTRAINT ck_nm_qual_cases_usage CHECK (length(usage_checksum) = 64)
            );
            CREATE INDEX idx_nm_qual_cases_run
                ON narrative_memory_qualification_case_results(run_id);

            CREATE TABLE narrative_memory_qualification_reports (
                id SERIAL PRIMARY KEY,
                owner_id INTEGER NOT NULL,
                novel_id INTEGER NOT NULL,
                run_id INTEGER NOT NULL,
                qualification_kind VARCHAR(40) NOT NULL DEFAULT 'single_book_candidate',
                verdict VARCHAR(32) NOT NULL,
                reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
                metric_payload_checksum VARCHAR(64) NOT NULL,
                verifier_checksum VARCHAR(64) NOT NULL,
                pointer_after_digest VARCHAR(64) NOT NULL,
                command_payload_checksum VARCHAR(64) NOT NULL,
                output_digest VARCHAR(64) NOT NULL,
                disclaimer TEXT NOT NULL,
                report_body JSONB NOT NULL DEFAULT '{}'::jsonb,
                sealed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                CONSTRAINT uq_nm_qual_reports_run UNIQUE (run_id),
                CONSTRAINT uq_nm_qual_reports_scope UNIQUE (owner_id, novel_id, run_id, id),
                CONSTRAINT fk_nm_qual_reports_run FOREIGN KEY
                    (owner_id, novel_id, run_id) REFERENCES
                    narrative_memory_qualification_runs(owner_id, novel_id, id)
                    ON DELETE CASCADE,
                CONSTRAINT ck_nm_qual_reports_verdict CHECK (
                    verdict IN ('qualified_candidate','blocked')
                ),
                CONSTRAINT ck_nm_qual_reports_kind CHECK (
                    qualification_kind = 'single_book_candidate'
                ),
                CONSTRAINT ck_nm_qual_reports_metric CHECK (
                    length(metric_payload_checksum) = 64
                ),
                CONSTRAINT ck_nm_qual_reports_ver CHECK (length(verifier_checksum) = 64),
                CONSTRAINT ck_nm_qual_reports_ptr CHECK (length(pointer_after_digest) = 64),
                CONSTRAINT ck_nm_qual_reports_cmd CHECK (
                    length(command_payload_checksum) = 64
                ),
                CONSTRAINT ck_nm_qual_reports_out CHECK (length(output_digest) = 64)
            );
            CREATE INDEX idx_nm_qual_reports_run
                ON narrative_memory_qualification_reports(run_id);
            """
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    _create_tables(bind)
    bind.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION narrative_memory_qualification_append_only()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'append_only_violation: % does not allow %',
                    TG_TABLE_NAME, TG_OP
                    USING ERRCODE = 'integrity_constraint_violation';
            END;
            $$ LANGUAGE plpgsql;

            CREATE TRIGGER trg_nm_qual_runs_no_update
                BEFORE UPDATE ON narrative_memory_qualification_runs
                FOR EACH ROW EXECUTE FUNCTION narrative_memory_qualification_append_only();
            CREATE TRIGGER trg_nm_qual_runs_no_delete
                BEFORE DELETE ON narrative_memory_qualification_runs
                FOR EACH ROW EXECUTE FUNCTION narrative_memory_qualification_append_only();

            CREATE TRIGGER trg_nm_qual_cases_no_update
                BEFORE UPDATE ON narrative_memory_qualification_case_results
                FOR EACH ROW EXECUTE FUNCTION narrative_memory_qualification_append_only();
            CREATE TRIGGER trg_nm_qual_cases_no_delete
                BEFORE DELETE ON narrative_memory_qualification_case_results
                FOR EACH ROW EXECUTE FUNCTION narrative_memory_qualification_append_only();

            CREATE TRIGGER trg_nm_qual_reports_no_update
                BEFORE UPDATE ON narrative_memory_qualification_reports
                FOR EACH ROW EXECUTE FUNCTION narrative_memory_qualification_append_only();
            CREATE TRIGGER trg_nm_qual_reports_no_delete
                BEFORE DELETE ON narrative_memory_qualification_reports
                FOR EACH ROW EXECUTE FUNCTION narrative_memory_qualification_append_only();
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text(
            """
            DROP TRIGGER IF EXISTS trg_nm_qual_reports_no_delete
                ON narrative_memory_qualification_reports;
            DROP TRIGGER IF EXISTS trg_nm_qual_reports_no_update
                ON narrative_memory_qualification_reports;
            DROP TRIGGER IF EXISTS trg_nm_qual_cases_no_delete
                ON narrative_memory_qualification_case_results;
            DROP TRIGGER IF EXISTS trg_nm_qual_cases_no_update
                ON narrative_memory_qualification_case_results;
            DROP TRIGGER IF EXISTS trg_nm_qual_runs_no_delete
                ON narrative_memory_qualification_runs;
            DROP TRIGGER IF EXISTS trg_nm_qual_runs_no_update
                ON narrative_memory_qualification_runs;
            DROP FUNCTION IF EXISTS narrative_memory_qualification_append_only();
            """
        )
    )
    for table_name in QUAL_TABLES:
        op.drop_table(table_name)
