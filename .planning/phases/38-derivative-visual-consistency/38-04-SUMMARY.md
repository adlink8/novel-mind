# 38-04 SUMMARY — Derivative Visual Review, Version Lineage, and Browser Verification

**Status:** COMPLETE | **Date:** 2026-08-05 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`backend/app/services/derivative_visual/review.py`** — 独立确定性 review seam：
   `review_candidate_asset`（owner-scoped 加载 → from_review_state 校验 → 幂等事件 →
   38-03 同款 `DerivativeVisualAssetView`/`PublishedDerivativeVisualAsset` envelope）、
   `load_review_candidate`/`list_review_candidates`（任意状态、owner/novel 显式正数 scope）。
   状态机不复造：`LEGAL_DERIVATIVE_ASSET_TRANSITIONS` 保持唯一事实源；`blocked`
   （identity drift / 未声明 divergence）永远不可 approve。
2. **`backend/app/api/derivative_visual_review.py`** — review 专用 API：`GET /review`（列表，
   project/fork/review_state 过滤）、`GET /review/{candidate_id}`（详情）、
   `POST /review/{candidate_id}/action`（apply review）。strict `extra="forbid"` DTO、
   `require_owned_novel`+`require_user`；wrong owner 统一 404、blocked approve → 409。
3. **`backend/scripts/run_derivative_visual_review_qualification.py`** — e2e 种子脚本。
4. **`frontend/src/lib/derivative-visual-api.ts`** — 类型化前端 API client。
5. **`frontend/src/components/writing/visual-review-panel.tsx`** — review 面板：展示 source
   refs、identity/style 章节评分、divergence manifest hash、`fanfiction_visual` namespace、
   append-only review 事件链；approve/reject/supersede 必须填 reason（无理由按钮 disabled）；
   blocked/superseded 锁定态；action/compare/reload 均重拉保持一致；无 innerHTML、a11y 齐全。
6. **`frontend/e2e/derivative-visual.spec.ts`** — e2e 6 用例（desktop/390px/tablet）。
7. **修改**：`main.py` 注册 review router（prefix `/api/novels`，与 assets 路由不冲突）；
   `test_visual_namespace_isolation.py` 追加 5 个 38-04 对抗断言；`writing/page.tsx` 挂载面板。

## 独立测试验证（2026-08-05，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/adversarial/test_visual_namespace_isolation.py -q` | ✅ 27 passed |
| `pytest tests/integration/test_derivative_visual.py -q`（CI PG 5433） | ✅ 23 passed（38-03 无回归） |
| `pytest tests/unit/derivative_visual -q` | ✅ 43 passed |
| `alembic heads` | ✅ 单 head `20260802_derivative_asset01` |
| `from app.main import app` + OpenAPI 路由核对 | ✅ 无异常；3 条 review 路由生效、无路径冲突 |
| `npm test -- visual-review` | ✅ 12 passed |
| `npm test`（前端全量） | ✅ 47 files / 416 passed |
| `npx playwright test e2e/derivative-visual.spec.ts --list` | ✅ 6 用例可解析 |

## Fail-closed 边界（T-38-04-01 / T-38-04-02）

- **blocked 不可 approve**：对抗断言 BLOCKED 全 action 非法 + 集成 38-03
  `test_identity_drift_blocks_publish` + 面板锁定态；API blocked approve → 409。
- **wrong owner 统一 404**：`require_owned_novel` + 统一 404 文案；foreign token → 404。
- **source hash 变化不可 publish**：store gate `identity_lineage_mismatch`；
  review.py 无 Original Visual Bible 写路径、不复算冻结 lineage。
- **approved 再 approve / superseded/blocked 无合法 action**：对抗断言 + 面板锁定态。
- **无 bypass**：对抗 AST 扫描 review.py 无裸 `db.get(...)`，所有查询带 owner/novel 过滤。
- **前端无 auto-approve**：按钮需 reason 才 enabled。

## 备注 / 偏差

- 无新 migration（38-04 复用 38-03 表结构）。
- e2e 未实跑：webServer 180s 超时（Next canary 编译 Google 字体失败，已知环境限制）；
  已用 `--list` 确认 6 用例可解析，未伪造通过。
- Phase 22 门禁保持既有 0/3 未动（D-38-04）。
- frontend tsc 39 个既有错误（旧 e2e spec + FanFictionChapter 类型遗留），新文件 0 错误。
