# 29-05 SUMMARY — evaluate-reading-skill-runs Skill 集成

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 29 override)

## What Was Built

1. **`agent-service/src/skills/evaluate-reading-skill-runs/`** — 版本化 Skill 资产：
   - `skill.yaml`（1.0.0）：4 工具 allowlist、零写权限、零审批、budget 80/60000/12000/$5.00；
   - `SKILL.md`、`input.schema.json`/`output.schema.json`（SkillEvaluationArtifact 信封）、
     fixtures。
2. **`agent-service/src/skills/loader.ts`**（修改）— `ALLOWLISTED_SKILL_DIRS` 增为 5。
3. **`backend/app/schemas/agent_runtime.py`**（修改）— 新增 `SkillEvaluationArtifact` +
   `EvaluatedSkillRunLineage`/`EvaluatedArtifactLineage`。
4. **`backend/app/services/agent_runtime/structured_output_integrity.py`**（修改）—
   `skill_evaluation` 分支：对 `QualificationReport` 重放 two-value verdict + checksum +
   source snapshot 绑定。
5. **测试**：`evaluate-reading-skill-runs.test.ts` 58 + `test_phase_29_skill.py` 19。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `cd agent-service && npx vitest run` | ✅ **491 passed / 15 files** |
| `cd agent-service && npx tsc --noEmit` | ✅ exit 0 |
| `pytest tests/integration/agent_runtime/test_phase_29_skill.py -q`（两次） | ✅ 19 passed / 19 passed |
| `pytest tests/integration/agent_runtime -q` | ✅ **115 passed** |
| `pytest tests/unit/qualification tests/contract/test_agent_tools.py -q` | ✅ **143 passed** |
| `pytest tests/unit -q`（全量） | ✅ **683 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- Phase 29 通过版本化 evaluate-reading-skill-runs Skill 消费；
- 评估冻结 SkillRun/ToolRun/Artifact/Manifest/model/source/dataset lineage，绝不重跑可变
  Agent 状态；
- 官方输出为 SkillEvaluationArtifact；无 ApprovalRequest/Publisher/promotion
  （`_count_approvals==0` + `status=="candidate"`）；
- blocked 是合法官方裁决（D-05 two-value）。

## 备注 / 偏差

- 未新增 domain 工具：`test_agent_tools.py` 断言 TOOL_NAMES 恰为 12，新增会破坏契约测试；
  skill 的 4 工具 allowlist 取既有只读工具子集，冻结记录经运行输入 refs+hashes 绑定。
- backend wire 模型 + integrity gate 分支为必要扩展（无此 finalize 对 `skill_evaluation`
  返回 `BLOCKED_UNKNOWN_TYPE`）。
