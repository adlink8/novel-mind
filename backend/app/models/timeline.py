"""
时间线事件 ORM 模型

事件类型 (event_type):
  - plot     : 剧情推进事件（核心情节转折）
  - character: 角色相关事件（角色登场、死亡、转变）
  - world    : 世界观事件（设定揭示、历史背景）
  - conflict : 冲突事件（战斗、争执、对决）

排序机制:
  sort_order 使用浮点数，支持在两个事件之间插入新事件（如 1.0, 1.5, 2.0）。
  AI 提取时按文本位置自动排序，用户可手动调整。
"""

from datetime import datetime
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, Text, Float, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class TimelineEvent(TimestampMixin, Base):
    """
    时间线事件表：存储小说中的关键事件。

    数据来源:
    - Phase 4 AI 自动提取（LLM 从全文抽取事件 + 因果链）
    - 用户手动添加/编辑

    与 Chapter 的关系: 一个章节可能包含多个事件，一个事件通常属于一个章节。
    """

    __tablename__ = "timeline_events"

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
        Integer, ForeignKey("chapters.id", ondelete="SET NULL")
    )  # 关联章节（可选，章节删除时设为 NULL）

    # 事件信息
    event_title: Mapped[str] = mapped_column(String(200), nullable=False)  # 事件标题
    event_description: Mapped[str | None] = mapped_column(Text)  # 事件详细描述
    event_type: Mapped[str] = mapped_column(String(50), default="plot")  # 事件类型
    sort_order: Mapped[float] = mapped_column(
        Float, default=0.0
    )  # 排序权重（越小越靠前）

    # 关联信息
    characters_involved: Mapped[str | None] = mapped_column(
        Text
    )  # 涉及角色（JSON 数组字符串）
    location: Mapped[str | None] = mapped_column(String(200))  # 事件发生地点
    time_reference: Mapped[str | None] = mapped_column(
        String(200)
    )  # 时间参考（"第3天", "一年后"）


class MachineTimelineEvent(TimestampMixin, Base):
    __tablename__ = "machine_timeline_events"
    __table_args__ = (
        UniqueConstraint("version_id", "logical_event_id", name="uq_machine_timeline_event"),
        CheckConstraint("time_precision IN ('exact','relative','fuzzy','unknown')", name="ck_machine_event_time_precision"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("analysis_versions.id", ondelete="CASCADE"), nullable=False)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    logical_event_id: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    time_precision: Mapped[str] = mapped_column(String(16), nullable=False)
    time_expression: Mapped[str | None] = mapped_column(String(120))
    exact_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    relative_anchor_event_id: Mapped[str | None] = mapped_column(String(80))
    relative_relation: Mapped[str | None] = mapped_column(String(24))
    fuzzy_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fuzzy_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    narrative_chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    narrative_index: Mapped[int] = mapped_column(Integer, nullable=False)
    story_rank: Mapped[int | None] = mapped_column(Integer)
    story_constraints: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_lineage: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    publication_status: Mapped[str] = mapped_column(String(24), nullable=False, default="provisional")


class TimelineParticipant(Base):
    __tablename__ = "timeline_participants"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("machine_timeline_events.id", ondelete="CASCADE"), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(ForeignKey("characters.id", ondelete="SET NULL"))
    mention: Mapped[str] = mapped_column(String(100), nullable=False)


class TimelineEvidenceRef(Base):
    __tablename__ = "timeline_evidence_refs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("machine_timeline_events.id", ondelete="CASCADE"), nullable=False)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(80), nullable=False)
    source_start: Mapped[int] = mapped_column(Integer, nullable=False)
    source_end: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class TimelineCausalEdge(Base):
    __tablename__ = "timeline_causal_edges"
    __table_args__ = (UniqueConstraint("version_id", "source_event_id", "target_event_id", "edge_type", name="uq_timeline_causal_edge"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("analysis_versions.id", ondelete="CASCADE"), nullable=False)
    source_event_id: Mapped[int] = mapped_column(ForeignKey("machine_timeline_events.id", ondelete="CASCADE"), nullable=False)
    target_event_id: Mapped[int] = mapped_column(ForeignKey("machine_timeline_events.id", ondelete="CASCADE"), nullable=False)
    edge_type: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class TimelineOverride(TimestampMixin, Base):
    __tablename__ = "timeline_overrides"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    logical_event_id: Mapped[str] = mapped_column(String(80), nullable=False)
    field_name: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[dict] = mapped_column(JSON, nullable=False)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("timeline_overrides.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")
    needs_relink: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class TimelineActivePointer(TimestampMixin, Base):
    __tablename__ = "timeline_active_pointers"
    __table_args__ = (UniqueConstraint("owner_id", "novel_id", name="uq_timeline_active_pointer"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    version_id: Mapped[int] = mapped_column(ForeignKey("analysis_versions.id", ondelete="RESTRICT"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    manifest_checksum: Mapped[str] = mapped_column(String(64), nullable=False)


class TimelinePointerJournal(TimestampMixin, Base):
    __tablename__ = "timeline_pointer_journal"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    from_version_id: Mapped[int | None] = mapped_column(ForeignKey("analysis_versions.id", ondelete="RESTRICT"))
    to_version_id: Mapped[int] = mapped_column(ForeignKey("analysis_versions.id", ondelete="RESTRICT"), nullable=False)
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    expected_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    resulting_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest: Mapped[dict] = mapped_column(JSON, nullable=False)
