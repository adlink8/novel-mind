"""RAG 文本块模型（支持 pgvector 向量检索）"""

from sqlalchemy import ForeignKey, Integer, String, Text, JSON, Float
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class TextChunk(TimestampMixin, Base):
    __tablename__ = "text_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("chapters.id", ondelete="SET NULL"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)  # 章节内序号
    content: Mapped[str] = mapped_column(Text, nullable=False)  # 文本块内容（300-500字）
    chunk_type: Mapped[str] = mapped_column(
        String(50), default="paragraph"
    )  # scene / dialogue / description / narration / paragraph
    metadata_json: Mapped[dict | None] = mapped_column(JSON, default=dict)
    # 元数据: {"characters": [...], "location": "...", "time": "..."}

    word_count: Mapped[int] = mapped_column(Integer, default=0)
    embedding_status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending / embedded / failed
