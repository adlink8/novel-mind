"""Phase 10 reader-chat conversations authority.

Revision ID: 12readerchat01
Revises: 11relobserve01
"""

from alembic import op
from sqlalchemy import text

revision = "12readerchat01"
down_revision = "11relobserve01"
branch_labels = None
depends_on = None


READER_CHAT_TABLES = [
    "reader_conversations",
    "reader_messages",
    "reader_message_selections",
    "reader_context_manifests",
    "reader_context_evidence_refs",
    "reader_message_citations",
    "reader_budget_ledgers",
    "reader_budget_reservations",
    "reader_generation_jobs",
    "reader_model_call_attempts",
]


def upgrade() -> None:
    # ORM tables are the single DDL contract; dependency order is explicit here.
    # Fail closed if Phase 09 did not leave a single head for us to extend.
    from app.models import Base  # imports all model modules into metadata

    bind = op.get_bind()
    for name in READER_CHAT_TABLES:
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)

    # Selections and manifests may only attach to user-role messages (D-03).
    bind.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION reader_chat_user_message_guard()
            RETURNS trigger AS $$
            DECLARE
                msg_role text;
            BEGIN
                SELECT role INTO msg_role
                FROM reader_messages
                WHERE id = NEW.user_message_id;
                IF msg_role IS DISTINCT FROM 'user' THEN
                    RAISE EXCEPTION
                        'reader_chat_user_message_required: % requires role=user',
                        TG_TABLE_NAME
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
    for table_name in ("reader_message_selections", "reader_context_manifests"):
        bind.execute(
            text(
                f"""
                DROP TRIGGER IF EXISTS trg_{table_name}_user_role ON {table_name};
                CREATE TRIGGER trg_{table_name}_user_role
                BEFORE INSERT OR UPDATE OF user_message_id ON {table_name}
                FOR EACH ROW
                EXECUTE FUNCTION reader_chat_user_message_guard();
                """
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    for table_name in ("reader_message_selections", "reader_context_manifests"):
        bind.execute(
            text(f"DROP TRIGGER IF EXISTS trg_{table_name}_user_role ON {table_name}")
        )
    bind.execute(text("DROP FUNCTION IF EXISTS reader_chat_user_message_guard()"))

    from app.models import Base

    for name in reversed(READER_CHAT_TABLES):
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
