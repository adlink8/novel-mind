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

from sqlalchemy import ForeignKey, Integer, String, Text, JSON
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
        Integer, ForeignKey("novels.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    chapter_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="SET NULL"),
        index=True,  # NULL 表示全书分析
    )

    # 分析信息
    analysis_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # 分析类型: plot_summary / character_analysis / theme / style / chapter_summary
    result_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)     # 结构化分析结果

    # 调用信息
    model_used: Mapped[str | None] = mapped_column(String(100))                       # 使用的 AI 模型
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)                        # 输入 token 数（用于成本统计）
    completion_tokens: Mapped[int | None] = mapped_column(Integer)                    # 输出 token 数
