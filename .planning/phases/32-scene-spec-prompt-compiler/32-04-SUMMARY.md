# 32-04 SUMMARY — Validation, Safety and Prompt Preview

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 32 override)

## What Was Built

1. **`backend/app/services/prompt_compiler/revisions.py`** — PromptRevision append-only
   review、approve/reject/history 与 stale/hash gate（`evaluate_prompt_approval_gate` 纯门 +
   review service + envelope）。
2. **`backend/app/api/prompt_revisions.py`**（修改）— owner/version/hash scoped
   approve/reject/history 端点。
3. **`backend/app/models/prompt_revision.py`**（修改）— `PromptRevisionReviewEvent`
   append-only 表 + 不可变监听。
4. **`backend/migrations/versions/20260801_prompt_review_events.py`** — 单 head、
   upgrade/downgrade 往返、`alembic check` 零 drift。
5. **前端**：`scene-spec-api.ts`、`preview.tsx`、`diff.tsx`、`scene-spec.test.tsx`（18p）、
   `e2e/scene-spec.spec.ts`（环境受限）。
6. **测试**：`test_prompt_revision_review.py` 17。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/integration/prompt_compiler/test_prompt_revision_review.py -q`（两次） | ✅ 17 passed / 17 passed |
| `alembic heads` | ✅ 单 head `20260801_prompt_review_events` |
| `pytest tests/unit -q`（全量） | ✅ **875 passed** |
| `pytest tests/integration/scene_spec tests/integration/key_scenes -q` | ✅ **34 passed**（相邻回归） |
| `npx vitest run scene-spec` | ✅ **18 passed** |
| `npx vitest run`（全量） | ✅ **348 passed / 41 files** |
| `from app.main import app` | ✅ OK |

## 关键设计

- SceneSpec/Prompt 可从证据+VB 版本确定性重建，unsupported detail fail closed；
- owner/novel/版本/snapshot/evidence-hash/cutoff 生效（8 个 lineage hash 校验）；
- candidate-only 投影、approval gate、budget `not_applicable` 显式持久化；
- review 事件 append-only 幂等（`(owner,novel,revision,event_key)` 唯一）。

## 备注 / 偏差

- 新增 PLAN 未列的 model + migration（`prompt_revision_review_events` 表）：append-only
  review 事件幂等持久化，沿用 VisualBibleReviewEvent/SceneReviewDecision 约定。
- PLAN verify 引用的 `tests/adversarial/test_scene_spec_boundaries.py` 仓库中不存在；
  等价 adversarial 覆盖已放入集成测试内纯门单测。
- e2e 仅记录（Next canary 编译失败环境限制）；页面挂载槽位 `/novels/{id}/scene-spec`
  尚未存在。
- `diff.tsx` react-hooks lint 1 处与既有 visual-bible/entity-sheet.tsx 相同的已接受模式。
