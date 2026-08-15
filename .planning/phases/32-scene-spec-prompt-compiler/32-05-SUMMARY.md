# 32-05 SUMMARY — compile-scene-spec Skill 集成

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 32 override)

## What Was Built

1. **`agent-service/src/skills/compile-scene-spec/`** — 版本化 Skill 资产（version 1.0.0）：
   skill.yaml（allowlist：get_visual_bible/get_evidence_span、零写权限、`scene_spec:approve`
   审批动作、budget）、SKILL.md、input/output schema（oneOf 物化 SceneSpecArtifact +
   PromptArtifact）、fixtures。
2. **`agent-service/src/skills/loader.ts`**（修改）— `ALLOWLISTED_SKILL_DIRS` +compile-scene-spec
   （8 技能）。
3. **`backend/app/schemas/agent_runtime.py`**（修改）— 新增 `SceneSpecArtifact`、
   `PromptArtifact` wire 模型。
4. **`backend/app/services/agent_runtime/structured_output_integrity.py`**（修改）—
   `_evaluate_scene_spec`/`_evaluate_prompt` + 5 个 blocked 常量 + 接入 `evaluate_integrity`。
5. **测试**：`compile-scene-spec.test.ts` 58 + `test_phase_32_skill.py` 21。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `cd agent-service && npx vitest run` | ✅ **655 passed / 18 files** |
| `cd agent-service && npx tsc --noEmit` | ✅ exit 0 |
| `pytest tests/integration/agent_runtime/test_phase_32_skill.py -q`（两次） | ✅ 21 passed / 21 passed |
| `pytest tests/integration/agent_runtime tests/integration/scene_spec -q` | ✅ **180 passed** |
| `pytest tests/unit -q`（全量） | ✅ **875 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- 正向 SceneSpecArtifact/PromptArtifact candidate 链 + 19 个对抗用例全部 fail-closed
  零写入；`scene_spec:approve` 只授权 Phase 33 消费（`approved_only` 门）；
- Agent 不能铸造 ApprovalRequest；Canon/Visual Bible/key-scene 集/scene_spec 域表零变更；
- 消费 validated SceneCandidate 和 VisualBible 版本，无 unsupported Canon。

## 备注 / 偏差

- backend wire 模型 + integrity gate 为必要扩展（无此 evaluate_integrity 对 scene_spec/
  prompt 类型返回 BLOCKED_UNKNOWN_TYPE）。
- **hash 域桥接**：key-scenes 候选证据用 chunking `stable_hash` 域、get_evidence_span 用
  queryplan 纯 sha256 域；integrity gate 以 namespaced evidence key 前缀匹配信封 ref
  （`ev-x:action` ↔ `ev-x`）桥接。
