# 38-02 SUMMARY — Derivative Scene Specs

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`backend/app/services/derivative_visual/scene_spec.py`** — compiler + owner-scoped
   service（将 approved VisualBible revision、key-scene、SceneSpec、IllustrationRevision
   lineage 与 IllustrationAnchor/export manifest 引用编译为 canonical derivative Scene Spec）。
2. **`backend/app/services/derivative_visual/gates.py`** — 8 个 deterministic gates
   （namespace_denied/source_snapshot_hash_mismatch/implicit_canon_detail/
   derivative_asset_approved/asset_source_ref_missing/asset_lineage_spec_mismatch 等）。
3. **`backend/app/api/derivative_visual.py`** — versions + scene-specs compile/read 路由。
4. **`backend/app/schemas/derivative_visual.py`**（修改）— DerivativeSceneSpecContract +
   row contracts + hash helpers（content_hash replay validator）。
5. **测试**：`test_scene_spec.py` 20 + `test_visual_namespace_isolation.py` 扩展 7。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/derivative_visual tests/adversarial/test_visual_namespace_isolation.py -q`（两次） | ✅ 65 passed / 65 passed |
| `pytest tests/integration/test_derivative_visual.py tests/integration/test_derivative_visual_schema.py -q` | ✅ **19 passed** |
| `alembic heads` | ✅ 单 head `20260802_derivative_visual01`（无新 migration） |
| `pytest tests/unit -q`（全量） | ✅ **1214 passed** |
| `pytest tests/adversarial -q`（全量） | ✅ **473 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- 相同 snapshot 生成相同 spec hash（deterministic + divergence 敏感）；所有引用可重验
  （content_hash replay + scene_spec/visual_bible/snapshot/manifest hash 逐项 gate）；
- unsupported detail 和 mixed authority 被 blocked；provider 只收到 canonical spec
  （test_spec_is_provider_only_no_orm_leakage——无 ORM row/文件路径泄漏）；
- wrong namespace/source hash mismatch/隐含 canon 细节拒绝；
- 上游契约缺失则 blocked（DerivativeSceneSpecBlockedError，API 409）。

## 备注 / 偏差

- 新增 schemas/derivative_visual.py 与 main.py 修改（严格 wire contract + 路由注册，必要
  支撑）。
- DerivativeSceneSpecContract 带 content_hash replay validator——构造时须先 model_construct
  计算 hash 再 model_validate（占位 hash 会触发 validator 失败），供 38-03 参考。
