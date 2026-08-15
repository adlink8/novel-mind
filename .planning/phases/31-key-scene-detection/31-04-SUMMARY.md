# 31-04 SUMMARY — detect-key-scenes Skill 集成

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 31 override)

## What Was Built

1. **`agent-service/src/skills/detect-key-scenes/`** — 版本化 Skill 资产（SKILL.md、
   skill.yaml、input/output schema、fixtures）。
2. **工具注册 12→13**：`get_visual_bible` 只读工具注册到 registry/facade/schema/API
   （PLAN allowlist 引用但此前未注册，为满足 loader fail-closed 校验而注册）。
3. **`backend/app/schemas/agent_runtime.py`**（修改）— 新增 `SceneCandidateArtifact` wire
   模型。
4. **`backend/app/services/agent_runtime/structured_output_integrity.py`**（修改）—
   `_evaluate_scene_candidate` integrity gate + blocked 码。
5. **测试**：`detect-key-scenes.test.ts` 54 + `test_phase_31_skill.py` 18。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `cd agent-service && npx vitest run` | ✅ **597 passed / 17 files** |
| `cd agent-service && npx tsc --noEmit` | ✅ exit 0 |
| `pytest tests/integration/agent_runtime/test_phase_31_skill.py -q`（两次） | ✅ 18 passed / 18 passed |
| `pytest tests/integration/agent_runtime -q` | ✅ **151 passed** |
| `pytest tests/unit -q`（全量） | ✅ **786 passed** |
| `pytest tests/integration/key_scenes tests/integration/visual_bible -q` | ✅ **48 passed**（回归） |
| `from app.main import app` | ✅ OK |

## 关键设计

- Phase 31 通过版本化 detect-key-scenes Skill 消费；
- 正向链：register→run→facade 工具→冻结 manifest→SceneCandidateArtifact→finalize→
  candidate+revision→publisher 持久化候选集→`key_scene:approve` review/freeze；
- 对抗路径稳定 blocked/cancelled 且零官方写入；
- 模型 proposal 与确定性 score/diversity/spoiler 校验及用户选择分离。

## 备注 / 偏差

- `get_visual_bible` 注册（registry/facade/schema/API + 既有测试同步 12→13）为必要偏差，
  否则 manifest 无法通过 loader fail-closed 校验。
- 无新增 migration：SceneCandidateArtifact 复用既有 artifacts/artifact_revisions 表。
- `test_openapi_contract.py` live-export subprocess 超时为既有环境问题。
