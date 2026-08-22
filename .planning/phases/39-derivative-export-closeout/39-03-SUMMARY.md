# 39-03 SUMMARY — Export Browser UAT

**Status:** COMPLETE | **Date:** 2026-08-05 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`frontend/src/lib/derivative-export-api.ts`** — 类型化 API client（prepare/approve/
   materialize/download，materialize body 只含 artifact_id/artifact_revision_id/
   approval_id/preparation_hash + branch/fork，无 manifest 组装）。
2. **`frontend/src/components/writing/export-panel.tsx`** — review/download UI：展示
   ExportPreparationArtifact 的 preparation_id / revision / export version /
   manifest checksum、approved asset/citation counts、三维 status 和 blocked reason；
   导出按钮只提交已批准 artifact 的 materialize 请求；下载后校验 materialize 返回
   manifest_hash 与响应 `X-Export-Manifest-Hash` 头；**下载完成不显示为质量通过**
   （质量唯一来源是 AuditCard，quality_qualification 恒 blocked）；EPUB done 文案显式
   "EPUB 互操作性未验证（无 EPUB validator，不标绿）"；无 innerHTML、a11y 齐全。
3. **`frontend/e2e/derivative-export.spec.ts`** — 12 场景 × 3 viewport（desktop/mobile/
   tablet）= 36 tests：两种格式、refresh/reopen、章节/asset/citation 字节比对、
   cross-owner、Original、pending/rejected/stale artifact、preparation_hash mismatch、
   missing asset blocked。
4. **`backend/tests/integration/test_derivative_export_uat_contract.py`** — 10p backend
   UAT contract：两种格式字节 parity、refresh/reopen 重放同字节、cross-owner 物化
   fail-closed（B 空间零写入）、Original/未知项目 404-hide 无 403 oracle、pending/
   rejected/stale/forged-hash/missing 全部 blocked + artifact 保持 candidate、
   no original mutation + audit 事件。
5. **`frontend/src/app/writing/page.tsx`** — 挂载 ExportPanel。

## 独立测试验证（2026-08-05，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `npm test -- --run export` | ✅ 17 passed（export-panel 13p + export 4p） |
| `npm test`（前端全量） | ✅ 429 passed / 48 files |
| `npx playwright test e2e/derivative-export.spec.ts --list` | ✅ 36 tests 可解析 |
| `pytest tests/integration/test_derivative_export_uat_contract.py -q` ×2 | ✅ 10 passed / 10 passed |
| 回归（unit/derivative_export + 两个 integration） | ✅ 61 passed |
| `alembic heads` | ✅ 单 head `20260802_derivative_asset01`（无新 migration） |
| `from app.main import app` | ✅ OK |

## 关键契约验证

- **下载完成 ≠ 质量通过**：export-panel.tsx:302-318 双处 manifest 校验 fail closed；
  done 文案不含质量断言；面板头注 + vitest/e2e 断言 `not.toHaveTextContent("质量通过")`。
- **EPUB 未验证不标绿**：done 文案含 "不标绿"；vitest/e2e 断言通过。（观察项：done 段落
  用 emerald 绿色样式为既有外观，契约断言是文本层，不影响结论。）
- **浏览器不能绕过确定性边界**：导出仅走 agent/materialize，load 阶段只认 approve_export +
  approved 状态 + artifact status==="approved"；无 manifest 组装、无 live revision 选择。
- **三维 status + blocked reason**：AuditCard 渲染 verdict、三维 badge、每维 blocked_reasons。

## 备注 / 偏差

- 无新 migration。
- e2e 完整运行未执行：webServer（Next 16 canary dev）180s 超时是已知环境限制；
  `--list` 确认 36 tests 可解析，未伪造通过。
- EPUB 无外部 validator，明确标 unverified 不标绿（符合 PLAN 要求）。
