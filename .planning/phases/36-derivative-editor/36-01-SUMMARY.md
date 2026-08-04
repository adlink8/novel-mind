# 36-01 SUMMARY — Derivative Project CRUD

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`backend/app/models/derivative_project.py`** — derivative project/domain 模型（lineage
   字段 before_update 拒绝改动）。
2. **`backend/app/schemas/derivative_project.py`** — strict DTO（extra="forbid"）。
3. **`backend/app/services/derivative_editor/projects.py`** — owner-scoped CRUD（无 fork_id →
   422；跨 owner/fork → 404；rejected/archived fork → 409 fork_not_usable）。
4. **`backend/app/api/derivative_projects.py`** — owner-scoped API。
5. **`backend/migrations/versions/36_derivative_project01.py`** — revision=
   `20260801_derivative_project01`、down_revision=`20260801_canon_contamination04`，单 head、
   upgrade/downgrade 往返、`alembic check` clean；3 FK + 唯一 + 7 CheckConstraint。
6. **测试**：`test_projects.py` 11 + `test_derivative_editor.py` 10 +
   `test_derivative_projects.py` 10 + `test_derivative_owner_isolation.py` 15。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/derivative_editor tests/integration/test_derivative_editor.py -q` | ✅ **21 passed** |
| `pytest tests/integration/test_derivative_projects.py tests/adversarial/test_derivative_owner_isolation.py -q`（两次） | ✅ 25 passed / 25 passed |
| `alembic heads` | ✅ 单 head `20260801_derivative_project01` |
| `pytest tests/unit -q`（全量） | ✅ **1063 passed** |
| `pytest tests/adversarial -q`（全量） | ✅ **344 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- project CRUD 严格 owner-scoped，创建时显式选择 Canon Fork；只写 Fanfiction namespace
  （DTO extra=forbid + service 门 + DB CheckConstraint 三层封死）；
- owner/novel/版本/cutoff lineage：project 行从所选 fork 拷贝冻结
  source_version_key/source_snapshot_hash/through_chapter/cutoff_snapshot_hash/scope_hash/
  manifest_hash；
- fork scope → project FK → owner-scoped API 链完整。

## 备注 / 偏差

- project 可绑定 candidate/approved fork（rejected/archived → 409）；集成测试直接以 API
  创建 candidate fork 绑定 project（未强制 approved，超出 36-01 范围）。
- stale-base/rollback 行为测试属 36-02/03（revision 机制）；36-01 冻结项目级 crash-before-ack
  持久性 fixture 供复用。
