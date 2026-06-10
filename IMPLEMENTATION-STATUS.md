# NovelMind Implementation Status

审计日期：2026-06-06  
审计原则：以实际代码为准，不采信文档中的完成勾选作为完成证据。

## Executive Summary

当前仓库不是一个已完成 Phase 1 的稳定基线，而是一个“基础骨架 + 部分可用小说导入链路 + 多个占位功能”的早期实现。

GSD 01-02 已修复启动级前后端契约问题：小说列表分页响应、AI 模型新增字段、模型测试结果处理，以及占位生成/抽取端点的 501 语义。后续仍不应直接进入 Phase 2 RAG，直到 smoke verification 和文档状态校准完成。

## Status Legend

- VERIFIED：代码中有实际实现，且结构上可验证。
- PARTIAL：有部分代码或 UI，但缺少关键行为、端到端契约或验证。
- MISSING：文档规划中存在，但代码中没有实际实现。

## VERIFIED

| Area | Status | Evidence |
|---|---|---|
| FastAPI 应用入口与路由注册 | VERIFIED | `backend/app/main.py` 注册 novels / analysis / timeline / characters / fanfiction / models 路由，并提供 `/api/health` |
| CORS、请求日志、尾部斜杠处理中间件 | VERIFIED | `backend/app/main.py`、`backend/app/core/logging.py` |
| SQLAlchemy async 数据层 | VERIFIED | `backend/app/core/database.py`、`backend/app/models/*.py` |
| Alembic 迁移目录 | VERIFIED | `backend/migrations/versions/*.py` |
| 小说上传 TXT 限制、编码检测、保存、基础清洗 | VERIFIED | `backend/app/api/novels.py`、`backend/app/services/novel_service.py` |
| 基础章节分割 | VERIFIED | `CHAPTER_PATTERNS` / `CHAPTER_REGEX` 支持中文章节、英文 Chapter、数字标题 |
| 小说 CRUD 和章节查询后端 | VERIFIED | `backend/app/api/novels.py` 接入数据库 |
| AI 模型配置后端 CRUD | VERIFIED | `backend/app/api/models.py` 接入 `AIModelConfig` |
| LiteLLM 调用封装存在 | VERIFIED | `backend/app/services/ai_service.py` 提供 chat / embedding / stream_chat / test_connection |
| AI 路由策略对象存在 | VERIFIED | `backend/app/services/ai_router.py` 有 quality / balanced / budget 预设 |
| Docker Compose 数据服务 | VERIFIED | `docker-compose.yml` 定义 PostgreSQL + pgvector、Chroma、Neo4j profile |
| Next.js App Router 页面骨架 | VERIFIED | `frontend/src/app/page.tsx`、`novels/page.tsx`、`writing/page.tsx`、`settings/page.tsx` |
| 前端上传对话框与书架页 UI | VERIFIED | `frontend/src/components/novel-upload-dialog.tsx`、`frontend/src/app/novels/page.tsx` |
| 前端小说列表契约 | VERIFIED | GSD 01-02 后 `novelStore.ts` 读取后端 `items` 分页响应；`npx tsc --noEmit` 通过 |
| 前端 AI 模型配置契约 | VERIFIED | GSD 01-02 后 settings 表单提交 `name` / `model_id`；`npx tsc --noEmit` 通过 |
| 未实现生成/抽取端点语义 | VERIFIED | GSD 01-02 后 analysis / timeline / characters / fanfiction 写入或生成类占位端点返回 HTTP 501 |

## PARTIAL

| Area | Status | Evidence / Gap |
|---|---|---|
| Phase 1 “联调通过” | PARTIAL | 文档宣称 13 个端点通过，但仓库没有测试报告或测试代码支撑；需重新跑 smoke tests |
| AI 智能路由 | PARTIAL | 有内存路由策略，但未与用户配置、实际 AI 调用、任务执行链路打通 |
| AI 调用成本统计 | PARTIAL | 有 `AIUsageLog` 模型和 UI 占位；没有写入、聚合或展示真实用量 |
| Chroma / Neo4j | PARTIAL | Docker 服务存在，但没有 Python client、同步服务或业务 API 集成 |
| TextChunk / pgvector | PARTIAL | ORM 模型存在；没有分块、embedding、写入、检索流程 |
| 小说导入 | PARTIAL | TXT 上传和基础分章可用；没有进度反馈、大文件分片、作者/类型推断、目录检测 |
| 首页仪表盘 | PARTIAL | 页面存在，但统计和最近活动是硬编码占位 |
| 写作中心 | PARTIAL | 页面存在，但新建按钮 disabled，数据是空数组占位 |
| 分析 / 人物 / 时间线 / 同人文 API | PARTIAL | 查询类路由仍是空状态；写入/生成/抽取类占位端点已改为 HTTP 501 |

## MISSING

| Area | Status | Evidence / Gap |
|---|---|---|
| RAG 三级分块管线 | MISSING | 无 `embedding_service.py`、`rag_service.py`、分块服务或批处理任务 |
| BGE-large-zh-v1.5 本地 embedding | MISSING | 无模型服务、Docker worker、调用配置 |
| Chroma 写入与检索 | MISSING | 无 Chroma client 代码 |
| pgvector 检索 | MISSING | 模型有 embedding 字段相关设计，但无向量索引查询实现 |
| 混合语义搜索 | MISSING | 只有小说标题/作者列表搜索，无文本块搜索 |
| 小说阅读详情页 | MISSING | 文档规划 `novels/[id]`，实际没有该路由 |
| AI 剧情分析引擎 | MISSING | `analysis.py` 仅占位；无摘要、叙事结构、伏笔、情感曲线 |
| RexUniNLU / NLP 管线 | MISSING | 无 NER、关系抽取、冲突消解服务 |
| 时间线提取与 D3 可视化 | MISSING | 无事件提取服务；前端未安装/使用 D3 时间线组件 |
| 人物关系图谱与 AntV G6 | MISSING | 前端依赖无 `@antv/g6`；无图谱组件或 Neo4j 查询 |
| 同人文续写引擎 | MISSING | 无 RAG 上下文注入、风格指纹、流式续写实际调用 |
| 富文本/Markdown 编辑器 | MISSING | 无编辑器组件或续写章节管理页面 |
| 导出 TXT / Markdown / EPUB | MISSING | 无导出 API 或前端入口 |
| 用户认证与权限隔离 | MISSING | 无 NextAuth 或后端用户上下文 |
| API Key 加密存储 | MISSING | 后端字段名为 `api_key`，没有加密层 |
| 自动化测试体系 | MISSING | 未发现后端 pytest、前端测试、端到端 smoke 脚本 |

## Required Startup Fixes

1. 已完成：修复前后端 API 契约：小说列表分页响应、AI 模型字段、测试连接响应处理。
2. 已完成：将占位成功端点改为 HTTP 501，避免 `/gsd auto` 误判完成。
3. 已完成：添加最小 smoke verification 文档，覆盖后端 import/compile 和前端 TypeScript/build。
4. 已完成：更新文档状态，把 Phase 1 从“已完成”降级为“PARTIAL，启动修复中”。

## GSD Starting Point

第一个 active milestone 必须从“审计与启动修复”开始。不要直接进入原文档中的 Phase 2 RAG，直到上述启动修复通过验证。
