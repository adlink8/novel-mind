"""
同人文章节 ORM 模型

与 FanFictionChapter 的区别:
  - FanFiction: 同人文整体（一个续写项目）
  - FanFictionChapter: 同人文的每个章节（多次生成的结果）

rag_context 字段:
  JSON 格式，存储生成该章节时 RAG 检索到的参考段落。
  用于:
  - 展示"AI 参考了哪些原文段落"
  - 用户理解续写依据，必要时修正
"""

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, Float, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class FanFictionChapter(TimestampMixin, Base):
    """
    同人文章节表：存储同人文的每个章节内容。

    支持多次 AI 生成（每次生成一个章节），
    记录生成所用模型、风格评分、RAG 上下文。
    """
    __tablename__ = "fanfiction_chapters"

    # 主键
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 外键：关联到同人文
    fanfiction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("fan_fictions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )

    # 章节内容
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)              # 章节序号
    title: Mapped[str] = mapped_column(String(200), default="")                       # 章节标题
    content: Mapped[str | None] = mapped_column(Text)                                 # 章节正文

    # AI 生成元数据
    ai_generated: Mapped[bool] = mapped_column(Boolean, default=False)                # 是否为 AI 生成（vs 用户手写）
    model_used: Mapped[str | None] = mapped_column(String(100))                       # 使用的 AI 模型标识
    style_score: Mapped[float | None] = mapped_column(Float)                          # 风格一致性评分（0-100，对比原作）

    # RAG 上下文
    rag_context: Mapped[dict | None] = mapped_column(JSON, default=dict)              # 生成时的 RAG 参考段落

    # 统计
    word_count: Mapped[int] = mapped_column(Integer, default=0)                       # 章节字数
