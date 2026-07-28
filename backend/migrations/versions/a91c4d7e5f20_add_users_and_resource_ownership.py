"""add users and resource ownership

Revision ID: a91c4d7e5f20
Revises: f3c8b7b2dbf7
Create Date: 2026-06-11 16:20:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a91c4d7e5f20"
down_revision: Union[str, Sequence[str], None] = "f3c8b7b2dbf7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=50), nullable=False),
        sa.Column("email", sa.String(length=100), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_superuser", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)

    legacy_owner_id = 1
    op.execute(
        sa.text(
            "INSERT INTO users (id, username, email, hashed_password, is_active, is_superuser) "
            "VALUES (1, 'legacy-owner', 'legacy-owner@invalid.local', "
            "'!disabled-migration-account!', false, true)"
        )
    )
    op.execute(sa.text("SELECT setval(pg_get_serial_sequence('users', 'id'), 1, true)"))

    op.add_column("novels", sa.Column("owner_id", sa.Integer(), nullable=True))
    op.add_column("ai_model_configs", sa.Column("owner_id", sa.Integer(), nullable=True))
    op.execute(sa.text(f"UPDATE novels SET owner_id = {legacy_owner_id}"))
    op.execute(sa.text(f"UPDATE ai_model_configs SET owner_id = {legacy_owner_id}"))
    op.alter_column("novels", "owner_id", nullable=False)
    op.alter_column("ai_model_configs", "owner_id", nullable=False)

    op.create_foreign_key("fk_novels_owner_id_users", "novels", "users", ["owner_id"], ["id"], ondelete="CASCADE")
    op.create_foreign_key(
        "fk_ai_model_configs_owner_id_users",
        "ai_model_configs",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_novels_owner_id"), "novels", ["owner_id"], unique=False)
    op.create_index(op.f("ix_ai_model_configs_owner_id"), "ai_model_configs", ["owner_id"], unique=False)
    op.drop_constraint("ai_model_configs_name_key", "ai_model_configs", type_="unique")
    op.create_unique_constraint("uq_ai_model_owner_name", "ai_model_configs", ["owner_id", "name"])


def downgrade() -> None:
    op.drop_constraint("uq_ai_model_owner_name", "ai_model_configs", type_="unique")
    op.create_unique_constraint("ai_model_configs_name_key", "ai_model_configs", ["name"])
    op.drop_index(op.f("ix_ai_model_configs_owner_id"), table_name="ai_model_configs")
    op.drop_index(op.f("ix_novels_owner_id"), table_name="novels")
    op.drop_constraint("fk_ai_model_configs_owner_id_users", "ai_model_configs", type_="foreignkey")
    op.drop_constraint("fk_novels_owner_id_users", "novels", type_="foreignkey")
    op.drop_column("ai_model_configs", "owner_id")
    op.drop_column("novels", "owner_id")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_table("users")
