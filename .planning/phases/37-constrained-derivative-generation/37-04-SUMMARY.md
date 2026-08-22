# 37-04 SUMMARY — Explicit Divergence Override

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`backend/app/models/derivative_override.py`** — 显式 CanonDelta override（frozen surface +
   仅 approval 列可变更 + append-only + 每 candidate 唯一约束）。
2. **`backend/app/services/derivative_generation/overrides.py`** — create/approve/reject/list/get。
3. **`backend/app/services/derivative_generation/published_revision.py`** — immutable
   PublishedDerivativeRevision DTO（owner_id/project_id/fork_id/revision_id/version_id/status/
   source_snapshot/manifest_hash/citation_hash/asset_hashes/approval/review，供 Phase 39 消费）。
4. **`backend/app/api/derivative_overrides.py`** — 5 路由。
5. **`backend/migrations/versions/37_derivative_override01.py`** — revision=
   `20260802_derivative_override01`、单 head、往返、`alembic check` clean。
6. **测试**：`test_derivative_overrides.py` 9 + `test_override_no_writeback.py` 12 +
   `e2e/derivative-generation.spec.ts`（15 用例）。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/integration/test_derivative_overrides.py tests/adversarial/test_override_no_writeback.py -q`（两次） | ✅ 21 passed / 21 passed |
| `alembic heads` | ✅ 单 head `20260802_derivative_override01` |
| `pytest tests/unit tests/adversarial -q`（全量） | ✅ **1622 passed** |
| `npx vitest run`（全量） | ✅ **404 passed / 46 files** |
| `npx playwright test --list e2e/derivative-generation.spec.ts` | ✅ **15 tests** |
| `from app.main import app` | ✅ OK |

## 关键设计

- 无 reason/evidence/approval 的 override 被拒绝（missing_reason/missing_evidence/
  missing_approval）；
- 原作空间和 active pointer 永不写入（approve 仅追加 Fanfiction agent_proposal revision，
  Original chapters/canon_space_artifacts 零变更）；
- blocked candidate → explicit override action → derivative revision only 链（clean candidate
  409、cross-fork 409、每 candidate 唯一 override）；
- 跨 owner/space 写回失败证据保留（404/409 + evidence_snapshot 冻结 gate 审计）；
- 生成成功不转质量资格或生产 promotion（DTO 状态恒 derivative_revision）。

## 备注 / 偏差

- e2e 无 derivative-generation UI 页面，spec 采用 route-mock API 级（browser fetch 驱动）。
- 物化 revision kind 用 agent_proposal（approval_state=approved + reason 记录 override 关联）。
- 每 candidate 唯一 override（uq_derivative_overrides_candidate），拒绝/批准均为终态。
