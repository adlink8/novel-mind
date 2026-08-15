# 37-02 SUMMARY — Constrained Draft Generation

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`backend/app/models/derivative_generation_job.py`** — Job/Attempt/Candidate 三表
   （状态机 + prompt/model/package hash、reserved vs actual usage/cost、budget_policy、
   error_code + 部分唯一索引）。
2. **`backend/app/services/derivative_generation/candidate.py`** — CandidateDraft/CanonDelta/
   BranchSuggestion DTO（extra=forbid + 闭集 intent）+ parse/gate/hash。
3. **`backend/app/services/derivative_generation/runner.py`** — budget gate、deployment、
   prompt 编译、runner、job service、idempotency key。
4. **`backend/app/api/derivative_generation.py`** — create/list/get/run/cancel 5 端点。
5. **`backend/migrations/versions/37_derivative_generation01.py`** — revision=
   `20260802_derivative_generation01`、单 head、往返、`alembic check` clean。
6. **测试**：`test_candidate_runner.py` 35 + `test_derivative_generation_job.py` 11。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/derivative_generation/test_candidate_runner.py tests/integration/test_derivative_generation_job.py -q`（两次） | ✅ 46 passed / 46 passed |
| `alembic heads` | ✅ 单 head `20260802_derivative_generation01` |
| `pytest tests/unit -q`（全量） | ✅ **1120 passed** |
| `pytest tests/integration/test_derivative_generation.py -q` | ✅ **10 passed**（37-01 回归） |
| `pytest tests/adversarial -q`（全量） | ✅ **415 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- LLM/agent 只能产严格 schema candidate；provider/budget/usage/failure lineage 持久化；
- fake gateway 可重放（相同响应 → 相同 response_hash/draft/usage）；同一 idempotency key
  不重复扣费/生成（replayed=true，仅 1 次 provider call）；
- provider 输出永不直接写 Original 或 active pointer（chapters 内容不变、derivative_revisions
  计数 0、fork active=false、candidate 行 append-only）；
- 超预算或 schema 非法不调用/不发布（budget 拒绝时 paused_budget 且 transport.calls==[]）；
- 任务状态可恢复；稳定 reason code（budget_exhausted/unknown_pricing/provider_timeout/
  provider_error/schema_invalid/package_hash_mismatch/job_not_runnable 等）；terminal job
  不再被静默重调；
- sealed package → ai_router → schema candidate → deterministic gate 链（candidate|blocked|
  needs_override）。

## 备注 / 偏差

- budget 门控范围为单请求实例（测试可注入）；跨请求持久 budget 台账不在本 phase。
- BranchSuggestion schema 强制 enabled_by_default=false（D-37-05），仅存储候选输出。
