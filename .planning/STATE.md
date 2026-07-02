---
gsd_state_version: 1.0
milestone: v0.3
milestone_name: Plans
status: unknown
last_updated: "2026-07-02T07:26:41.996Z"
progress:
  total_phases: 4
  completed_phases: 3
  total_plans: 11
  completed_plans: 9
  percent: 75
---

# Project State

## Project Reference

See `.planning/PROJECT.md` and `IMPLEMENTATION-STATUS.md`。<br>
系统结构以 `docs/architecture/` 为准。

**Core value:** 先建立可信、安全、可迁移的实现基线，再扩展 RAG。

## Current Position

Phase: 04 (llm) — EXECUTING
Plan: 3 of 4

- Milestone: **v0.3 - 小说导入 + RAG 索引** — GAPS FOUND
- Phase: 03-01 RAG 评测集与自动化检索优化闭环 — PARTIAL
- Current branch: `feat/phase2-wave2-embedding`
- Status: 导入/索引/混合搜索/前端 VERIFIED；评测基础设施 VERIFIED，质量闭环 PARTIAL
- Last activity: 2026-06-13 16:25 — 后端边界审计、eval owner 隔离/NDCG/迁移漂移修复，v0.3 状态复核

## Auto Routing

v0.3 保持 active，但没有可自动执行 plan。需要人工完成评测题确认和 gold chunk 校准后再继续自动评测。

## Verified In v0.3

### 混合搜索后端

- BM25 全文搜索（PostgreSQL tsvector + ginseng 索引）
- 向量语义搜索（ChromaDB + Ollama nomic-embed-text）
- 加权融合排序（vector 0.5 + bm25 0.5）
- 全局搜索 API：POST /api/search
- 小说内搜索 API：POST /api/search/novels/{novel_id}

### 前端搜索 UI

- 全局搜索栏（防抖 300ms、Command+K 唤起、下拉预览）
- 搜索结果页：/search?q=xxx（含骨架屏、空状态、错误状态）
- 搜索结果卡片（高亮片段、相关度百分比、点击跳转）
- 阅读页内搜索面板（右侧抽屉、Ctrl+F 唤起、Esc 关闭）

### RAG 评测基础设施

- ORM：EvalDataset / EvalRun / EvalResult 三表 + Alembic 迁移
- 评测引擎：3 种策略（bm25 / baseline_vector / hybrid_search）
- 指标：recall@k, precision@k, MRR, NDCG@k + error_case 标记
- API：POST /api/eval/runs, GET /api/eval/runs/{id}, GET /api/eval/datasets, PATCH /api/eval/datasets/{id}
- CLI：scripts/run_rag_eval.py（JSON + Markdown 报告）
- 数据库测试题：100 条（5 类 × 20），其中 10 confirmed / 90 candidate
- 前端：/eval 页面含上边栏导航 + ECharts 可视化（指标对比柱状图 + 趋势折线图 + 延迟趋势）
- Ollama 模型：D:\Ollama\models（bge-m3, nomic-embed-text, qwen3.5:9b, gemma4-local）

### 前端 UI/UX 重构

- 文学编辑台 + AI 研究工作区视觉系统
- 响应式 AppShell：桌面浮动侧栏、移动顶部品牌栏与六项底部导航
- 登录、工作台、书架、阅读器、搜索、评测、设置和创作页统一重构
- 搜索结果高亮移除 `dangerouslySetInnerHTML`，跳转对齐 `/novels/[id]`
- 1280px 桌面与 390px 移动端浏览器验收通过，控制台无错误

## Open Work

- pgvector 双写备用路径
- 大文件（>5MB）流式上传
- 端到端 CI/CD 集成测试
- 将 90 条 candidate 完成人工确认/驳回，达到 100 条高质量 confirmed 的 issue 门槛
- 校准 gold_chunks；当前 6 次运行 Recall/Precision/MRR/NDCG 均为 0
- 实现并验证 faithfulness 与 cost 指标
- 将评测运行从同步 HTTP 请求迁移到持久化后台任务

## Verification Snapshot

| 检查项 | 结果 |
|--------|------|
| Backend pytest | 239 passed（非 e2e）|
| Frontend Vitest | 22 passed |
| Frontend build | Next.js 16 Turbopack passed |
| ESLint | 0 errors |
| TypeScript | 0 errors |
| Ruff | All checks passed |
| Bandit | 0 High, 0 Medium |
| npm audit | 0 vulnerabilities |
| Alembic | upgrade/current/check 通过 (head: 518675fa18f8) |
| RAG e2e | 12 passed (真实 Ollama nomic-embed-text) |
| Eval 三策略 | 可执行，但现有 6 次运行的质量指标均为 0；不能作为效果验收 |
| UI 浏览器验收 | 1280px + 390px 通过，console 0 errors |

## Accumulated Context

### Roadmap Evolution

- 2026-07-02: Phase 04 added and planned: LLM 语义判定与证据门控知识图谱链路。该 phase 参考 `C:\Users\li\Desktop\数据分析` 的候选召回 -> LLM proposal/judgment -> deterministic evidence gate -> accepted graph projection 模式，并明确 Phase 04 执行依赖 Phase 03 缺口关闭或人工覆盖。

### Phase 04 Execution Decisions

- 2026-07-02: 04-02 uses text_chunk pairs as evidence-unit relation candidates; no accepted graph facts are created.
- 2026-07-02: 04-02 reports unavailable live LLM calls as blocked judgment audit status instead of fabricating semantic output.

### Performance Metrics

| Plan | Duration | Tasks | Files |
|---|---:|---:|---:|
| Phase 04 P02 | 10min | 1 task | 7 files |

### Session

- Last session: 2026-07-02T07:25:13.620Z
- Stopped At: Completed 04-02-candidate-packages-and-llm-judgment-PLAN.md
- Resume File: None
