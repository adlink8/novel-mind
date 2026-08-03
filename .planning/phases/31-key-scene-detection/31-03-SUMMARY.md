# 31-03 SUMMARY — Human Review and Frozen Key-Scene Set

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 31 override)

## What Was Built

1. **`backend/app/services/key_scenes/review.py`** — `KeySceneReviewService.append_decision`
   （候选级 approve/reject/needs_relink，append-only/幂等）、`freeze`（set 级审批 + 冻结门 +
   冻结 manifest 重算）、`build/load_frozen_set_view`、纯函数 `evaluate_candidate_approval_gate`/
   `evaluate_freeze_gate`；budget 显式 `not_applicable`，审计 details 含 lineage/gate。
2. **`backend/app/api/key_scenes.py`**（修改）— 新增 `POST .../{set_id}/review`、
   `POST .../{set_id}/freeze`、`GET .../{set_id}/frozen`；owner-scoped，404/409 映射。
3. **`backend/app/schemas/key_scene.py`**（修改）— 新增 `FrozenKeySceneSetView` 契约
   （Phase 32 消费）。
4. **`backend/app/services/key_scenes/candidates.py`**（修改）— `derive_candidate_review_states`，
   `load_set_view` 从 append-only 决策派生候选有效 review_state。
5. **前端**：`key-scenes-api.ts`、`review.tsx`、`candidate-card.tsx`、`key-scenes.test.tsx`（15p）、
   `e2e/key-scenes.spec.ts`（环境受限）。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/integration/key_scenes tests/unit/key_scenes -q` | ✅ **80 passed** |
| `pytest tests/integration/key_scenes/test_review.py -q`（两次） | ✅ 15 passed / 15 passed |
| `alembic heads` | ✅ 单 head `20260801_key_scene` |
| `pytest tests/unit -q`（全量） | ✅ **786 passed** |
| `npx vitest run key-scenes` | ✅ **15 passed** |
| `npx vitest run`（全量） | ✅ **330 passed / 40 files** |
| `from app.main import app` | ✅ OK |

## 关键设计

- 候选行不可变（append-only guard），有效 review_state 从 `key_scene_review_decisions`
  派生（最后一次决策的 `to_review_state`），set 行 review_state 是唯一可变投影；
- 冻结 set 可重算（manifest 重算确定性断言）、拒绝不丢失（reject 留在 history）、重复
  approval 不重复生效（decision_key 幂等，DB 仅 1 行）；
- 模型 proposal 与确定性 score/diversity/spoiler 校验及用户选择分离。

## 备注 / 偏差

- 决策动作按 31-01 契约 `approve/reject/needs_relink/supersede`（PLAN 写
  `confirm/reject/needs_review` 已过时）。
- 新增支持文件（FrozenKeySceneSetView、key-scenes-api.ts）不在 PLAN files_modified 但为
  必要实现。
- e2e 无法在本机执行（Next canary 编译失败环境限制）。
