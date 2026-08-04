# 33-03 SUMMARY — Identity and Style Consistency Scoring

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 33 override)

## What Was Built

1. **`backend/app/services/illustrations/consistency.py`** — `ConsistencyEvaluator.evaluate`
   （冻结 fixture 评估 + versioned `ConsistencyReportContract`，保留 evaluator/model/fixture/
   policy lineage）、`ConsistencyReportService`（append-only 幂等持久化 + owner-scoped 读取）、
   `ConsistencyReportView`、`mock_consistency_fixture_registry`。
2. **`backend/app/api/illustrations.py`**（修改）— `POST .../assets/{id}/consistency/evaluate`、
   `GET .../consistency`、`GET .../consistency/compare`、`GET .../consistency-reports` +
   fixture registry seam（默认空→unavailable）。
3. **测试**：`test_consistency.py` unit 17 + integration 5。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/illustrations tests/integration/illustrations -q` | ✅ **71 passed** |
| `pytest tests/unit/illustrations/test_consistency.py tests/integration/illustrations/test_consistency.py -q`（两次） | ✅ 22 passed / 22 passed |
| `alembic heads` | ✅ 单 head `20260801_illustration_jobs`（无新 migration） |
| `pytest tests/unit -q`（全量） | ✅ **927 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- identity/style drift、unsupported detail、unavailable 四种 verdict 可区分；score ∈ [0,1]
  是 review signal（契约无 auto_approve/promote_to_canon/rewrite_visual_bible 字段）；
- fixture 冻结 + hash 敏感；idempotency key 按 owner/novel/asset/report_key/evidence 重放；
  报告行 append-only；
- 默认无 evaluator（生产 registry 空）→ 所有评估 fail-closed 为 `unavailable`；fixture 由
  `set_illustration_consistency_fixtures` seam 注入。

## 备注 / 偏差

- 新增 `tests/unit/illustrations/__init__.py`（计划外第 5 文件）：解决 pytest `prepend`
  导入模式对同名 test_consistency.py 的 "import file mismatch"——按仓库惯例补齐包标记。
- worker 不自动评估：一致性报告仅在显式 POST evaluate 时生成（幂等）；自动评估可作为
  后续决策。
