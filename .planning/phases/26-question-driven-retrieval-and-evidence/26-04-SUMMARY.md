# 26-04 SUMMARY — Reader/Analysis Chat 消费者接入 QueryPlan

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped; 26-01..26-03 done)

## What Was Built

1. **`backend/app/services/queryplan/service.py`** — 共享消费者 seam（REQ-QP-04 / D-10）：
   - `ConsumerPlanBlocked`（携带稳定 reason_code）、`ConsumerManifestResult`（无模型调用的
     冻结检索/证据图）、`CitationJumpTarget`、`ConsumerQueryPlanView`
     （trace / availability / fallback / citation_jump / cutoff / approval）；
   - `QueryPlanService.build_consumer_request`（同一 payload 构造器，Reader 传 selection、
     Analysis 传 chapter_range）、`parse_consumer_request`（fail-closed）、
     `execute_consumer`（全链 → leaf-only cited-answer gate）、
     `execute_consumer_manifest`（上下文构建期冻结，不调模型）；
   - `execute()` 重构为 `build_manifest()` + producer/gate，原行为不变。
2. **`backend/app/services/reader_chat/retrieval.py`** — `build_source_snapshot`（章节冻结 →
   `SourceSnapshot`，确定性 snapshot hash，两消费者共享同一 leaf/raw 权威）、
   `chat_retrieval_dimension_results`（把 chat 检索栈候选转成精确重切的 `EvidenceRef`，
   不可重切者绝不成证据，D-07/D-15）。
3. **`backend/app/services/reader_chat/context.py`** — Reader consumer seam：
   `build_reader_consumer_request`（selection anchor）、`run_reader_queryplan`
   （owner/cutoff/spoiler 重验证 → 共享 seam；blocked → 稳定 code）；
   `assemble_context_manifest` / `assemble_range_context_manifest` 可选 `queryplan_view`
   把 trace 嵌入 `prompt_inputs`（非证据）。
4. **`backend/app/services/analysis_chat/query_adapter.py`（新建）** — Analysis consumer：
   `AnalysisQueryPlanAdapter`（chapter_range anchor；`resolve_scope` 用
   `narrow_chapter_range` 收窄到阅读进度；start 超进度 422 `chapter_beyond_cutoff`；
   whole-book 仍需 per-novel 开关）。
5. **`backend/app/schemas/reader_chat.py` + `conversations.py`** — `MessageView.queryplan`
   （`QueryPlanTraceView`）暴露 trace/citation 级数据（从冻结 manifest `prompt_inputs` 回显）。
6. **`backend/tests/integration/queryplan/test_chat_consumers.py`（新建）** — 17 用例消费者
   契约 + smoke（共享核心、不同 anchor、leaf-only gate、owner/cutoff/spoiler/whole-book
   重验证、consumer view、QA fixture 冻结、reader seam 与 analysis adapter smoke）。
7. **前端** — `analysis-chat-panel.tsx` 暴露 QueryPlan trace 条（anchor/cutoff/abstained/
   引用数/部分维度不可用）；`e2e/reader-chat-queryplan.spec.ts`（新建，12 用例跨 3 浏览器）；
   `api.ts` 增加 `QueryPlanTraceView`；vitest 新增 trace 覆盖。

## 验收

| 项 | 结果 |
|---|---|
| `venv/Scripts/python.exe -m pytest tests/integration/queryplan/test_chat_consumers.py -q` | ✅ **17 passed** |
| 回归 `tests/unit tests/adversarial tests/integration/queryplan` | ✅ **745 passed** |
| ruff（全部改动文件） | ✅ 全绿 |
| `cd frontend && npm run test -q` | ✅ **282 passed（36 files）** |
| `npx playwright test --list e2e/reader-chat-queryplan.spec.ts` | ✅ 解析通过（12 用例） |
| e2e 实际执行 | ❌ 环境限制（见下） |
| 前置 gate `python scripts/check_phase_execution_gate.py` | ⛔ 仍 BLOCKED（Phase 22 0/3，未改写） |

## 设计决策（D-01..D-16 授权范围内）

- **anchor 语义**：Reader seam 把 selection anchor 的 `chapter_id` 记为**章序号**
  （QueryPlan 解析器 scope 检查按 chapter-number 与 cutoff 比较；证据重切一律走
  evidence refs 的 DB chapter id，绝不依赖 anchor）。
- **manifest-only vs full answer**：上下文构建期只冻结检索/证据图（`execute_consumer_manifest`，
  不调模型）；模型调用仍在 chat worker，`execute_consumer` 用于完整 leaf-only gate 证明。
- **共享检索**：QueryPlan raw_text 维度的证据来自 chat 自身 `retrieve_visible_evidence`
  （candidate spans），再对冻结快照精确重切——不建立第二检索栈。
- **无新表/无 migration**：trace 经 `prompt_inputs["queryplan"]` 回显，Manifest 仍 checksum
  内容寻址。

## 备注 / 偏离

- **超出 PLAN files_modified 的改动（已最小化）**：为把 trace/citation 暴露到消息层，
  需改 `backend/app/schemas/reader_chat.py`（新增 `QueryPlanTraceView` + `MessageView.queryplan`）、
  `backend/app/services/reader_chat/conversations.py`（`_queryplan_view` 回显）、
  `frontend/src/lib/api.ts`（类型）。`conversations.py` 的 `ProductionContextBuilder` 未改：
  `run_reader_queryplan` / `AnalysisQueryPlanAdapter` 已就绪，正式接入生产构建器留待
  26-06/27（避免在 26-04 内触碰消息创建事务路径）。
- **e2e 环境限制（pre-existing）**：本机 Next 16.3.0-canary.6 + Turbopack 无法编译
  （`/analysis?novel=11` 返回 HTTP 500；`Persisting failed … 拒绝访问 (os error 5)` +
  Google Fonts 网络不可达）。`npx playwright test` webServer 180s 超时。spec 已通过
  `--list` 解析并可在环境修复后直接运行；断言无法实际执行，记录为环境限制。
- **PG 依赖的集成测试**：`tests/integration/reader_chat` 因沙箱无 PostgreSQL 连接挂起
  （既有环境问题，与本次改动无关）；`test_chat_consumers.py` 为纯快照契约，不依赖 PG。
- 未做任何 git commit（主代理统一处理）。
