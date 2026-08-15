# 26-02 SUMMARY — 检索适配器与融合

**Status:** COMPLETE | **Date:** 2026-08-02 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped)

## What Was Built

1. **`backend/app/services/queryplan/adapters.py`** — 维度适配器注册表：
   - `DEFAULT_ADAPTERS`：raw/event/causal/character/relation/timeline/clue/world/NM 全维度；
   - `DimensionResult`：status（available/partial/unavailable）+ 稳定 reason code + provenance
     + stage；
   - 三段链 `evaluate_dimension_chain`：exact/domain reader → deterministic heuristic
     candidate recall → stable partial/unavailable reason；
   - heuristic 输出 `evidence_eligible=False`（结构性保证，绝不成为 fact/EvidenceRef/citation）；
   - `run_plan_adapters`：预算/owner/version/cutoff/provenance 贯穿。
2. **`backend/app/services/queryplan/fusion.py`** — 确定性 provenance-preserving 融合：
   - `fuse_dimension_results`：确定性排序、单一来源可比、snapshot 越界 fail-closed、
     预算传递、checksum；
   - 缺维度永不变成 empty-success 或 uncited fact。
3. **测试**：`test_adapters.py` 25 用例 + `test_fusion.py` 19 用例。

## 验收

| 项 | 结果 |
|---|---|
| `cd backend && venv/Scripts/python.exe -m pytest tests/unit/queryplan -q` | ✅ **96 passed**（26-01 52 + 新增 44，零回归） |
| `cd backend && venv/Scripts/python.exe -m pytest tests/integration/queryplan -q` | ✅ **28 passed** |
| ruff lint | ✅ 全绿 |
| 依赖 | ✅ 无新增、无第二检索栈、无 active-pointer 写入 |

## 设计决策

- 生产 reader 经 `ReaderResolver` 注入而非直接 import 既有服务——保持模块纯函数、可单测
  （无 DB）；既有服务的 reader id 已登记，实际绑定留给 26-03 evidence 阶段。
- `character_state`/`world_rules` 按 26-01 保持 reader_id=None（Phase 27 前无生产 reader）；
  运行时适配器仍走 heuristic，命中关键词可报告 `partial`（candidate-only），是运行时细化。

## 备注 / 偏离

- PLAN Task 2 引用的 `single_book_v1.json`（NM 资格 fixture）只含 cases 元数据，无原始章节
  文本，无法构建 SourceSnapshot；改用测试内冻结章节记录（含 content_hash 的 4 章）覆盖全维度。
