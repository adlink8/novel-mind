"""QueryPlanTrace immutable authority (26-01 / REQ-QP-01).

第八张权威表 query_plan_traces：已验证 QueryPlan 的 append-only 持久化投影。

设计约定（延续 27_approval_requests.py）:
  - 幂等 inspector 守卫 + 对称 downgrade，upgrade/downgrade 成对可逆。
  - 反规范化 owner_id / novel_id 外键（users.id / novels.id，ON DELETE
    CASCADE）；version_id 为普通 INTEGER（novel 版本号无独立版本表，沿用
    narrative_memory_build_runs.version_id 约定）。
  - JSONB 用 ``JSONB().with_variant(JSON(), "sqlite")``：PostgreSQL 渲染
    JSONB（与 ORM 模型一致，alembic check 无 drift），SQLite 测试渲染 JSON。
  - 无 active-pointer / promotion 列（D-14）；不修改任何既有 chat 表。

Revision ID: 20260801_2601
Revises: 27approval01
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260801_2601"
down_revision = "27approval01"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    """Upgrade schema（幂等 inspector 守卫：表已存在则跳过）。"""
    insp = sa.inspect(op.get_bind())
    if insp.has_table("query_plan_traces"):
        return
    op.create_table(
        "query_plan_traces",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("trace_id", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column(
            "owner_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "novel_id",
            sa.Integer,
            sa.ForeignKey("novels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_id", sa.Integer, nullable=False),
        sa.Column("cutoff_mode", sa.String(32), nullable=False),
        sa.Column("through_chapter", sa.Integer, nullable=False),
        sa.Column("full_book_authorized", sa.Boolean, nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("dataset_lineage", sa.String(128), nullable=False),
        sa.Column("canonical_payload", JSONB, nullable=False),
        sa.Column("canonical_payload_hash", sa.String(64), nullable=False),
        sa.Column("availability_checksum", sa.String(64), nullable=False),
        sa.Column("fallback", JSONB, nullable=False),
        sa.Column("blocked_reason", sa.String(120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("trace_id", name="uq_query_plan_traces_trace_id"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_query_plan_traces_idempotency"
        ),
        sa.CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_query_plan_traces_payload_hash",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_query_plan_traces_idempotency_key",
        ),
        sa.CheckConstraint(
            "cutoff_mode IN ('reading_progress','whole_book')",
            name="ck_query_plan_traces_cutoff_mode",
        ),
    )
    op.create_index(
        "idx_query_plan_traces_scope",
        "query_plan_traces",
        ["owner_id", "novel_id", "version_id"],
    )
    op.create_index(
        "idx_query_plan_traces_owner_created",
        "query_plan_traces",
        ["owner_id", "created_at"],
    )


def downgrade() -> None:
    """Downgrade schema：对称删除。"""
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("query_plan_traces"):
        return
    op.drop_index(
        "idx_query_plan_traces_owner_created", table_name="query_plan_traces"
    )
    op.drop_index("idx_query_plan_traces_scope", table_name="query_plan_traces")
    op.drop_table("query_plan_traces")
