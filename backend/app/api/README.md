# backend/app/api — 路由层

FastAPI REST API 路由模块，定义对外 HTTP 端点，负责请求校验、认证依赖注入、响应序列化。

## 模块清单

| 文件 | 路由前缀 | 状态 | 职责 |
|---|---|---|---|
| `auth.py` | `/api/auth` | ✅ VERIFIED | 注册 / 登录 / 注销 / 获取当前用户，Bearer JWT + HttpOnly Cookie 双通道 |
| `novels.py` | `/api/novels` | ✅ VERIFIED | 小说上传、列表、详情（不含 source_path）、章节查询、删除（含文件补偿清理） |
| `models.py` | `/api/models` | ✅ VERIFIED | AI 模型配置 CRUD、测试连接、设为默认，owner 隔离 + 密钥加密存储 |
| `rag.py` | `/api/rag` | ✅ IMPLEMENTED | RAG 检索：语义搜索、触发索引、查询索引进度 |
| `analysis.py` | `/api/analysis` | ⏳ 501 占位 | 剧情分析（待实现核心逻辑） |
| `timeline.py` | `/api/timeline` | ⏳ 501 占位 | 时间线管理（待实现核心逻辑） |
| `characters.py` | `/api/characters` | ⏳ 501 占位 | 人物关系（待实现核心逻辑） |
| `fanfiction.py` | `/api/fanfiction` | ⏳ 501 占位 | 同人文生成（待实现核心逻辑） |
| `dependencies.py` | — | ✅ VERIFIED | FastAPI 依赖注入：`get_current_user`、`get_db` |

## 约定

- 所有端点通过 `dependencies.get_current_user` 进行认证
- 响应模型使用 `schemas/` 中的 Pydantic 模型
- 占位端点返回 `501 Not Implemented`，待 v0.3 激活
- 在 `main.py` 中统一注册：`app.include_router(router, prefix="/api/xxx", tags=["..."])`
