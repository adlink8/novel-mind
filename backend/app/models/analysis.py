"""
AI 分析结果 ORM 模型

分析类型 (analysis_type):
  - plot_summary      : 剧情摘要（全书或分卷）
  - character_analysis: 人物分析（性格、成长弧线、动机）
  - theme             : 主题分析（核心主题、象征意义）
  - style             : 风格分析（写作风格、叙事手法）
  - chapter_summary   : 章节摘要（每章 200-500 字结构化摘要）

结果存储:
  result_data 使用 JSON 字段存储结构化分析结果。
  不同分析类型的 result_data 结构不同，由前端按类型渲染。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    JSON,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AnalysisResult(TimestampMixin, Base):
    """
    AI 分析结果表：存储 AI 对小说的各类分析结果。

    一个小说可以有多条分析记录（不同类型、不同模型、不同版本）。
    chapter_id 为空时表示全书分析，非空时表示章节级分析。
    """

    __tablename__ = "analysis_results"

    # 主键
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 外键关联
    novel_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("novels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chapter_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("chapters.id", ondelete="SET NULL"),
        index=True,  # NULL 表示全书分析
    )

    # 分析信息
    analysis_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # 分析类型: plot_summary / character_analysis / theme / style / chapter_summary
    result_data: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=dict
    )  # 结构化分析结果

    # 调用信息
    model_used: Mapped[str | None] = mapped_column(String(100))  # 使用的 AI 模型
    prompt_tokens: Mapped[int | None] = mapped_column(
        Integer
    )  # 输入 token 数（用于成本统计）
    completion_tokens: Mapped[int | None] = mapped_column(Integer)  # 输出 token 数


class AnalysisRun(TimestampMixin, Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "novel_id", "active_key", name="uq_analysis_runs_active"
        ),
        CheckConstraint(
            "status IN ('pending','running','paused_budget','paused_dependency','cancelled','completed','failed')",
            name="ck_analysis_runs_status",
        ),
        Index("idx_analysis_runs_scope", "owner_id", "novel_id"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[int | None] = mapped_column(
        ForeignKey("analysis_versions.id", ondelete="SET NULL"), nullable=True
    )
    active_key: Mapped[str | None] = mapped_column(
        String(16), nullable=True, default="active"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    status_reason: Mapped[str | None] = mapped_column(String(128))
    lease_id: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    checkpoint: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    progress: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class AnalysisVersion(TimestampMixin, Base):
    __tablename__ = "analysis_versions"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "novel_id", "version_key", name="uq_analysis_versions_scope_key"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    novel_id: Mapped[int] = mapped_column(
        ForeignKey("novels.id", ondelete="CASCADE"), nullable=False
    )
    version_key: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("analysis_versions.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="candidate")
    source_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hierarchy_build_id: Mapped[str] = mapped_column(String(64), nullable=False)
    hierarchy_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_lineage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    decoding_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    price_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    manifest_checksum: Mapped[str | None] = mapped_column(String(64))
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnalysisChapterStage(TimestampMixin, Base):
    __tablename__ = "analysis_chapter_stages"
    __table_args__ = (
        UniqueConstraint("run_id", "stage_key", name="uq_analysis_chapter_stage"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    chapter_id: Mapped[int | None] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE")
    )
    stage_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    artifact_checksum: Mapped[str | None] = mapped_column(String(64))
    checkpoint: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)


class ModelCallAttempt(TimestampMixin, Base):
    __tablename__ = "model_call_attempts"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "stage_key", "attempt_number", name="uq_model_call_attempt"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    reservation_id: Mapped[int | None] = mapped_column(
        ForeignKey("analysis_budget_reservations.id", ondelete="SET NULL")
    )
    stage_key: Mapped[str] = mapped_column(String(160), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    cache_key: Mapped[str | None] = mapped_column(String(128))
    cache_source_attempt_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_call_attempts.id", ondelete="SET NULL")
    )
    provider_request_id: Mapped[str | None] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_hash: Mapped[str | None] = mapped_column(String(64))
    usage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))


class AnalysisBudgetLedger(TimestampMixin, Base):
    __tablename__ = "analysis_budget_ledgers"
    __table_args__ = (UniqueConstraint("run_id", name="uq_analysis_budget_run"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), nullable=False
    )
    max_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    max_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    max_cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    reserved_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    reserved_output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    reserved_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=0
    )
    settled_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    settled_input_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    settled_output_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    settled_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=0
    )


class AnalysisBudgetReservation(TimestampMixin, Base):
    __tablename__ = "analysis_budget_reservations"
    __table_args__ = (
        UniqueConstraint(
            "ledger_id", "reservation_key", name="uq_analysis_budget_reservation"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ledger_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_budget_ledgers.id", ondelete="CASCADE"), nullable=False
    )
    reservation_key: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="reserved")
    calls: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    settled_usage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
