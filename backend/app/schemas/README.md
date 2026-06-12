# backend/app/schemas — API 契约层

Pydantic v2 请求/响应模型，定义 API 的输入输出形状。FastAPI 路由使用这些模型进行自动校验、序列化与 OpenAPI 文档生成。

## 模块

| 文件 | 导出的 Schema 类 | 用途 |
|---|---|---|
| `common.py` | `ErrorResponse`、`PaginatedResponse`、`SuccessResponse` | 通用响应封装 — 错误、分页、成功 |
| `novel.py` | `NovelCreate`、`NovelUpdate`、`NovelResponse`、`NovelListResponse`、`ChapterResponse`、`NovelUploadResponse`、`ImportJobResponse`、`ImportStatusResponse` | 小说 & 章节 & 导入 CRUD 的请求/响应 |
| `analysis.py` | `AnalysisRequest`、`AnalysisResponse`、`ChapterAnalysisResponse` | 分析请求与结果响应 |
| `character.py` | `CharacterResponse`、`CharacterDetailResponse`、`CharacterRelationResponse` | 角色 & 关系 |
| `timeline.py` | `TimelineEventCreate`、`TimelineEventResponse`、`TimelineEventUpdate` | 时间线事件 |
| `fanfiction.py` | `FanFictionCreate`、`FanFictionResponse`、`FanFictionChapterResponse` | 同人文 |
| `ai_model.py` | `AIModelConfigCreate`、`AIModelConfigResponse`、`AIModelConfigUpdate`、`AIModelTestRequest`、`AIModelTestResponse` | AI 模型配置 |

## 约定

- 使用 Pydantic v2 风格（`model_config = ConfigDict(from_attributes=True)`）
- Response 模型配置 `from_attributes=True` 以支持 ORM 对象直接序列化
- 敏感字段（如 API key）在 Response 中排除，仅在 Create/Update 中接受
- 遵循 RESTful 命名：`XxxCreate` / `XxxUpdate` / `XxxResponse` / `XxxListResponse`
