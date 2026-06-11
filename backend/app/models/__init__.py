"""
ORM 模型统一导出包

本包包含 NovelMind 所有数据库模型（12 张表），分为 6 个业务域:

小说域:
  - Novel          : 小说主表（标题、作者、字数、状态、文风指纹）
  - Chapter        : 章节表（章节号、标题、正文、AI 摘要）

RAG 域:
  - TextChunk      : 文本块表（三级分块后的语义单元，用于向量检索）

分析域:
  - AnalysisResult : AI 分析结果表（摘要、人物分析、叙事结构等）
  - Character      : 人物表（名称、别名、角色类型、性格描述）
  - CharacterRelation: 人物关系表（关系类型、强度、首次出现章节）
  - TimelineEvent  : 时间线事件表（事件标题、类型、因果关联）

创作域:
  - FanFiction     : 同人文表（续写提示、风格配置、生成状态）
  - FanFictionChapter: 同人文章节表（AI 生成标记、风格评分、RAG 上下文）

基础设施域:
  - User           : 用户与认证主体
  - AIModelConfig  : AI 模型配置表（提供商、密钥、路由层级）
  - AIUsageLog     : AI 调用日志表（token 用量、费用、延迟）

使用方式:
  from app.models import Novel, Chapter, TextChunk  # 统一导入
  # 或
  from app.models.novel import Novel, Chapter       # 按模块导入
"""

from app.models.base import Base
from app.models.user import User
from app.models.novel import Novel, Chapter
from app.models.analysis import AnalysisResult
from app.models.timeline import TimelineEvent
from app.models.character import Character, CharacterRelation
from app.models.fanfiction import FanFiction
from app.models.fanfiction_chapter import FanFictionChapter
from app.models.ai_model import AIModelConfig
from app.models.ai_usage_log import AIUsageLog
from app.models.text_chunk import TextChunk

__all__ = [
    "Base",
    "User",
    "Novel",
    "Chapter",
    "AnalysisResult",
    "TimelineEvent",
    "Character",
    "CharacterRelation",
    "FanFiction",
    "FanFictionChapter",
    "AIModelConfig",
    "AIUsageLog",
    "TextChunk",
]
