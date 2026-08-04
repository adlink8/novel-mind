# 38-01 SUMMARY — Forked Visual Bible Schema and Lineage

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`backend/app/models/derivative_visual.py`** — 4 表：versions/entities/assets/review_events
   （versions 冻结 lineage guard 仅 review_state 可变更；entities/assets/review_events
   append-only）。
2. **`backend/app/schemas/derivative_visual.py`** — identity/style/reference/divergence DTO。
3. **`backend/app/services/derivative_visual/fork.py`** — 显式 fork + fail-closed 门
   （source_snapshot_hash/source_manifest_hash/cutoff 与源校验一致才可 fork）。
4. **`backend/app/services/derivative_visual/lineage.py`** — append-only review +
   owner-scoped read seams。
5. **`backend/migrations/versions/38_derivative_visual01.py`** — revision=
   `20260802_derivative_visual01`、down_revision=`20260802_derivative_override01`，单 head、
   往返、`alembic check` clean。
6. **测试**：`test_contracts.py` 23 + `test_visual_namespace_isolation.py` 15 +
   `test_derivative_visual.py` 12 + `test_derivative_visual_schema.py` 7。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/derivative_visual tests/adversarial/test_visual_namespace_isolation.py tests/integration/test_derivative_visual.py tests/integration/test_derivative_visual_schema.py -q`（两次） | ✅ 57 passed / 57 passed |
| `alembic heads` | ✅ 单 head `20260802_derivative_visual01` |
| `pytest tests/unit -q`（全量） | ✅ **1194 passed** |
| `pytest tests/adversarial -q`（全量） | ✅ **466 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- derivative Visual Bible 独立 namespace/version/owner/provenance；Original visual rows
  immutable（visual_namespace='fanfiction_visual' DB CheckConstraint 封印；
  source 复合 RESTRICT FK 使 Original snapshot 被引用时不可删除）；
- Original Visual Bible snapshot → explicit derivative fork → immutable derivative revision
  链；显式 divergence 声明为硬门（D-38-02）。

## 备注 / 偏差

- 新增 `schemas/derivative_visual.py`、包标记、models/__init__.py 注册（alembic check 必需）。
- 38-01 未加 API 路由（PLAN 仅 schema/lineage/services + 测试）。
- 既有失败 `test_derivative_editor_gate.py::test_no_publish_or_release_route_on_derivative_surface`
  因 37-05 的 publish_derivative_revision agent tool 路由未提交到该门禁（37-05 收尾时应更新）。
