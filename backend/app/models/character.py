"""
角色与角色关系 ORM 模型

角色类型 (Character.role):
  - protagonist : 主角
  - antagonist  : 反派/对手
  - supporting  : 配角
  - minor       : 龙套/路人

关系类型 (CharacterRelation.relation_type):
  - friend   : 朋友
  - enemy    : 敌人/对手
  - family   : 家人/亲属
  - lover    : 恋人/配偶
  - mentor   : 师徒（导师方向）
  - disciple : 师徒（弟子方向）
  - colleague: 同事/同门
  - subordinate: 上下级

关系强度 (strength): 1-10 整数，10 表示最强绑定
"""

from sqlalchemy import ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Character(TimestampMixin, Base):
    """
    人物表：存储小说中识别出的角色信息。

    数据来源:
    - Phase 3 AI 自动抽取（NER + LLM）
    - 用户手动编辑/修正

    extra_data 用于存储扩展信息（如经典台词、出场统计等）。
    """

    __tablename__ = "characters"

    # 主键
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 外键：关联到小说
    novel_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("novels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 角色信息
    name: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )  # 角色名称（主名称）
    aliases: Mapped[str | None] = mapped_column(
        Text
    )  # 别名列表（逗号分隔: "小明, 明哥, 阿明"）
    role: Mapped[str] = mapped_column(String(50), default="supporting")  # 角色类型
    description: Mapped[str | None] = mapped_column(Text)  # 外貌/身份描述
    personality: Mapped[str | None] = mapped_column(Text)  # 性格特征
    background: Mapped[str | None] = mapped_column(Text)  # 背景故事
    first_appearance_chapter: Mapped[int | None] = mapped_column(
        Integer
    )  # 首次出场章节号

    # 扩展数据（JSON 格式）
    # 示例: {"classic_quotes": ["xxx"], "dialogue_style": "简洁有力", "appearance_count": 42}
    extra_data: Mapped[dict | None] = mapped_column(JSON, default=dict)


class CharacterRelation(TimestampMixin, Base):
    """
    人物关系表：存储两个角色之间的关系。

    关系是有向的（source → target），但语义上通常双向理解。
    例如: source=贾宝玉, target=林黛玉, relation_type=lover
    """

    __tablename__ = "character_relations"

    # 主键
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # 外键：关联到小说
    novel_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("novels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # 关系两端
    source_character_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("characters.id", ondelete="CASCADE"),
        nullable=False,  # 关系起点角色
    )
    target_character_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("characters.id", ondelete="CASCADE"),
        nullable=False,  # 关系终点角色
    )

    # 关系属性
    relation_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # 关系类型: friend / enemy / family / lover / mentor / ...
    description: Mapped[str | None] = mapped_column(Text)  # 关系描述
    strength: Mapped[int | None] = mapped_column(Integer, default=5)  # 关系强度 1-10
    chapter_first_seen: Mapped[int | None] = mapped_column(
        Integer
    )  # 该关系首次出现的章节号
