"""Phase 11 clue tracking authority.

Revision ID: 11cluetrack01
Revises: 12readerchat01
"""

from alembic import op
from sqlalchemy import text

revision = "11cluetrack01"
down_revision = "12readerchat01"
branch_labels = None
depends_on = None


CLUE_TABLES = [
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
]

# Physically append-only: supersession is always INSERT of a new row.
APPEND_ONLY_TABLES = (
    "clue_lifecycle_events",
    "clue_overrides",
    "clue_pointer_journal",
)


def upgrade() -> None:
    # ORM tables are the single DDL contract; dependency order is explicit here.
    from app.models import Base  # imports all model modules into metadata

    bind = op.get_bind()
    for name in CLUE_TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)

    # Physical append-only enforcement for lifecycle history, overrides and journals.
    bind.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION clue_append_only_guard()
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
                EXECUTE FUNCTION clue_append_only_guard();
                """
            )
        )

    # paid_off rows must carry ordered cue/payoff narrative coordinates.
    bind.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION clue_paid_off_order_guard()
            RETURNS trigger AS $$
            BEGIN
                IF NEW.to_status = 'paid_off' THEN
                    IF NEW.cue_chapter IS NULL OR NEW.payoff_chapter IS NULL
                       OR NEW.cue_source_start IS NULL OR NEW.payoff_source_start IS NULL THEN
                        RAISE EXCEPTION
                            'paid_off_requires_cue_payoff_coordinates'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                    IF (NEW.payoff_chapter < NEW.cue_chapter)
                       OR (NEW.payoff_chapter = NEW.cue_chapter
                           AND NEW.payoff_source_start <= NEW.cue_source_start) THEN
                        RAISE EXCEPTION
                            'paid_off_requires_later_payoff'
                            USING ERRCODE = 'integrity_constraint_violation';
                    END IF;
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
    bind.execute(
        text(
            """
            DROP TRIGGER IF EXISTS trg_clue_lifecycle_paid_off_order
                ON clue_lifecycle_events;
            CREATE TRIGGER trg_clue_lifecycle_paid_off_order
            BEFORE INSERT ON clue_lifecycle_events
            FOR EACH ROW
            EXECUTE FUNCTION clue_paid_off_order_guard();
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        text(
            "DROP TRIGGER IF EXISTS trg_clue_lifecycle_paid_off_order "
            "ON clue_lifecycle_events"
        )
    )
    bind.execute(text("DROP FUNCTION IF EXISTS clue_paid_off_order_guard()"))
    for table_name in APPEND_ONLY_TABLES:
        bind.execute(
            text(
                f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only ON {table_name}"
            )
        )
    bind.execute(text("DROP FUNCTION IF EXISTS clue_append_only_guard()"))

    from app.models import Base

    for name in reversed(CLUE_TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
