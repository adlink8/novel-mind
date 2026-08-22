"""World-model event fact and causal edge immutable projection (27-01).

三张权威表 world_model_events / world_model_causal_edges / world_model_conflicts：
append-only 事件事实、证据门控因果边与保留冲突（D-02 无 active-pointer /
promotion / current-revision 列，D-04 冲突保留而非覆盖）。

设计约定（延续 20260801_2601_query_plan_trace.py）:
  - 幂等 inspector 守卫 + 对称 downgrade，upgrade/downgrade 成对可逆。
  - 反规范化 owner_id / novel_id 外键（users.id / novels.id，ON DELETE
    CASCADE）；version_id 为普通 INTEGER（novel 版本号，沿用现有约定）。
  - JSONB 用 ``JSONB().with_variant(JSON(), "sqlite")``：PostgreSQL 渲染
    JSONB（与 ORM 模型一致，alembic check 无 drift），SQLite 测试渲染 JSON。
  - 每行携带 canonical payload + SHA-256 checksum + 幂等键 + 投影级
    projection_hash，restart replay 可做 byte-equivalent 证明。

Revision ID: 20260801_2701
Revises: 20260801_2601
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260801_2701"
down_revision = "20260801_2601"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

AUTHORITY_VALUES = (
    "'canon_fact','probable_inference',"
    "'literary_interpretation','user_interpretation'"
)
GATE_STATUS_VALUES = "'pending','passed','rejected'"


def _common_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
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
        sa.Column(
            "canonical_payload", JSONB, nullable=False
        ),
        sa.Column("canonical_payload_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("projection_hash", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
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
    ]


def upgrade() -> None:
    """Upgrade schema（幂等 inspector 守卫：表已存在则跳过）。"""
    insp = sa.inspect(op.get_bind())
    if insp.has_table("world_model_events"):
        return

    op.create_table(
        "world_model_events",
        *[
            sa.Column("event_key", sa.String(180), nullable=False),
            sa.Column("authority", sa.String(32), nullable=False),
            sa.Column("confidence", sa.Float, nullable=False),
            sa.Column("effective_start", sa.Integer, nullable=True),
            sa.Column("effective_end", sa.Integer, nullable=True),
            sa.Column("disclosure_cutoff", sa.Integer, nullable=False),
            sa.Column("gate_status", sa.String(16), nullable=False),
            sa.Column("source_refs", JSONB, nullable=False),
        ]
        + _common_columns(),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_world_model_events_idempotency"
        ),
        sa.CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_world_model_events_payload_hash",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_world_model_events_idempotency_key",
        ),
        sa.CheckConstraint(
            f"authority IN ({AUTHORITY_VALUES})",
            name="ck_world_model_events_authority",
        ),
        sa.CheckConstraint(
            f"gate_status IN ({GATE_STATUS_VALUES})",
            name="ck_world_model_events_gate_status",
        ),
    )
    op.create_index(
        "idx_world_model_events_scope",
        "world_model_events",
        ["owner_id", "novel_id", "version_id"],
    )

    op.create_table(
        "world_model_causal_edges",
        *[
            sa.Column("edge_key", sa.String(180), nullable=False),
            sa.Column("source_event_key", sa.String(180), nullable=False),
            sa.Column("target_event_key", sa.String(180), nullable=False),
            sa.Column("edge_type", sa.String(24), nullable=False),
            sa.Column("authority", sa.String(32), nullable=False),
            sa.Column("confidence", sa.Float, nullable=False),
            sa.Column("disclosure_cutoff", sa.Integer, nullable=False),
            sa.Column("gate_status", sa.String(16), nullable=False),
            sa.Column("source_refs", JSONB, nullable=False),
        ]
        + _common_columns(),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_world_model_edges_idempotency"
        ),
        sa.CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_world_model_edges_payload_hash",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_world_model_edges_idempotency_key",
        ),
        sa.CheckConstraint(
            "edge_type IN ('caused','triggered','responded','blocked')",
            name="ck_world_model_edges_edge_type",
        ),
        sa.CheckConstraint(
            f"authority IN ({AUTHORITY_VALUES})",
            name="ck_world_model_edges_authority",
        ),
        sa.CheckConstraint(
            f"gate_status IN ({GATE_STATUS_VALUES})",
            name="ck_world_model_edges_gate_status",
        ),
    )
    op.create_index(
        "idx_world_model_edges_scope",
        "world_model_causal_edges",
        ["owner_id", "novel_id", "version_id"],
    )

    op.create_table(
        "world_model_conflicts",
        *[
            sa.Column("conflict_key", sa.String(180), nullable=False),
            sa.Column("kind", sa.String(32), nullable=False),
            sa.Column("involved_keys", JSONB, nullable=False),
            sa.Column("description", sa.String(400), nullable=False),
        ]
        + _common_columns(),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_world_model_conflicts_idempotency"
        ),
        sa.CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_world_model_conflicts_payload_hash",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_world_model_conflicts_idempotency_key",
        ),
        sa.CheckConstraint(
            "kind IN ('temporal_conflict','assertion_conflict')",
            name="ck_world_model_conflicts_kind",
        ),
    )
    op.create_index(
        "idx_world_model_conflicts_scope",
        "world_model_conflicts",
        ["owner_id", "novel_id", "version_id"],
    )


def downgrade() -> None:
    """Downgrade schema：对称删除。"""
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("world_model_events"):
        return
    op.drop_index(
        "idx_world_model_conflicts_scope", table_name="world_model_conflicts"
    )
    op.drop_table("world_model_conflicts")
    op.drop_index(
        "idx_world_model_edges_scope", table_name="world_model_causal_edges"
    )
    op.drop_table("world_model_causal_edges")
    op.drop_index("idx_world_model_events_scope", table_name="world_model_events")
    op.drop_table("world_model_events")
