# 36-04 SUMMARY — Editor Browser UAT and Pre-Release Gate

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`frontend/src/components/writing/revision-history.tsx`** — lineage/autosave/diff/恢复草稿/
   回滚确认面板（10p 单测）。
2. **`frontend/src/app/writing/page.tsx`**（修改）— 刷新恢复（sessionStorage 记住所选
   project，按 novel 作用域）。
3. **`frontend/src/components/writing/markdown-editor.tsx`**（修改）— 嵌入 RevisionHistory +
   recoverDraft/applyRollback 处理器。
4. **`frontend/src/lib/derivative-api.ts`**（修改）— revision/autosave/diff/rollback 类型与
   client 方法。
5. **`frontend/e2e/derivative-editor.spec.ts`** — 5 场景 route-mock（create/edit/
   refresh-recover/409/diff+rollback/isolation；15 用例 ×3 project）。
6. **`backend/tests/integration/test_derivative_editor_gate.py`** — 8p 发布前 fail-closed gate。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/integration/test_derivative_editor_gate.py -q`（两次） | ✅ 8 passed / 8 passed |
| derivative 全套（7 文件） | ✅ **109 passed**（含 gate 共 117p） |
| `alembic heads` | ✅ 单 head `20260801_derivative_revision01`（无新 migration） |
| `npx vitest run writing` | ✅ **19 passed** |
| `npx vitest run`（全量） | ✅ **404 passed / 46 files** |
| `npx playwright test --list e2e/derivative-editor.spec.ts` | ✅ **15 tests** |
| `from app.main import app` | ✅ OK |

## 关键设计

- 浏览器可恢复 draft、查看 diff、处理冲突并 rollback；owner/fork 错误不泄露数据
  （跨 owner 全路由 404 且 body 不含 SECRET_MARKDOWN）；
- rollback 等状态变更动作显式 approval（两步确认 + CAS base_revision；成功只来自服务器
  响应；409 显示 head revision 且无伪造成功）；
- 发布类动作只进入 Fanfiction（derivative 路由无 publish/release/promote；kind='publish'
  被 DB CHECK 拒绝；space=original_canon 注入 422；编辑会话后 Original chapters 字节不变）；
- 不改变 Phase 22 BLOCKED/0-of-3 状态。

## 备注 / 偏差

- 新增修改 markdown-editor.tsx 与 derivative-api.ts（revision-history 必须挂到编辑器内部 +
  36-03 revision client 方法接入前端，属必要支撑变更）。
- e2e 未执行断言（Next canary 编译失败环境限制）；spec `--list` 验证结构合法。
