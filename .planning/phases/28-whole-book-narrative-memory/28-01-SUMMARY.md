# 28-01 SUMMARY — Failure Classification and Recovery

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 28 override 2026-08-03)

## What Was Built

1. **`backend/app/services/narrative_memory/recovery.py`** — failure classification 与幂等
   resume：`classify_failure`（异常→稳定 ReasonCode + FailureClass）、`build_resume_plan`
   （只重跑无终态 stage）、`isolate_chapter` + `block_dependents`（传递闭包，completed 兄弟
   节点不回退）、`validate_cache_reuse`（拒绝 source/lineage/package 漂移）。
2. **`builder_contracts.py`**（修改）— TerminalState/FailureClass/ReasonCode/StageLineage/
   ResumePlan。
3. **`builder_repository.py`**（修改）— `mark_stage`（terminal_state + reason_code +
   append-only checkpoint journal）、`record_checkpoint`/`block_dependents`/`isolate_stage`/
   `recompute_terminal_states`/`increment_resume_count`/`get_ledger_totals`。
4. **`builder_worker.py`**（修改）— 接入 RecoveryCoordinator，per-stage idempotency
   key/source checksum/lineage/journal。
5. **`models/narrative_memory_builder.py`**（修改）— 新列 + `NarrativeMemoryBuildCheckpoint`。
6. **Migration `20260801_2801_nm_checkpoint_ledger.py`**：revision=`20260801_2801`、
   down_revision=`20260801_2703`，upgrade/downgrade 往返 EXIT=0、`alembic check` 零 drift、
   旧行 terminal_state 归一化。
7. **Fixtures**：`failure_matrix_v1.json`（12 项注入矩阵）。
8. **测试**：`test_recovery.py` 16 + `test_narrative_memory_safety.py` 9 +
   `test_checkpoint_migration.py` 5。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/narrative_memory/test_recovery.py tests/adversarial/test_narrative_memory_safety.py tests/integration/narrative_memory/test_checkpoint_migration.py -q` | ✅ **30 passed** |
| `pytest tests/integration/narrative_memory/test_checkpoint_migration.py -q`（两次） | ✅ 5 passed / 5 passed |
| `alembic heads` | ✅ 单 head `20260801_2801` |
| `pytest tests/unit -q`（全量） | ✅ **638 passed** |
| `pytest tests/integration/narrative_memory -q` | ✅ **111 passed**（3 failed 为既有 `.venv` 路径环境问题） |
| `pytest tests/unit/world_model tests/integration/world_model -q` | ✅ **128 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- 任何异常都产生显式终态（completed/isolated/blocked）或可恢复 checkpoint；resume 不重跑
  已确认 stage；单章失败只隔离章节并阻断依赖节点，无 whole-book restart；
- 精确 cache 仅 checksum-identical 输入可复用；cost/budget/evidence/owner/spoiler 门可审计；
- 输出 immutable candidate-only，无 pointer/cutover（adversarial AST 扫描证明）。

## 备注 / 偏差

- 6 个既有集成测试的 head 断言随 2703→2801 同步更新（必要变更）。
- 章节失败 status 保持 `failed` 兼容既有测试，显式终态由新增 `terminal_state="isolated"`
  承载（D-02 语义不破坏存量断言）。
- `test_qualification_command_pg.py` 3 个失败是既有 `.venv`（应为 `venv`）路径硬编码环境
  问题，非本阶段引入。
