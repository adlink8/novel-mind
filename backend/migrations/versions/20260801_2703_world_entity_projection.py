"""World-model entity/rule/faction/place/item immutable projection (27-03).

五张权威表 world_model_entities / world_model_rules / world_model_rule_exceptions /
world_model_entity_links / world_model_alias_reviews：
append-only 类型化实体/势力/地点/物品、世界规则、一等规则例外、归属/空间/物品
状态链接与 review-only 别名冲突候选（REQ-WM-03）。别名相似度只产生可审查的
review candidate，绝不静默合并实体；规则例外是一等记录，绝不被规范化丢弃；
无 active-pointer / promotion / current-revision 列（D-02）。

设计约定（延续 20260801_2702_world_knowledge_projection.py）:
  - 幂等 inspector 守卫 + 对称 downgrade，upgrade/downgrade 成对可逆。
  - 反规范化 owner_id / novel_id 外键（users.id / novels.id，ON DELETE
    CASCADE）；version_id 为普通 INTEGER（novel 版本号，沿用现有约定）。
  - JSONB 用 ``JSONB().with_variant(JSON(), "sqlite")``：PostgreSQL 渲染
    JSONB（与 ORM 模型一致，alembic check 无 drift），SQLite 测试渲染 JSON。
  - 每行携带 canonical payload + SHA-256 checksum + 幂等键 + 投影级
    projection_hash，restart replay 可做 byte-equivalent 证明。
  - 反规范化的过滤列（entity_type / link_kind / disclosure_cutoff / authority /
    gate_status / source_kind）支撑 cutoff → disclosure 查询路径。

Revision ID: 20260801_2703
Revises: 20260801_2702
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260801_2703"
down_revision = "20260801_2702"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

ENTITY_TYPE_VALUES = "'entity','faction','place','item'"
LINK_KIND_VALUES = (
    "'member_of','allegiance','controls',"
    "'owns','located_in','carried_by'"
)
SOURCE_KIND_VALUES = (
    "'canon_source','reader_chat',"
    "'user_conversation','human_override'"
)
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


def _scoped_indexes(table: str, prefix: str) -> None:
    op.create_index(
        f"idx_{prefix}_scope",
        table,
        ["owner_id", "novel_id", "version_id"],
    )


def upgrade() -> None:
    """Upgrade schema（幂等 inspector 守卫：表已存在则跳过）。"""
    insp = sa.inspect(op.get_bind())
    if insp.has_table("world_model_entities"):
        return

    op.create_table(
        "world_model_entities",
        *[
            sa.Column("entity_key", sa.String(180), nullable=False),
            sa.Column("entity_type", sa.String(24), nullable=False),
            sa.Column("disclosure_cutoff", sa.Integer, nullable=False),
            sa.Column("source_kind", sa.String(32), nullable=False),
            sa.Column("authority", sa.String(32), nullable=False),
            sa.Column("confidence", sa.Float, nullable=False),
            sa.Column("gate_status", sa.String(16), nullable=False),
            sa.Column("source_refs", JSONB, nullable=False),
            sa.Column("aliases", JSONB, nullable=False),
            sa.Column("lineage", JSONB, nullable=False),
        ]
        + _common_columns(),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_world_model_entities_idempotency"
        ),
        sa.CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_world_model_entities_payload_hash",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_world_model_entities_idempotency_key",
        ),
        sa.CheckConstraint(
            f"entity_type IN ({ENTITY_TYPE_VALUES})",
            name="ck_world_model_entities_entity_type",
        ),
        sa.CheckConstraint(
            f"source_kind IN ({SOURCE_KIND_VALUES})",
            name="ck_world_model_entities_source_kind",
        ),
        sa.CheckConstraint(
            f"authority IN ({AUTHORITY_VALUES})",
            name="ck_world_model_entities_authority",
        ),
        sa.CheckConstraint(
            f"gate_status IN ({GATE_STATUS_VALUES})",
            name="ck_world_model_entities_gate_status",
        ),
    )
    _scoped_indexes("world_model_entities", "world_model_entities")
    op.create_index(
        "idx_world_model_entities_type",
        "world_model_entities",
        ["owner_id", "novel_id", "entity_type"],
    )

    op.create_table(
        "world_model_rules",
        *[
            sa.Column("rule_key", sa.String(180), nullable=False),
            sa.Column("disclosure_cutoff", sa.Integer, nullable=False),
            sa.Column("source_kind", sa.String(32), nullable=False),
            sa.Column("authority", sa.String(32), nullable=False),
            sa.Column("confidence", sa.Float, nullable=False),
            sa.Column("gate_status", sa.String(16), nullable=False),
            sa.Column("source_refs", JSONB, nullable=False),
            sa.Column("lineage", JSONB, nullable=False),
        ]
        + _common_columns(),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_world_model_rules_idempotency"
        ),
        sa.CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_world_model_rules_payload_hash",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_world_model_rules_idempotency_key",
        ),
        sa.CheckConstraint(
            f"source_kind IN ({SOURCE_KIND_VALUES})",
            name="ck_world_model_rules_source_kind",
        ),
        sa.CheckConstraint(
            f"authority IN ({AUTHORITY_VALUES})",
            name="ck_world_model_rules_authority",
        ),
        sa.CheckConstraint(
            f"gate_status IN ({GATE_STATUS_VALUES})",
            name="ck_world_model_rules_gate_status",
        ),
    )
    _scoped_indexes("world_model_rules", "world_model_rules")

    op.create_table(
        "world_model_rule_exceptions",
        *[
            sa.Column("exception_key", sa.String(180), nullable=False),
            sa.Column("rule_key", sa.String(180), nullable=False),
            sa.Column("applies_to", sa.String(180), nullable=True),
            sa.Column("disclosure_cutoff", sa.Integer, nullable=False),
            sa.Column("source_kind", sa.String(32), nullable=False),
            sa.Column("authority", sa.String(32), nullable=False),
            sa.Column("confidence", sa.Float, nullable=False),
            sa.Column("gate_status", sa.String(16), nullable=False),
            sa.Column("source_refs", JSONB, nullable=False),
        ]
        + _common_columns(),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_world_model_exceptions_idempotency"
        ),
        sa.CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_world_model_exceptions_payload_hash",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_world_model_exceptions_idempotency_key",
        ),
        sa.CheckConstraint(
            f"source_kind IN ({SOURCE_KIND_VALUES})",
            name="ck_world_model_exceptions_source_kind",
        ),
        sa.CheckConstraint(
            f"authority IN ({AUTHORITY_VALUES})",
            name="ck_world_model_exceptions_authority",
        ),
        sa.CheckConstraint(
            f"gate_status IN ({GATE_STATUS_VALUES})",
            name="ck_world_model_exceptions_gate_status",
        ),
    )
    _scoped_indexes("world_model_rule_exceptions", "world_model_exceptions")
    op.create_index(
        "idx_world_model_exceptions_rule",
        "world_model_rule_exceptions",
        ["owner_id", "novel_id", "rule_key"],
    )

    op.create_table(
        "world_model_entity_links",
        *[
            sa.Column("link_key", sa.String(180), nullable=False),
            sa.Column("link_kind", sa.String(24), nullable=False),
            sa.Column("source_key", sa.String(180), nullable=False),
            sa.Column("target_key", sa.String(180), nullable=False),
            sa.Column("disclosure_cutoff", sa.Integer, nullable=False),
            sa.Column("source_kind", sa.String(32), nullable=False),
            sa.Column("authority", sa.String(32), nullable=False),
            sa.Column("confidence", sa.Float, nullable=False),
            sa.Column("gate_status", sa.String(16), nullable=False),
            sa.Column("source_refs", JSONB, nullable=False),
        ]
        + _common_columns(),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_world_model_links_idempotency"
        ),
        sa.CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_world_model_links_payload_hash",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_world_model_links_idempotency_key",
        ),
        sa.CheckConstraint(
            f"link_kind IN ({LINK_KIND_VALUES})",
            name="ck_world_model_links_link_kind",
        ),
        sa.CheckConstraint(
            f"source_kind IN ({SOURCE_KIND_VALUES})",
            name="ck_world_model_links_source_kind",
        ),
        sa.CheckConstraint(
            f"authority IN ({AUTHORITY_VALUES})",
            name="ck_world_model_links_authority",
        ),
        sa.CheckConstraint(
            f"gate_status IN ({GATE_STATUS_VALUES})",
            name="ck_world_model_links_gate_status",
        ),
    )
    _scoped_indexes("world_model_entity_links", "world_model_links")
    op.create_index(
        "idx_world_model_links_kind",
        "world_model_entity_links",
        ["owner_id", "novel_id", "link_kind"],
    )

    op.create_table(
        "world_model_alias_reviews",
        *[
            sa.Column("review_key", sa.String(180), nullable=False),
            sa.Column("entity_key_a", sa.String(180), nullable=False),
            sa.Column("entity_key_b", sa.String(180), nullable=False),
            sa.Column("matched_alias", sa.String(180), nullable=False),
            sa.Column("similarity", sa.Float, nullable=False),
            sa.Column("review_status", sa.String(16), nullable=False),
            sa.Column("disclosure_cutoff", sa.Integer, nullable=False),
            sa.Column("source_kind", sa.String(32), nullable=False),
            sa.Column("source_refs", JSONB, nullable=False),
        ]
        + _common_columns(),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_world_model_alias_reviews_idempotency"
        ),
        sa.CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_world_model_alias_reviews_payload_hash",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_world_model_alias_reviews_idempotency_key",
        ),
        sa.CheckConstraint(
            f"source_kind IN ({SOURCE_KIND_VALUES})",
            name="ck_world_model_alias_reviews_source_kind",
        ),
        sa.CheckConstraint(
            "review_status IN ('review','resolved','rejected')",
            name="ck_world_model_alias_reviews_status",
        ),
    )
    _scoped_indexes("world_model_alias_reviews", "world_model_alias_reviews")


def downgrade() -> None:
    """Downgrade schema：对称删除。"""
    insp = sa.inspect(op.get_bind())
    if not insp.has_table("world_model_entities"):
        return
    op.drop_index(
        "idx_world_model_alias_reviews_scope",
        table_name="world_model_alias_reviews",
    )
    op.drop_table("world_model_alias_reviews")
    op.drop_index(
        "idx_world_model_links_kind", table_name="world_model_entity_links"
    )
    op.drop_index(
        "idx_world_model_links_scope", table_name="world_model_entity_links"
    )
    op.drop_table("world_model_entity_links")
    op.drop_index(
        "idx_world_model_exceptions_rule",
        table_name="world_model_rule_exceptions",
    )
    op.drop_index(
        "idx_world_model_exceptions_scope",
        table_name="world_model_rule_exceptions",
    )
    op.drop_table("world_model_rule_exceptions")
    op.drop_index("idx_world_model_rules_scope", table_name="world_model_rules")
    op.drop_table("world_model_rules")
    op.drop_index(
        "idx_world_model_entities_type", table_name="world_model_entities"
    )
    op.drop_index(
        "idx_world_model_entities_scope", table_name="world_model_entities"
    )
    op.drop_table("world_model_entities")
