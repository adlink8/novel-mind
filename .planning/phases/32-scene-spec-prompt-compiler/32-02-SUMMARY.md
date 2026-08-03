# 32-02 SUMMARY — Evidence-to-Spec Compiler

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 32 override)

## What Was Built

1. **`backend/app/services/scene_spec/compiler.py`** — 纯编译器 `compile_scene_spec` +
   `build_prompt_revision_from_spec` + owner-scoped `SceneSpecService`
   （preview/create/list/diff/stale + append-only 持久化）。
2. **`backend/app/api/scene_specs.py`** — `GET/POST /api/novels/{novel_id}/scene-specs`、
   `/preview`、`/{spec_id}`、`/{spec_id}/diff`。
3. **`backend/app/main.py`**（修改）— 注册 `scene_specs_router`。
4. **测试**：`test_compiler.py` 18 + `test_scope.py` 8。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/scene_spec tests/integration/scene_spec -q` | ✅ **66 passed** |
| `pytest tests/unit/scene_spec/test_compiler.py tests/integration/scene_spec/test_scope.py -q`（两次） | ✅ 26 passed / 26 passed |
| `alembic heads` | ✅ 单 head `20260801_scene_spec_prompt`（无新 migration） |
| `pytest tests/unit -q`（全量） | ✅ **844 passed** |
| `pytest tests/unit/key_scenes tests/unit/visual_bible tests/integration/key_scenes tests/integration/visual_bible -q` | ✅ **151 passed**（相邻回归） |
| `from app.main import app` | ✅ OK |

## 关键设计

- 同输入同 hash、VB ID/连续性保持、unsupported canon/spoiler/冲突不放行；
- owner scope、VB 变更后 stale、candidate-only、preview 无 provider、evidence 回跳。

## 备注 / 偏差

- **快照哈希域分离**：key_scenes 与 visual_bible 用不同算法的 `compute_source_snapshot_hash`
  （不同 kind 前缀），SceneSpec 的 evidence lineage 绑定 key-scenes 域；Visual Bible 派生
  clause 只携带 `VisualBibleRef`（revision hash）——避免跨域契约校验失败。不违反 must-have
  （每条 clause 仍可回跳证据或 VB revision）。
- diff 语义：VB revision 变更后 spec 标记 stale；diff 用最新已批准 revision 重编译。
- `test_postgres_migrations.py` 3 项失败为既有过期 head pin（`EXPECTED_HEAD=20260801_2801`），
  非本次改动。
