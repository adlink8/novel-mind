# 37-01 SUMMARY — Context Package Compiler

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`backend/app/models/derivative_context.py`** — append-only 密封 package 行（space 绑定
   fanfiction_canon、64-hex 约束、update/delete fail-closed）。
2. **`backend/app/schemas/derivative_context.py`** — 严格 wire 契约（fork_id+intent+可选
   through_chapter）。
3. **`backend/app/services/derivative_generation/context_package.py`** — 纯函数（canonical
   hash/budget/cutoff/dimension）+ `ContextPackageCompiler`（六维读取：world_model_entities/
   rules/events/causal_edges + clue lifecycle replay + branch-aware retrieval）。
4. **`backend/app/api/derivative_context.py`** — POST 编译 / GET list / GET detail。
5. **`backend/migrations/versions/37_derivative_context01.py`** — revision=
   `20260801_derivative_context01`、单 head、往返、`alembic check` clean。
6. **测试**：`test_context_package.py` 22 + `test_context_package_boundaries.py` 13 +
   `test_derivative_generation_boundaries.py` 11 + `test_derivative_generation.py` 10。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/derivative_generation tests/adversarial/test_context_package_boundaries.py tests/adversarial/test_derivative_generation_boundaries.py tests/integration/test_derivative_generation.py -q`（两次） | ✅ 56 passed / 56 passed |
| `alembic heads` | ✅ 单 head `20260801_derivative_context01` |
| `pytest tests/unit -q`（全量） | ✅ **1085 passed** |
| `pytest tests/adversarial -q`（全量） | ✅ **415 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- 生成前冻结六维 cutoff 状态/证据/未回收线索/world rules/user intent；
- package 可审计可重放（确定性 package_hash、byte-replayable、verify_package_hash
  fail-closed）；
- fork manifest → branch-aware retrieval → immutable context package 链；无 silent
  write-back to original canon（仅 ContextPackageRecord 可写，AST 断言）；
- 预算超限在 provider 调用前 blocked（budget_exhausted 422）；cutoff 只可缩小。

## 备注 / 偏差

- Migration revision ID 缩短为 `20260801_derivative_context01`（超 varchar(32) 限制）。
- `project_id` 字段已移除（编译器当前仅按 fork/cutoff 编译，不消费 project）。
- 一致性 gates 的判定逻辑属 37-02+；本 phase 以 package 层 fail-closed + AST 源检查覆盖
  可审计性。
