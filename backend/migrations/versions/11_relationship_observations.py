"""Phase 09 relationship observation authority.

Revision ID: 11relobserve01
Revises: 10analysistime01
"""

from alembic import op
from sqlalchemy import text

revision = "11relobserve01"
down_revision = "10analysistime01"
branch_labels = None
depends_on = None


RELATIONSHIP_TABLES = [
    "relationship_build_runs",
    "relationship_observation_candidates",
    "relationship_observation_judgments",
    "relationship_observations",
    "relationship_evidence_links",
    "character_identity_overrides",
    "relationship_overrides",
    "relationship_projection_audits",
]

APPEND_ONLY_TABLES = (
    "relationship_observations",
    "character_identity_overrides",
    "relationship_overrides",
)


def upgrade() -> None:
    # ORM tables are the single DDL contract; dependency order is explicit here.
    from app.models import Base  # imports all model modules into metadata

    bind = op.get_bind()
    for name in RELATIONSHIP_TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)

    # Physical append-only enforcement for accepted facts and corrections.
    # Supersession is always a new INSERT; prior rows must remain byte-stable.
    bind.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION relationship_append_only_guard()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION 'append_only_violation: % does not allow %',
                    TG_TABLE_NAME, TG_OP
                    USING ERRCODE = 'integrity_constraint_violation';
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
    for table_name in APPEND_ONLY_TABLES:
        bind.execute(
            text(
                f"""
                DROP TRIGGER IF EXISTS trg_{table_name}_append_only ON {table_name};
                CREATE TRIGGER trg_{table_name}_append_only
                BEFORE UPDATE OR DELETE ON {table_name}
                FOR EACH ROW
                EXECUTE FUNCTION relationship_append_only_guard();
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in APPEND_ONLY_TABLES:
        bind.execute(
            text(f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only ON {table_name}")
        )
    bind.execute(text("DROP FUNCTION IF EXISTS relationship_append_only_guard()"))

    from app.models import Base

    for name in reversed(RELATIONSHIP_TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
