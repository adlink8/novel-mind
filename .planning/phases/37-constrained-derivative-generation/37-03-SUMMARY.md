# 37-03 SUMMARY — Contradiction and Consistency Gates

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`backend/app/services/derivative_generation/gates.py`** — 确定性一致性门 +
   `evaluate_consistency` + BranchSuggestion verdict；`ContinuityClaim` 新契约模型
   （character_behavior/established_fact/timeline/clue + evidence_keys + chapter_number +
   disposition）。
2. **`backend/app/services/derivative_generation/fixtures.py`** — 冻结 continuation 资格夹具
   （11 fixture + fixture_hash）+ `run_frozen_qualification()`。
3. **测试**：`test_gates.py` 51 + `test_derivative_consistency.py` 24。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/derivative_generation/test_gates.py tests/adversarial/test_derivative_consistency.py -q`（两次） | ✅ 75 passed / 75 passed |
| `alembic heads` | ✅ 单 head `20260802_derivative_generation01`（无新 migration） |
| `pytest tests/unit -q`（全量） | ✅ **1171 passed** |
| `pytest tests/adversarial -q`（全量） | ✅ **439 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- 人物/既定事实/时间线/clue 违规均 blocked（character_contradiction/fact_contradiction/
  timeline_contradiction/clue_contradiction），不自动修正为 canon；每条 violation 定位到
  package/candidate 字段；
- 冻结样例集是资格门（fixture 变更即失败）；通过 ≠ promotion（clean→candidate、
  显式 divergence→needs_override、冲突→blocked 且保留 violations/detail/branch_suggestions）；
- BranchSuggestion 六字段齐全、enabled_by_default 强制 false（schema 拒绝 true/approval
  额外字段）；不建 fork、不写 Canon、不发/复用 approval（gates 源 AST 零 write/approval）；
- 显式 CanonDelta 只覆盖同类 divergence type（覆盖不能被滥用为全局通行证）。

## 备注 / 偏差

- 未改 candidate.py/runner.py/模型/API（runner 接入留待 37-04/05）。
- evaluate_consistency 遵循既有 creative_consistency.py「只消费结构化 claims、不从散文推断」
  模式。
- runner.py（37-02）有 4 个 pre-existing ruff 告警（F401×2/F841×2），非本 phase 引入。
