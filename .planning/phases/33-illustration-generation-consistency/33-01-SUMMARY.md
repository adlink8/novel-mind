# 33-01 SUMMARY — Illustration Job and Asset Contract

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 33 override)

## What Was Built

1. **`backend/app/models/illustration_job.py`** — IllustrationJob/IllustrationAttempt/
   IllustrationBudgetLedger/IllustrationBudgetReservation/IllustrationReviewEvent。
2. **`backend/app/models/illustration.py`** — AssetRevision/ConsistencyReport
   （approval_state 为唯一可变投影）。
3. **`backend/app/schemas/illustration.py`** — IllustrationLineage/JobContract/
   AssetRevisionContract/ConsistencyReportContract/PriceSnapshot + 服务端门。
4. **`backend/migrations/versions/20260801_illustration_jobs.py`** — 7 张表、单 head、
   upgrade/downgrade 往返、`alembic check` 零 drift。
5. **`backend/app/services/illustrations/budget.py`** — 预算/成本服务。
6. **测试**：`test_contracts.py` 35 项。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/illustrations -q`（两次） | ✅ 35 passed / 35 passed |
| `alembic heads` | ✅ 单 head `20260801_illustration_jobs` |
| `pytest tests/unit -q`（全量） | ✅ **910 passed** |
| `pytest tests/integration/scene_spec tests/integration/prompt_compiler -q` | ✅ **25 passed**（相邻回归） |
| `from app.main import app` | ✅ OK |

## 关键设计

- vocabulary closed + pinned hash、idempotency key lineage 确定性/变化、空成功 asset 拒绝、
  asset lineage 门、budget reserve/settle/unknown fail-closed、consistency evidence-not-canon、
  approval 状态机 + 幂等 + proposal_ready 门、ORM append-only + approval 投影。

## 备注 / 偏差

- 修复两处 migration 结构问题（FK 依赖顺序 + illustration_jobs 唯一约束）——实现中发现并
  回填，ORP 契约不变。
- `validate_asset_revision_contract` 移除 job_id 比较（JobContract 为创建契约无 id，job_id
  由 ORM FK 强制）。
- IllustrationAttempt 不加 before_delete 监听（避免与 job FK CASCADE 冲突）。
