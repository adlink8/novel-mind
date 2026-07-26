"""
raw TextChunk → Chroma 索引日志（Phase 24-01）

`chunk_index_journal` 记录每一次 index_novel 尝试的完整生命周期，
用于：

1. 重启后判断上次索引进行到哪个阶段（phase 状态机）；
2. 幂等键：同一 source_signature 已 completed 且零失败 → 跳过重复索引；
   进行中（finished_at IS NULL）→ 拒绝并发；
3. fail-closed 审计：embedding/向量写入失败不再静默，phase=failed + error 摘要；
4. manifest 绑定（Phase 24-02）：completed 记录携带 chunk 集 checksum
   （排序 `chunk_id:sha256(content)` 行的 sha256），reconcile 可从 DB 复算比对。

phase 状态机（kind='index'）:
  started → deleting_old → chunks_persisted → embedding → completed / failed

kind:
  - index            : index_novel 正常索引尝试
  - reconcile_repair : indexing_reconcile 修复运行写入的审计记录
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

INDEX_JOURNAL_PHASES = (
    "started",
    "deleting_old",
    "chunks_persisted",
    "embedding",
    "completed",
    "failed",
)

INDEX_JOURNAL_KINDS = ("index", "reconcile_repair")

# 进行中阶段：finished_at 为空且 phase 在此集合 → 拒绝并发触发
INDEX_JOURNAL_IN_PROGRESS_PHASES = (
    "started",
    "deleting_old",
    "chunks_persisted",
    "embedding",
)


class ChunkIndexJournal(TimestampMixin, Base):
    """单次 raw chunk 索引（或 reconcile 修复）尝试的持久日志。"""

    __tablename__ = "chunk_index_journal"
    __table_args__ = (
        CheckConstraint(
            "phase IN ('started','deleting_old','chunks_persisted',"
            "'embedding','completed','failed')",
            name="ck_chunk_index_journal_phase",
        ),
        CheckConstraint(
            "kind IN ('index','reconcile_repair')",
            name="ck_chunk_index_journal_kind",
        ),
        Index("idx_chunk_index_journal_novel_phase", "novel_id", "phase"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    novel_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("novels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 每次尝试的唯一标识（uuid4 hex）
    attempt_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )

    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="index")

    phase: Mapped[str] = mapped_column(String(32), nullable=False, default="started")

    # 计数（随批次提交推进）
    total_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedded_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Chroma collection（当前架构为固定名 novel_{novel_id}）
    collection_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # 幂等键：排序 (chapter_id, chapter_number, sha256(content)) 的 sha256
    source_signature: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # manifest 绑定：排序 `chunk_id:sha256(content)` 行集的 sha256（completed 时写入）
    manifest_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # 失败/部分失败摘要（截断存储）
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
