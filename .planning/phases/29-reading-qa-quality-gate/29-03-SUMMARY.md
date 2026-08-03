# 29-03 SUMMARY — Browser UAT

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 29 override)

## What Was Built

1. **`backend/tests/integration/qualification/test_browser_contract.py`** — 28 个服务端契约/
   smoke 测试：26-00 gate fail-closed 断言、request scope、CandidateManifest parity tamper×7、
   Frozen Manifest leaf-only allowlist、partial/abstain、failure/cancel/retry 契约、leaf
   citation smoke、e2e spec 形状契约。
2. **`frontend/e2e/reader-chat-quality.spec.ts`** — Reader Chat UAT 路径（citation jump/
   evidence/partial-failure/desktop-mobile/accessibility/spoiler/cutoff）。
3. **`frontend/e2e/analysis-chat-quality.spec.ts`** — Analysis Chat UAT 路径（QueryPlan
   trace/anchor/abstain/boundary/failure/accessibility）。
4. **组件修复**：`reader-chat-panel.tsx`（desktop 自动聚焦、成功刷新清 error、citation chip
   aria-label/focus-visible、job status aria-live、移动 overflow-hidden、MessageBubble 无效
   引用过滤）、`analysis-chat-panel.tsx`（citation 跳转补 `end` 参数、aria-live）、
   `novels/[id]/page.tsx`（读取 start/end 深链高亮原文——citation offset 修复）。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/integration/qualification/test_browser_contract.py -q`（两次） | ✅ 28 passed / 28 passed |
| `pytest tests/integration/qualification -q` | ✅ **58 passed** |
| `pytest tests/unit/reader_chat tests/unit/queryplan -q` | ✅ **172 passed** |
| `pytest tests/unit -q`（全量） | ✅ **683 passed** |
| `cd frontend && npm run test -q` | ✅ **293 passed / 38 files** |
| `npx playwright test --list` | ✅ **33 tests**（desktop/mobile-390/tablet） |
| `from app.main import app` | ✅ OK |

## 关键设计

- 服务端 contract/smoke 在 Playwright 前证明服务契约（fail-closed）；Playwright 是 phase
  gate，不能由截图或 aggregate score 代替；
- 未来章节 metadata 不泄漏（backend cutoff/beyond_cutoff 测试 + e2e `SECRET_FUTURE`
  count=0）；citation 可跳转原文（深链 start/end 高亮）；partial/failure 可理解可重试；
- 不写 active pointer。

## 备注 / 偏差

- **e2e 环境限制（已记录）**：Playwright 实际运行被 Next 16 canary dev server 编译失败
  阻塞（fonts.gstatic.com 网络不可达，webServer exit 143，与既有 `os error 5` 同源）。
  specs 通过 `--list` 解析（33 tests），断言路径完整覆盖 D-06；backend contract/smoke
  为主要验证。
- PLAN 写 `reader-chat.tsx`，实际组件为 `reader-chat-panel.tsx`（按实际路径修改）。
