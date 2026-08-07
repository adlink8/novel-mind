"""
Immutable durable QueryPlanTrace authority (Phase 26-01 / REQ-QP-01).

表 ``query_plan_traces`` 是已验证 QueryPlan 的 append-only 持久化投影：
  - canonical payload（不含 raw question / trace 自身）、trace id、幂等 key、
    owner/novel/version/cutoff、schema/parser version、source/dataset lineage、
    availability/fallback、created-at 与 blocked reason。
  - 唯一幂等 key 冲突只重放既有行（repository 层保证），绝不产生第二条 trace。
  - 代码库中不存在任何 UPDATE 路径；repository 拒绝跨 owner 读取。
  - 无 active-pointer / promotion / current-revision 列（D-14）。
"""

from __future__ import annotations


from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, JSONB, TimestampMixin

QUERYPLAN_CUTOFF_MODES = ("reading_progress", "whole_book")


class QueryPlanTrace(TimestampMixin, Base):
    """Append-only durable projection of one validated QueryPlan.

    No ``update_*`` method exists anywhere for this table. Cross-owner reads are
    rejected at the repository layer, and no active-pointer / promotion write is
    defined (D-14).
    """

    __tablename__ = "query_plan_traces"
    __table_args__ = (
        UniqueConstraint("trace_id", name="uq_query_plan_traces_trace_id"),
        UniqueConstraint("idempotency_key", name="uq_query_plan_traces_idempotency"),
        Index("idx_query_plan_traces_scope", "owner_id", "novel_id", "version_id"),
        Index("idx_query_plan_traces_owner_created", "owner_id", "created_at"),
        CheckConstraint(
            "length(canonical_payload_hash) = 64",
            name="ck_query_plan_traces_payload_hash",
        ),
        CheckConstraint(
            "length(idempotency_key) = 64",
            name="ck_query_plan_traces_idempotency_key",
        ),
        CheckConstraint(
            "cutoff_mode IN ('reading_progress','whole_book')",
            name="ck_query_plan_traces_cutoff_mode",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[int] = mapped_column(Integer, nullable=False)
    cutoff_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    through_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    full_book_authorized: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_lineage: Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    canonical_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    availability_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    fallback: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    blocked_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # created_at / updated_at come from TimestampMixin; repository sets them
    # explicitly from the trace so SQLite tests never depend on a server default.
