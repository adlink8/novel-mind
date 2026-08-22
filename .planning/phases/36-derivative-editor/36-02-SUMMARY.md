# 36-02 SUMMARY — Chapter Planning and Markdown Editor

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`backend/app/models/derivative_chapter.py`** — ordered chapter plan 模型（position
   唯一/status=draft|archived/checksum 64hex/revision>0）。
2. **`backend/app/schemas/derivative_chapter.py`** — strict DTO（extra=forbid；patch 强制
   base_revision；scope 回显 fork/version/cutoff）。
3. **`backend/app/services/derivative_editor/chapters.py`** — scoped CRUD + canonicalize/
   checksum + 乐观并发 409 + 全量 reorder。
4. **`backend/app/api/derivative_chapters.py`** — 章节 API（create/list/get/patch/order/delete）。
5. **`backend/migrations/versions/36_derivative_chapter01.py`** — revision=
   `20260801_derivative_chapter01`、down_revision=`20260801_derivative_project01`，单 head、
   往返、`alembic check` clean。
6. **前端**：`derivative-api.ts`、`markdown-editor.tsx`（scope 头、dirty/saving/saved/error/
   conflict/blocked、autosave+手动保存、章节规划/排序/删除）、`writing/page.tsx`（宿主页）。
7. **测试**：`test_derivative_chapter_scope.py` 16 + `test_derivative_chapters.py` 13 +
   `markdown-editor.test.tsx` 7。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/adversarial/test_derivative_chapter_scope.py tests/integration/test_derivative_chapters.py -q`（两次） | ✅ 29 passed / 29 passed |
| `alembic heads` | ✅ 单 head `20260801_derivative_chapter01` |
| `pytest tests/unit -q`（全量） | ✅ **1063 passed** |
| `pytest tests/adversarial -q`（全量） | ✅ **360 passed** |
| `npx vitest run writing` | ✅ **8 passed** |
| `npx vitest run`（全量） | ✅ **393 passed / 45 files** |
| `from app.main import app` | ✅ OK |

## 关键设计

- 章节顺序稳定（position 唯一）、Markdown 可重放（canonicalize + checksum）、API 返回
  project/fork/revision scope、非法字段拒绝；
- wrong scope/authority 全部 fail-closed（跨 owner 404、stale base_revision 409、reorder
  冲突 409、extra_forbid 422）；
- 编辑器始终显示 project/fork/namespace/version/cutoff，禁止从阅读页面隐式选 fork；
- revision 仅在 canonical Markdown 实际变化时自增（title/status 改动不 bump，no-op 检测）。

## 备注 / 偏差

- 新增 migration（chapter 表需在 CI Postgres 存在才能跑集成测试）。
- 新增 `derivative-api.ts`（不在 PLAN files_modified，但为 planned analog——编辑器无 API
  client 无法加载/保存）。
- reorder 独立端点 `PUT .../chapters/order`（全量置换，缺失/重复/越权 409）。
- 36-03/04 的 immutable revisions/diff/rollback 仍按 D-36-02 延后；本步 revision 是章内
  乐观并发 token，不替代后续 revision 服务。
