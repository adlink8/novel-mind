"""角色与角色关系模型"""

from sqlalchemy import ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Character(TimestampMixin, Base):
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    aliases: Mapped[str | None] = mapped_column(Text)  # "小明, 明哥, 阿明"
    role: Mapped[str] = mapped_column(String(50), default="supporting")  # protagonist / antagonist / supporting / minor
    description: Mapped[str | None] = mapped_column(Text)
    personality: Mapped[str | None] = mapped_column(Text)
    background: Mapped[str | None] = mapped_column(Text)
    first_appearance_chapter: Mapped[int | None] = mapped_column(Integer)
    extra_data: Mapped[dict | None] = mapped_column(JSON, default=dict)  # 扩展字段


class CharacterRelation(TimestampMixin, Base):
    __tablename__ = "character_relations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    novel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_character_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    target_character_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(String(50), nullable=False)  # friend / enemy / family / lover / mentor etc.
    description: Mapped[str | None] = mapped_column(Text)
    strength: Mapped[int | None] = mapped_column(Integer, default=5)  # 1-10, 关系强度
    chapter_first_seen: Mapped[int | None] = mapped_column(Integer)
