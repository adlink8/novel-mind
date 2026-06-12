"""
导入任务 ORM 模型

业务说明:
  ImportJob 是小说导入流程的持久化状态跟踪实体。
  替代原先的进程内存字典 _import_status，支持重启后状态恢复。

状态机 (ImportJob.status):
  pending → uploading → detecting → parsing → chunking → embedding → ready / failed
  - pending: 等待处理
  - uploading: 正在上传文件
  - detecting: 正在检测编码
  - parsing: 正在解析章节
  - chunking: 正在分块
  - embedding: 正在向量化
  - ready: 导入完成
  - failed: 导入失败
  - cancelled: 用户取消（终态）

重试机制:
  - 失败任务可重试，retry_count 记录重试次数
  - max_retries 限制最大重试次数（默认 3 次）
  - 超过重试次数后不可再重试

并发控制:
  - lease_id / lease_expires_at: 分布式租约，防止多个 worker 同时处理同一个 job
  - 租约超时 300 秒，过期后可由恢复机制重置

幂等控制:
  - content_hash: 文件内容 SHA-256 哈希，防止重复导入相同文件
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.novel import Novel


# 状态机允许的状态转换
VALID_STATUS_TRANSITIONS = {
    "pending": ["uploading", "cancelled"],
    "uploading": ["detecting", "failed", "cancelled"],
    "detecting": ["parsing", "failed", "cancelled"],
    "parsing": ["chunking", "saving", "failed", "cancelled"],
    "chunking": ["embedding", "saving", "failed", "cancelled"],
    "embedding": ["ready", "failed", "cancelled"],
    "saving": ["ready", "failed", "cancelled"],
    "ready": [],  # 终态
    "failed": ["pending"],  # 只能重试回 pending
    "cancelled": [],  # 终态
}


class ImportJob(TimestampMixin, Base):
    """
    导入任务表。

    存储小说导入任务的状态和进度信息。
    通过 novel_id 外键关联到 Novel。
    """

    __tablename__ = "import_jobs"

    # 主键
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 外键：关联到 novels 表（可为空，因为导入任务可能在小说记录创建前生成）
    novel_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("novels.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # 状态信息
    status: Mapped[str] = mapped_column(
        String(50), default="pending"
    )  # 状态: pending / uploading / detecting / parsing / chunking / embedding / ready / failed
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 进度百分比 0-100
    message: Mapped[str] = mapped_column(
        String(500), default=""
    )  # 状态描述信息

    # 错误信息
    error_detail: Mapped[str | None] = mapped_column(
        Text
    )  # 失败时的详细错误信息

    # 重试机制
    retry_count: Mapped[int] = mapped_column(Integer, default=0)  # 已重试次数
    max_retries: Mapped[int] = mapped_column(Integer, default=3)  # 最大重试次数

    # 租约并发控制
    lease_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # 租约 ID（UUID hex）
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # 租约过期时间

    # 幂等键
    content_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )  # 文件内容 SHA-256 哈希

    # 关系定义
    # - back_populates: 双向关联，Novel.import_jobs 指回 ImportJob
    # - cascade: 删除 Novel 时级联删除所有 ImportJob
    novel: Mapped["Novel"] = relationship(back_populates="import_jobs")

    def can_transition_to(self, new_status: str) -> bool:
        """
        检查是否允许从当前状态转换到新状态。

        Args:
            new_status: 目标状态

        Returns:
            True 如果转换允许，False 否则
        """
        allowed = VALID_STATUS_TRANSITIONS.get(self.status, [])
        return new_status in allowed

    def can_retry(self) -> bool:
        """
        检查是否可以重试。

        Returns:
            True 如果任务失败且未超过最大重试次数，False 否则
        """
        return self.status == "failed" and self.retry_count < self.max_retries

    def is_terminal(self) -> bool:
        """
        检查是否为终态。

        终态包括 ready、failed、cancelled，这些状态不可再转换。

        Returns:
            True 如果为终态，False 否则
        """
        return self.status in ("ready", "failed", "cancelled")
