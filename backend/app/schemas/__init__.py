"""Schema 统一导出"""

# 通用
from app.schemas.common import (
    ErrorResponse,
    PaginatedResponse,
    SuccessResponse,
)

# 小说 & 章节
from app.schemas.novel import (
    ChapterResponse,
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
