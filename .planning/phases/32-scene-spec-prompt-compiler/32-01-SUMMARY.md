# 32-01 SUMMARY — Scene Spec and Prompt Revision Contract

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 32 override)

## What Was Built

1. **`backend/app/schemas/scene_spec.py`** — SceneSpec/SceneDetail/NegativeConstraint/
   SceneUncertainty/PromptRevision/PromptArtifactLineage 严格契约（extra=forbid, frozen）+
   canonical hash helpers + fail-closed gates + review 状态机 + read/frozen envelopes。
2. **`backend/app/models/scene_spec.py`** — 5 表（scene_spec_versions/details/
   negative_constraints/evidence_refs/uncertainties），composite owner/novel/version FK、
   spoiler gate、append-only content rows。
3. **`backend/app/models/prompt_revision.py`** — prompt_revisions 表，`input_hash≠prompt_hash`
   的 DB CHECK、lineage 完整。
4. **`backend/migrations/versions/20260801_scene_spec_prompt.py`** — revision=
   `20260801_scene_spec_prompt`、down_revision=`20260801_key_scene`，单 head、
   upgrade/downgrade 可重放、`alembic check` 零 drift。
5. **`backend/tests/unit/scene_spec/test_contracts.py`** — 40 项测试。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/scene_spec -q`（两次） | ✅ 40 passed / 40 passed |
| `alembic heads` | ✅ 单 head `20260801_scene_spec_prompt` |
| `pytest tests/unit -q`（全量） | ✅ **826 passed** |
| `pytest tests/unit/visual_bible tests/unit/key_scenes -q` | ✅ **103 passed**（相邻回归） |
| `pytest tests/adversarial -q`（全量） | ✅ **245 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- provider 字段被拒绝（provider-neutral）、unsupported-future spoiler rejected 或
  unresolved、SceneSpec 唯一 canonical candidate、PromptRevision 派生 candidate；
- 确定性 lineage、character/location continuity、negative constraints、prompt version、
  无 unsupported detail 伪装成 canon。

## 备注 / 偏差

- 模型注册（`app/models/__init__.py`）为必要偏差——不注册则 `alembic check` 判 6 张新表
  drift、ORM 导出失败。
- 编译器 service 不在本切片（确定性 canonical render/compile helpers 放 schemas 模块内）；
  实际 compiler service/adapter 属 32-02/32-03。
- budget/rights-provenance 门本切片不适用（无 budget ledger 与 reference asset 字段；
  rights 由 Phase 30 覆盖，budget 归 job/budget ledger 切片）。
