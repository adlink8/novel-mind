"""
RAG 文本块 ORM 模型

本模型支持三级文本分块架构（Phase 2 RAG 索引核心）:
  章节级 → 场景级 → 殏落级（每块 300-500 字）

分块类型 (chunk_type):
  - scene     : 场景块（一个完整的场景/事件）
  - dialogue  : 对话块（连续的对话段落）
  - description: 描写块（环境/外貌/心理描写）
  - narration : 叙述块（旁白/背景介绍）
  - paragraph : 通用段落块（默认类型）

embedding_status 状态机:
  pending → embedded / failed
  - pending:  等待生成向量
  - embedded: 向量已写入 Chroma/pgvector
  - failed:   向量生成失败（可重试）
"""

from sqlalchemy import ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class TextChunk(TimestampMixin, Base):
    """
    文本块表：存储小说分块后的语义单元。

    每个 TextChunk 代表小说中的一个语义段落，用于:
    1. 生成 embedding 向量 → 写入 Chroma/pgvector
    2. RAG 检索时作为上下文注入 Prompt
    3. 搜索结果展示时跳转到原文位置
    """

    __tablename__ = "text_chunks"

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
        index=True,  # 章节删除时设为 NULL，不级联删除文本块
    )

    # 分块内容
    chunk_index: Mapped[int] = mapped_column(
        Integer, nullable=False
    )  # 章节内的块序号（0, 1, 2...）
    content: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # 文本块内容（300-500 字）
    chunk_type: Mapped[str] = mapped_column(
        String(50), default="paragraph"
    )  # 块类型: scene / dialogue / description / narration / paragraph

    # 元数据（JSON 格式）
    # 存储分块时提取的结构化信息，用于过滤和展示
    # 示例: {"characters": ["林黛玉", "贾宝玉"], "location": "潇湘馆", "time": "午后"}
    metadata_json: Mapped[dict | None] = mapped_column(JSON, default=dict)

    # 向量化状态
    word_count: Mapped[int] = mapped_column(Integer, default=0)  # 块内字数
    embedding_status: Mapped[str] = mapped_column(
        String(20), default="pending"
    )  # 向量生成状态: pending / embedded / failed

    # 全文搜索向量（PostgreSQL tsvector，用于 BM25 混合搜索）
    # SQLite 不支持 tsvector，设为 nullable
    search_vector: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None
    )
