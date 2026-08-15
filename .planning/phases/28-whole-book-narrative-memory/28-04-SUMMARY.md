# 28-04 SUMMARY — Cross-Dimension Closure and One-Click Analysis

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 28 override)

## What Was Built

1. **`backend/app/services/narrative_memory/contracts.py`**（修改）— DimensionStatus/
   DimensionKind/BudgetTotals/DimensionResult/CandidateManifest + 稳定 checksum。
2. **`backend/app/services/narrative_memory/manifest_contract.py`** — parity/checksum/
   pointer guard（`PARITY_FIELDS` 一致校验 + `assert_no_pointer_fields` 深扫描）。
3. **`backend/app/services/narrative_memory/closure.py`** — 跨维度闭合（timeline/relation/
   clue/character/world）+ one-click analysis + facet ranges + SSE frames。
4. **`backend/app/services/narrative_memory/progress.py`** — durable progress/resume/
   notification（DB checkpoints 权威，SSE 仅通知）。
5. **`backend/app/api/narrative_memory.py`**（修改）— GET/POST
   `/{novel_id}/versions/{version_id}/analysis`。
6. **前端**：`narrative-memory-progress.tsx` + `.test.tsx`（4 vitest）、
   `narrative-memory-progress.spec.ts`（e2e，环境受限）。
7. **测试**：`test_closure.py` 8 + `test_manifest_parity.py` 14。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/integration/narrative_memory/test_closure.py tests/integration/narrative_memory/test_manifest_parity.py -q` | ✅ **22 passed** |
| `pytest tests/integration/narrative_memory/test_manifest_parity.py -q`（两次） | ✅ 14 passed / 14 passed |
| `alembic heads` | ✅ 单 head `20260801_2801`（无新 migration） |
| `pytest tests/unit/narrative_memory -q` | ✅ **189 passed** |
| `pytest tests/integration/narrative_memory -q` | ✅ **157 passed**（3 failed 为既有 `.venv` 路径环境问题） |
| `pytest tests/unit -q`（全量） | ✅ **650 passed** |
| `cd frontend && npm run test -q` | ✅ **293 passed / 38 files** |
| `from app.main import app` | ✅ OK |

## 关键设计

- one-click analysis 报告每维度 available/partial/blocked + durable progress + resume
  （POST 持久化到 `run.progress.dimension_statuses`，`load_durable_progress` 从 DB 重建）；
- DimensionResult 与 CandidateManifest 同 snapshot/cutoff/owner/version/budget/lineage 契约
  （`PARITY_FIELDS` + `validate_candidate_manifest`）；
- 无 progress/closure 操作写 active pointer（guard + AST 扫描 + 既有测试）；
- Progress events 复用 Agent SSE/Job transport（仅通知），DB checkpoints 跨重连权威。

## 备注 / 偏差

- API 路径按真实文件 `backend/app/api/narrative_memory.py`（PLAN 写 `routes/` 子目录）。
- 无新表/迁移：durable progress 复用 `narrative_memory_build_runs.progress` JSONB +
  checkpoint journal。
- cancel 走既有 builder worker `request_cancel`，未为报告单独加端点。
- 前端组件独立未挂载进 `analysis/page.tsx`（不在 PLAN files_modified 且环境无法编译验证）。
