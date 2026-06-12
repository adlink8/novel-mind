# Project State

## Project Reference

See `.planning/PROJECT.md` and `IMPLEMENTATION-STATUS.md`。<br>
系统结构以 `docs/architecture/` 为准。

**Core value:** 先建立可信、安全、可迁移的实现基线，再扩展 RAG。

## Current Position

- Milestone: v0.3 - 小说导入 + RAG 索引
- Phase: v0.3 核心功能已实现
- Current branch: `feat/phase2-wave2-embedding`
- Status: 混合搜索 + 前端搜索 UI + 阅读页内搜索 已交付
- Last activity: 2026-06-12 23:10 — v0.3 混合搜索与前端完成

## Auto Routing

`.planning/ROADMAP.md` — 当前 active milestone: v0.3

## Completed In v0.3

### 混合搜索后端
- BM25 全文搜索（PostgreSQL tsvector + ginseng 索引）
- 向量语义搜索（ChromaDB + Ollama nomic-embed-text）
- 加权融合排序（vector 0.5 + bm25 0.5）
- 全局搜索 API：POST /api/search
- 小说内搜索 API：POST /api/search/novels/{novel_id}
- 测试：210 passed（含 hybrid_search 测试）

### 前端搜索 UI
- 全局搜索栏（防抖 300ms、Command+K 唤起、下拉预览）
- 搜索结果页：/search?q=xxx（含骨架屏、空状态、错误状态）
- 搜索结果卡片（高亮 <mark> 片段、相关度百分比、点击跳转）

### 阅读页内搜索
- 搜索面板（右侧抽屉、Ctrl+F 唤起、Esc 关闭）
- 小说内实时搜索
- 结果高亮 + 点击跳转章节

## Open Work

- pgvector 双写备用路径
- 大文件（>5MB）流式上传
- 端到端 CI/CD 集成测试

## Verification Snapshot

| 检查项 | 结果 |
|--------|------|
| Backend pytest | 210 passed（非 e2e）|
| Frontend Vitest | 22 passed |
| Ruff | All checks passed |
| Bandit | 0 High, 0 Medium |
| TypeScript | 0 errors |
| ESLint | 0 errors |
| Next.js build | 成功 |
| npm audit | 0 vulnerabilities |
| Alembic | upgrade/current/check 通过 |
| RAG e2e | 12 passed (真实 Ollama nomic-embed-text) |
