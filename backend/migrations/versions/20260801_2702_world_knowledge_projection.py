"""World-model character epistemic history immutable projection (27-02).

单张权威表 world_model_knowledge：append-only 人物 state/goal/motivation/
knowledge 声明（REQ-WM-02）。错误信念、隐藏知识与矛盾以显式 epistemic_status
标签保留，从不覆盖（D-04/D-05）；Reader Chat / 用户对话来源不可落为原作事实
（D-06，gate 层强制，此处仅保留 source_kind 枚举约束）。

设计约定（延续 20260801_2701_world_event_projection.py）:
  - 幂等 inspector 守卫 + 对称 downgrade，upgrade/downgrade 成对可逆。
  - 反规范化 owner_id / novel_id 外键（users.id / novels.id，ON DELETE
    CASCADE）；version_id 为普通 INTEGER（novel 版本号，沿用现有约定）。
  - JSONB 用 ``JSONB().with_variant(JSON(), "sqlite")``：PostgreSQL 渲染
    JSONB（与 ORM 模型一致，alembic check 无 drift），SQLite 测试渲染 JSON。
  - 每行携带 canonical payload + SHA-256 checksum + 幂等键 + 投影级
    projection_hash，restart replay 可做 byte-equivalent 证明。
  - 反规范化的过滤列（subject/aspect/known_at/disclosure_cutoff/pov/
    pov_kind/source_kind/authority/epistemic_status/gate_status）支撑
    cutoff/POV → disclosure/authority 查询路径，canonical_payload 仍是
    checksum 锚定的权威内容。

Revision ID: 20260801_2702
Revises: 20260801_2701
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260801_2702"
down_revision = "20260801_2701"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

AUTHORITY_VALUES = (
    "'canon_fact','probable_inference',"
    "'literary_interpretation','user_interpretation'"
)
GATE_STATUS_VALUES = "'pending','passed','rejected'"
ASPECT_VALUES = "'state','goal','motivation','knowledge'"
POV_KIND_VALUES = "'character','omniscient'"
SOURCE_KIND_VALUES = (
    "'canon_source','reader_chat',"
    "'user_conversation','human_override'"
)
EPISTEMIC_STATUS_VALUES = (
    "'asserted','mistaken_belief',"
    "'hidden_knowledge','retracted','contradiction','candidate'"
)


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
        sa.Column("canonical_payload", JSONB, nullable=False),
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
    if insp.has_table("world_model_knowledge"):
        return

    op.create_table(
        "world_model_knowledge",
        *[
            sa.Column("knowledge_key", sa.String(180), nullable=False),
            sa.Column("subject", sa.String(180), nullable=False),
            sa.Column("aspect", sa.String(24), nullable=False),
            sa.Column("known_at", sa.Integer, nullable=False),
            sa.Column("disclosure_cutoff", sa.Integer, nullable=False),
            sa.Column("pov", sa.String(180), nullable=False),
            sa.Column("pov_kind", sa.String(16), nullable=False),
            sa.Column("source_kind", sa.String(32), nullable=False),
            sa.Column("authority", sa.String(32), nullable=False),
            sa.Column("confidence", sa.Float, nullable=False),
            sa.Column("epistemic_status", sa.String(32), nullable=False),
            sa.Column("transition_from", sa.String(180), nullable=True),
            sa.Column("lineage", JSONB, nullable=False),
            sa.Column("source_refs", JSONB, nullable=False),
            sa.Column("gate_status", sa.String(16), nullable=False),
        ]
        + _common_columns(),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_world_model_knowledge_idempotency"
        ),
        sa.CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_world_model_knowledge_payload_hash",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_world_model_knowledge_idempotency_key",
        ),
        sa.CheckConstraint(
            f"aspect IN ({ASPECT_VALUES})",
            name="ck_world_model_knowledge_aspect",
        ),
        sa.CheckConstraint(
            f"pov_kind IN ({POV_KIND_VALUES})",
            name="ck_world_model_knowledge_pov_kind",
        ),
        sa.CheckConstraint(
            f"source_kind IN ({SOURCE_KIND_VALUES})",
            name="ck_world_model_knowledge_source_kind",
        ),
        sa.CheckConstraint(
            f"authority IN ({AUTHORITY_VALUES})",
            name="ck_world_model_knowledge_authority",
        ),
        sa.CheckConstraint(
            f"epistemic_status IN ({EPISTEMIC_STATUS_VALUES})",
            name="ck_world_model_knowledge_epistemic_status",
        ),
        sa.CheckConstraint(
            f"gate_status IN ({GATE_STATUS_VALUES})",
            name="ck_world_model_knowledge_gate_status",
        ),
    )
    op.create_index(
        "idx_world_model_knowledge_scope",
        "world_model_knowledge",
        ["owner_id", "novel_id", "version_id"],
    )
    op.create_index(
        "idx_world_model_knowledge_visibility",
        "world_model_knowledge",
        ["owner_id", "novel_id", "subject", "known_at"],
    )


def downgrade() -> None:
    """Downgrade schema：对称删除。"""
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("world_model_knowledge"):
        return
    op.drop_index(
        "idx_world_model_knowledge_visibility", table_name="world_model_knowledge"
    )
    op.drop_index("idx_world_model_knowledge_scope", table_name="world_model_knowledge")
    op.drop_table("world_model_knowledge")
