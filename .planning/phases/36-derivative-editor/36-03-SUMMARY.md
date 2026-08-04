# 36-03 SUMMARY — Autosave, History, Diff and Rollback

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`backend/app/models/derivative_revision.py`** — immutable revision 行（parent_revision_id/
   content_checksum/content/kind/actor_id/reason/approval_state + `before_update` 不可变门）。
2. **`backend/app/services/derivative_editor/revisions.py`** — autosave CAS（原子条件更新
   `UPDATE ... WHERE revision = base_revision`）/ 幂等重放 / diff_markdown（canonical）/
   history / rollback（新 child + reason/approval）。
3. **`backend/app/api/derivative_revisions.py`** — `POST autosave`、`GET revisions`、
   `GET revisions/{id}`、`GET diff`、`POST rollback`。
4. **`backend/app/services/derivative_editor/chapters.py`**（修改）— create 时种 root
   revision 行；markdown patch 变更时追加 revision 行（保持 lineage 完整）。
5. **`backend/migrations/versions/36_derivative_revision01.py`** — revision=
   `20260801_derivative_revision01`、down_revision=`20260801_derivative_chapter01`，单 head、
   往返、`alembic check` clean。
6. **测试**：`test_derivative_revision_history.py` 19 + `test_derivative_revision_concurrency.py` 26。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/integration/test_derivative_revision_history.py tests/adversarial/test_derivative_revision_concurrency.py -q`（两次） | ✅ 45 passed / 45 passed |
| `alembic heads` | ✅ 单 head `20260801_derivative_revision01` |
| `pytest tests/unit tests/adversarial -q`（全量） | ✅ **1449 passed** |
| `pytest tests/integration/test_derivative_editor.py test_derivative_projects.py test_derivative_chapters.py test_derivative_revision_history.py -q` | ✅ **52 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- autosave 不丢草稿（崩溃重试幂等 + commit-before-ack）；并发写不 last-write-wins
  （原子 CAS）；冲突返回 409 + 最新 revision（结构化 detail 含 current_revision 完整视图）；
- rollback 可追溯且不覆盖历史（只创建新 child 行）；diff 基于 canonical Markdown
  （CRLF/尾部空白规范化）；
- 相同内容重放 → noop，不新增行。

## 备注 / 偏差

- chapters.py 扩展（种 root 行 + 变更追加行）为满足完整版本 lineage 的必要改动；既有
  36-02 断言保持通过。
- 409 冲突响应形状为结构化 detail（code/message/current_revision_number/checksum/revision），
  不同于 36-01/02 的字符串约定。
- rollback 增加 base_revision CAS（防并发客户端提交旧 base 覆盖）。
- 三个集成测试文件的 head 断言更新至 `20260801_derivative_revision01`。
- diff 用 revision **id** 定位（base_revision_id/target_revision_id），前端需注意与
  revision_number 的映射。
