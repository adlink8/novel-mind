"""
小说与章节 ORM 模型

业务说明:
  Novel 是系统的核心实体，代表用户导入的一本小说。
  Chapter 是 Novel 的子实体，通过 novel_id 外键关联。

状态机 (Novel.status):
  importing → ready → analyzing → analyzed
  - importing: 正在上传/解析
  - ready: 解析完成，可供阅读和分析
  - analyzing: AI 正在分析中
  - analyzed: 分析完成

文风指纹 (style_fingerprint):
  JSON 字段，存储 AI 分析出的小说写作风格特征（句长分布、词汇丰富度等），
  用于后续同人文续写时保持风格一致性。
"""

from sqlalchemy import ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Novel(TimestampMixin, Base):
    """
    小说主表。

    存储小说的元信息（标题、作者、类型）和处理状态。
    与 Chapter 是一对多关系（cascade delete-orphan）。
    """

    __tablename__ = "novels"

    # 主键
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 外键：所属用户
    owner_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # 基础信息
    title: Mapped[str] = mapped_column(
        String(200), nullable=False, index=True
    )  # 小说标题（必填，建立索引用于搜索）
    author: Mapped[str | None] = mapped_column(
        String(100)
    )  # 作者（可选，从文件名或内容推断）
    description: Mapped[str | None] = mapped_column(Text)  # 简介/摘要
    cover_url: Mapped[str | None] = mapped_column(String(500))  # 封面图片 URL
    genre: Mapped[str | None] = mapped_column(
        String(50)
    )  # 类型/流派（玄幻、言情、科幻等）

    # 统计信息
    chapter_count: Mapped[int] = mapped_column(Integer, default=0)  # 章节总数
    word_count: Mapped[int] = mapped_column(Integer, default=0)  # 总字数

    # 处理状态
    status: Mapped[str] = mapped_column(
        String(50), default="importing"
    )  # 状态: importing / chunking / embedding / analyzing / analyzed / ready

    # 文件路径
    source_path: Mapped[str | None] = mapped_column(
        String(500)
    )  # 原始 TXT 文件存储路径

    # 阅读进度：记录当前阅读到的章节ID和进度百分比（0-100）
    # 格式: {"chapter_id": 12, "progress_percent": 45.2}
    reading_progress: Mapped[dict | None] = mapped_column(JSON, default=dict)

    # 文风指纹（JSON 格式，存储风格分析结果）
    # 内容示例: {"avg_sentence_len": 25.3, "vocabulary_richness": 0.72, ...}
    style_fingerprint: Mapped[dict | None] = mapped_column(JSON, default=dict)

    # 关系定义
    # - back_populates: 双向关联，Chapter.novel 指回 Novel
    # - cascade: 删除 Novel 时级联删除所有 Chapter
    # - lazy="selectin": 查询 Novel 时自动预加载 chapters（避免 N+1 查询）
    chapters: Mapped[list["Chapter"]] = relationship(
        back_populates="novel", cascade="all, delete-orphan", lazy="selectin"
    )


class Chapter(TimestampMixin, Base):
    """
    章节表。

    存储小说的每个章节内容，通过 novel_id 外键关联到 Novel。
    summary 字段用于存储 AI 生成的章节摘要。
    """

    __tablename__ = "chapters"

    # 主键
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 外键：关联到 novels 表
    novel_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("novels.id", ondelete="CASCADE"),  # 级联删除
        nullable=False,
        index=True,  # 建立索引加速按小说查询章节
    )

    # 章节内容
    chapter_number: Mapped[int] = mapped_column(
        Integer, nullable=False
    )  # 章节序号（1, 2, 3...）
    title: Mapped[str] = mapped_column(
        String(200), default=""
    )  # 章节标题（如 "第一章 初入江湖"）
    content: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # 章节正文内容（完整文本）
    word_count: Mapped[int] = mapped_column(Integer, default=0)  # 本章字数

    # AI 生成内容
    summary: Mapped[str | None] = mapped_column(Text)  # AI 生成的章节摘要（Phase 3）

    # 关系定义
    novel: Mapped["Novel"] = relationship(back_populates="chapters")
