# backend/app/api — 路由层

FastAPI REST API 路由模块，定义对外 HTTP 端点，负责请求校验、认证依赖注入、响应序列化。

## 模块清单

| 文件 | 路由前缀 | 状态 | 职责 |
|---|---|---|---|
| `auth.py` | `/api/auth` | ✅ VERIFIED | 注册 / 登录 / 注销 / 获取当前用户，Bearer JWT + HttpOnly Cookie 双通道 |
| `novels.py` | `/api/novels` | ✅ VERIFIED | 小说上传、列表、详情（不含 source_path）、章节查询、删除（含文件补偿清理） |
| `models.py` | `/api/models` | ✅ VERIFIED | AI 模型配置 CRUD、测试连接、设为默认，owner 隔离 + 密钥加密存储 |
| `settings.py` | `/api/settings` | ✅ IMPLEMENTED | 设置中心：AI 路由偏好读写（app_settings 持久化 + 同步 ai_router） |
| `usage.py` | `/api/usage` | ✅ IMPLEMENTED | AI 用量汇总（今日/近7天/近30天费用 + 累计 token） |
| `rag.py` | `/api/rag` | ✅ IMPLEMENTED | RAG 检索：语义搜索、触发索引、查询索引进度 |
| `search.py` | `/api/search` | ✅ VERIFIED | 全局与小说内混合搜索，owner 隔离、evidence 回链 |
| `eval.py` | `/api/eval` | ⚠️ PARTIAL | 评测 dataset/run/report；认证与 owner 隔离已验证，质量闭环未通过 |
| `analysis.py` | `/api/analysis` | ✅ IMPLEMENTED | 版本化剧情分析任务（Phase 08）；仅 `analyze/stream` 仍 501（Phase 25 处置） |
| `timeline.py` | `/api/timeline` | ✅ VERIFIED | 时间线查询/服务端章范围/剧透边界（Phase 08） |
| `clues` | `/api/clues` | ✅ IMPLEMENTED | 线索与伏笔生命周期（Phase 11） |
| `relationships` | `/api/relationships` | ✅ VERIFIED | 人物关系图（Phase 09） |
| `reader chat` | `/api/novels/.../chat` | ✅ IMPLEMENTED | 阅读器选区多会话对话（Phase 10） |
| `narrative memory` | `/api/narrative-memory` | ✅ IMPLEMENTED | NM 只读结构 API（Phase 20；candidate_preview，无 promotion） |
| `characters.py` | `/api/characters` | ⚠️ 废弃双轨 | 占位返回空数组/501；Phase 25 决定 410 或适配 Phase 09 |
| `fanfiction.py` | `/api/fanfiction` | ⏳ deferred | 创建/续写 501；由 v1.4 创作域接管（Phase 31–33） |
| `dependencies.py` | — | ✅ VERIFIED | FastAPI 依赖注入：`get_current_user`、`get_db` |

完整注册清单以 `backend/app/main.py` 为准（另含 knowledge、eval、chunking、asset audit 等）。

## 约定

- 业务端点通过 `core.security.require_user` 强制认证；小说资源使用 owner 依赖或等价查询边界
- 响应模型使用 `schemas/` 中的 Pydantic 模型
- 占位/废弃端点的处置（410、实现或移除）见 `.planning/ROADMAP.md` Phase 25
- 在 `main.py` 中统一注册：`app.include_router(router, prefix="/api/xxx", tags=["..."])`
