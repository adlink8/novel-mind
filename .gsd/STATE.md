# Project State

## Project Reference

See `.gsd/PROJECT.md` and `IMPLEMENTATION-STATUS.md`.

**Core value:** 先建立可信、安全、可迁移的实现基线，再扩展 RAG。

## Current Position

- Milestone: v0.2 - 安全与架构修复
- Phase: 2 of 3
- Current branch: `feat/phase2-wave2-embedding`
- Status: Wave 2 核心开发完成
- Last activity: 2026-06-12 17:30 — 龙族小说导入测试通过，项目目录整理
- Progress: 02-01 完成，02-02 完成，Wave 2 核心开发完成，导入管线集成验证完成

## Auto Routing

`.gsd/phases/02-security-and-architecture-remediation/02-01-PLAN.md` — completed

Remaining P1 items (SSRF DNS validation, import persistence, dependency audit) are tracked separately.

## Completed In This Milestone

### Wave 1 — 小说阅读页 + 导入
- 前端阅读页组件（sidebar / content / progress-bar）
- 后端章节 API（ChapterSummaryResponse / ChapterResponse 拆分）
- 多编码检测导入（UTF-8 / GBK / GB18030 / Big5 / Shift_JIS）
- 导入进度跟踪 API
- **2026-06-12 集成验证**：《龙族Ⅰ·火之晨曦》539KB GB18030 小说导入成功（11 章 / 274,011 字）
- **目录整理**：小说源文件统一存放于 `backend/uploads/`，根目录无散落 .txt

### Wave 2 — 安全与架构修复（02-01 核心阻断项）
- `.gitignore` 行尾注释修复
- `cryptography` 依赖声明与安装
- 动态路由目录修复 `[id/` → `[id]`
- `alembic.ini` GBK 编码问题
- CI 分支覆盖（main / master / develop）
- 前端 ESLint 非交互配置
- Novel 详情响应瘦身（移除 source_path，chapters 使用摘要）
- JWT + bcrypt 认证框架（User 模型 / 注册 / 登录 / Bearer Token）
- 小说资源所有权隔离（owner_id + 权限检查）
- 加密密钥持久化（从 settings.secret_key 派生）
- 上传文件生命周期安全（64KB 分块读取 + 原子重命名）

### Wave 2 — RAG 管线核心功能
- 文本分块服务（chunking_service.py）
- 向量存储服务（vector_store.py，ChromaDB 集成）
- 索引管线服务（indexing_service.py）
- RAG API 端点（语义搜索、触发索引、查询索引进度）
- TextChunk 模型与 Alembic 迁移
- RAG 相关 Schema 定义

## Open Work

- SSRF DNS 解析后验证（P1）
- 导入任务持久化（P1）
- 生产依赖漏洞审计（P1）
- RAG 管线集成测试
- 混合语义搜索 API

## Verification Snapshot

- Backend pytest: 172 passed
- Frontend Vitest: 22 passed
- Frontend build: 成功，路由 `/novels/[id]`
- Frontend lint: 非交互，无错误
- Alembic current: `f3c8b7b2dbf7`
- 导入测试：《龙族Ⅰ·火之晨曦》11 章 / 274,011 字导入成功
- 数据库：PostgreSQL 16 + pgvector，ChromaDB 正常
