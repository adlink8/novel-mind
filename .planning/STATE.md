---
gsd_state_version: 1.0
milestone: v0.5
milestone_name: Automated Quality and CI Gates
status: phase_complete
last_updated: "2026-07-12T20:00:00.000Z"
progress:
  total_phases: 5
  completed_phases: 3
  total_plans: 25
  completed_plans: 25
  percent: 100
---

# Project State

## Project Reference

See `.planning/PROJECT.md` and `IMPLEMENTATION-STATUS.md`.<br>
系统结构以 `docs/architecture/` 为准。

**Core value:** 先建立可信、安全、可迁移的实现基线，再扩展 RAG。

## Current Position

Phase: 06 (automated-quality-ci) — COMPLETE
Plan: 7 of 7 (06-01..06-07 COMPLETE)

- Milestone: **v0.5 - 自动质量与 CI 门禁**
- Current branch: `feat/phase2-wave2-embedding`
- Status: 06-07 complete — ci-gate aggregate, branch protection contexts=["ci-gate"], phase release gate PASS.
- Last activity: 2026-07-12 — Executed 06-07 (ci-gate + protection + release verifier).

## Auto Routing

Phase 06 is ready for plan verification and later explicit execution. Auto-start remains disabled.

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

## Phase 06 Progress

### 06-01 COMPLETE

- pytest markers unit/integration/contract/live (+ e2e scope), fail-closed classification gate
- marker timeouts D-16; live embeddings no random fallback
- coverage/timeout/flake policy locked

### 06-02 COMPLETE

- `docker-compose.ci.yml` + `.github/ci/service-lock.json`
- Postgres `16.10@sha256:21f6013…` on :5433 / `novelmind_ci`
- Chroma `1.5.9@sha256:abcce7c…` on :8002, health `/api/v2/heartbeat`, client `chromadb==1.5.9`
- Integration tests: 12 postgres + 8 chroma passed against real services

### 06-03 COMPLETE

- Content-hash `SourceSnapshot` + signed `EvalCase` freeze pipeline (regenerate≤2 → quarantine)
- G/J isolation: different model_family AND weights/revision; offline stub generator/judge
- Adversarial suite fail-closed (`invalid_fixture`/`failed_policy`, metrics=null)
- Independent signed Judge calibration (3-repeat, CFA=0, consistency≥0.80); domain/hash isolated from benchmark
- Alembic head `f6a0303ragfix`; tests 26 fixture + 8 calibration passed

### 06-04 COMPLETE

- SUT retrieve+answer scoring; faithfulness/relevance/context precision/recall@5
- Deterministic arbiter with locked D-08 thresholds; fail-closed on missing policy/baseline/health/lineage

### 06-05 COMPLETE

- OpenAPI export + frozen baseline + oasdiff v1.17.0 (Python fallback); pos/neg fixtures
- Frontend `evalApi` / `use-eval` / eval store consume legacy + all quality statuses + deprecation
- Playwright 1.61.1: chromium-desktop + chromium-mobile-390; core + error/isolation journeys
- Verified: contract 7 passed; vitest 54 passed; playwright 10 passed
- Durable quality worker: lease/heartbeat/checkpoint/resume/cancel + stage-cache idempotency
- Legacy Eval API compatibility fields + `/api/eval/quality/*` durable endpoints
- Live dual-model test: Ollama outage → `blocked_dependency`, metrics=null
- Tests: 16 scoring + 2 live + 31 worker/api/service

### 06-06 COMPLETE

- Unified `.github/workflows/ci.yml` producer DAG; legacy workflows disabled (no push/PR triggers)
- Fork-safe: no pull_request_target; secret/self-hosted/write jobs gated off PR
- Timeouts D-16; artifact retention D-17; isolated alert job D-18
- Nightly self-hosted dual-model + signed promote-baseline (passed/qualified only)
- actionlint v1.7.12 clean; pytest tests/ci → 49 passed

### 06-07 COMPLETE

- Fail-closed aggregate job `ci-gate` (always()) over event-aware producer matrix
- Branch protection on `adlink8/novel-mind` default `master`: required contexts exactly `["ci-gate"]` (live GET readback)
- Release gate verifier: seven SUMMARYs + policy/signed evidence + remote protection
- Tests: test_ci_gate / test_branch_protection / test_release_gate; full tests/ci green

## Open Work

- pgvector 双写备用路径
- 大文件（>5MB）流式上传
- 将 90 条 candidate 完成人工确认/驳回，达到 100 条高质量 confirmed 的 issue 门槛
- 校准 gold_chunks；当前 6 次运行 Recall/Precision/MRR/NDCG 均为 0

## Next Action

Phase 06 complete. Prefer milestone audit / next planned phase; do not re-open 06-07 unless protection drifts.
