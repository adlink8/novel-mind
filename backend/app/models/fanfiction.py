"""
同人文/续写 ORM 模型

状态机 (FanFiction.status):
  draft → generating → completed / failed
  - draft:      草稿，用户刚创建，未开始生成
  - generating: AI 正在生成中（流式输出）
  - completed:  生成完成
  - failed:     生成失败（API 超时、内容审核等）

续写流程:
  1. 用户创建 FanFiction（指定 prompt、parent_chapter_id）
  2. RAG 检索相关前文段落
  3. AI 流式生成内容
  4. 生成结果写入 FanFiction.content
  5. 可选: 分章节写入 FanFictionChapter 表
"""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class FanFiction(TimestampMixin, Base):
    """
    同人文主表：存储用户创建的续写/同人创作。

    parent_chapter_id 指定从原作哪个章节开始续写，
    结合 RAG 检索该章节前后的相关段落作为上下文。
    """

    __tablename__ = "fan_fictions"

    # 主键
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 外键：关联到原作小说
    novel_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("novels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 创作信息
    title: Mapped[str] = mapped_column(String(200), nullable=False)  # 同人文标题
    prompt: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # 用户输入的续写提示/创作要求
    content: Mapped[str | None] = mapped_column(Text)  # AI 生成的完整内容
    style_config: Mapped[str | None] = mapped_column(Text)  # 风格配置（JSON 字符串）

    # 状态与统计
    word_count: Mapped[int] = mapped_column(Integer, default=0)  # 已生成字数
    status: Mapped[str] = mapped_column(String(20), default="draft")  # 生成状态
    model_used: Mapped[str | None] = mapped_column(String(100))  # 使用的 AI 模型

    # 续写起点
    parent_chapter_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="SET NULL")
    )  # 从原作哪个章节开始续写
