"""
Pydantic Schema 统一导出包

本包定义所有 API 的请求/响应数据模型（API 契约），按业务域组织:
  - common.py     : 通用响应模型（错误、成功、分页）
  - novel.py      : 小说 & 章节的 CRUD 模型
  - analysis.py   : 剧情分析请求/响应
  - character.py  : 角色 & 关系响应
  - timeline.py   : 时间线事件 CRUD
  - fanfiction.py : 同人文 & 章节
  - ai_model.py   : AI 模型配置 & 测试

使用方式:
  from app.schemas import NovelResponse, AIModelConfigCreate

设计原则:
  - 所有 Schema 继承 BaseModel（Pydantic v2）
  - Response 模型使用 ConfigDict(from_attributes=True) 支持 ORM 直接转换
  - Create/Update 模型使用 Field() 定义校验规则（min_length、max_length 等）
  - 敏感字段（如 api_key）不出现在 Response 模型中
"""

# 通用
from app.schemas.common import (
    ErrorResponse,
    PaginatedResponse,
    SuccessResponse,
)

# 小说 & 章节
from app.schemas.novel import (
    ChapterResponse,
    ImportJobResponse,
    ImportStatusResponse,
    NovelCreate,
    NovelListResponse,
    NovelResponse,
    NovelUpdate,
    NovelUploadResponse,
)

# 分析
from app.schemas.analysis import (
    AnalysisRequest,
    AnalysisResponse,
    ChapterAnalysisResponse,
)

# 角色 & 关系
from app.schemas.character import (
    CharacterDetailResponse,
    CharacterRelationResponse,
    CharacterResponse,
)

# 时间线
from app.schemas.timeline import (
    TimelineEventCreate,
    TimelineEventResponse,
    TimelineEventUpdate,
)

# 同人文
from app.schemas.fanfiction import (
    FanFictionChapterResponse,
    FanFictionCreate,
    FanFictionResponse,
)

# AI 模型配置
from app.schemas.ai_model import (
    AIModelConfigCreate,
    AIModelConfigResponse,
    AIModelConfigUpdate,
    AIModelTestRequest,
    AIModelTestResponse,
)

__all__ = [
    # 通用
    "ErrorResponse",
    "PaginatedResponse",
    "SuccessResponse",
    # 小说
    "ChapterResponse",
    "ImportJobResponse",
    "ImportStatusResponse",
    "NovelCreate",
    "NovelListResponse",
    "NovelResponse",
    "NovelUpdate",
    "NovelUploadResponse",
    # 分析
    "AnalysisRequest",
    "AnalysisResponse",
    "ChapterAnalysisResponse",
    # 角色
    "CharacterDetailResponse",
    "CharacterRelationResponse",
    "CharacterResponse",
    # 时间线
    "TimelineEventCreate",
    "TimelineEventResponse",
    "TimelineEventUpdate",
    # 同人文
    "FanFictionChapterResponse",
    "FanFictionCreate",
    "FanFictionResponse",
    # AI 模型
    "AIModelConfigCreate",
    "AIModelConfigResponse",
    "AIModelConfigUpdate",
    "AIModelTestRequest",
    "AIModelTestResponse",
]
