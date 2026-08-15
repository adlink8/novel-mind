# 29-02 SUMMARY — Retrieval, Citation and Answer Evaluation

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 29 override)

## What Was Built

1. **`backend/app/services/qualification/metrics.py`** — 八桶 retrieval/citation correctness/
   faithfulness/relevance/abstention/fallback/latency p50·p95/cost/calls/tokens 聚合。
2. **`backend/app/services/qualification/report.py`** — lineage-bound `QualificationReport`
   （header + buckets + operations + manifest snapshot + verdict + checksum）。
3. **`backend/app/services/qualification/runner.py`** — candidate/leaf parity runner，消费
   `CandidateManifest`，硬门 fail-closed。
4. **测试**：`test_report.py` 17 + `test_dimension_manifest_parity.py` 13 +
   `test_qualification_lineage.py`（+5）。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/integration/qualification tests/adversarial/test_qualification_lineage.py -q` | ✅ **46 passed** |
| `pytest tests/integration/qualification/test_report.py -q`（两次） | ✅ 17 passed / 17 passed |
| `pytest tests/integration/qualification/test_dimension_manifest_parity.py -q` | ✅ **13 passed** |
| `pytest tests/unit/qualification -q` | ✅ **33 passed**（gold set 回归） |
| `pytest tests/unit -q`（全量） | ✅ **683 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- 报告不以单一总分隐藏失败；每桶 candidate/baseline 各 18 项指标 + worst_cases +
  blocked_reasons；
- lineage mismatch（manifest/header/coverage）→ blocked 且 `buckets == ()`（停止指标汇总）；
- verdict 仅 qualified_candidate 或 blocked，禁止 promotion；
- 硬门失败停止汇总；rubric 违规（spoiler leak/no_answer hallucination/stale citation）→
  blocked 但保留逐桶指标使失败可见；
- 成本漏算即 block（`operations_cost_incomplete`，不零填充）。

## 备注

- 无 DB schema 变更：报告为纯内存契约；如主代理要求持久化报告需另开 migration。
- 服务端 owner 强制：runner 用 gold_set 派生 lineage context 审计 candidate 与 baseline，
  artifact 显式不同 owner/novel/version 判 `cross_owner`。
