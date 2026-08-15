# 26-05 SUMMARY — answer-reading-question Skill 集成 Phase 26 确定性能力

**Status:** COMPLETE | **Date:** 2026-08-02 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped)

## What Was Built

1. **`agent-service/src/skills/answer-reading-question/`**（修改）— Phase 26 版本化只读
   Skill manifest：
   - `skill.yaml`：版本化只读 manifest（schemas、6 个已注册只读域工具 allowlist、budget、
     permissions；**无 ApprovalRequest 或 Publisher 动作**）；
   - `SKILL.md`：Phase 26 消费契约 + fail-closed 语义；
   - `input.schema.json`：增加 branch/selection/source_snapshot 可选字段；
   - `output.schema.json`：增加 normalization trail 形状声明；
   - examples/basic.json、tests/basic.json：fixture 补充 normalization trail。
2. **`agent-service/tests/skills/answer-reading-question.test.ts`**（修改）— 54 用例
   （原 29 → 新增 25 个 Phase 26 契约用例）：loader 接受 pinned manifest、拒绝未知工具/
   schema 不匹配/未声明权限。
3. **`backend/tests/integration/agent_runtime/test_phase_26_skill.py`**（新建）— 19 用例：
   端到端 Runtime→Tool→Artifact→Validator 证明，正/对抗用例均以 candidate 产物落库或
   零写入，无 ApprovalRequest/Publisher 路径。

## 独立测试验证（2026-08-02/03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `cd agent-service && npx vitest run` | ✅ **282 passed / 11 files** |
| `cd agent-service && npx tsc --noEmit` | ✅ exit 0 |
| `pytest tests/integration/agent_runtime/test_phase_26_skill.py -q`（串行两次） | ✅ 19 passed / 19 passed |
| `pytest tests/integration/agent_runtime -q` | ✅ **60 passed** |
| `pytest tests/unit/queryplan tests/integration/queryplan tests/adversarial -q` | ✅ **293 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- **CitedAnswerArtifact 是 Phase 26 唯一官方 Agent 输出**；所有正/对抗用例以 candidate
  产物落库或零写入。
- **工具 allowlist**：保留 6 个已注册只读工具（PLAN 列的 search_narrative_units 等 4 个
  不在 `facade.TOOL_NAMES` 注册集，loader 对未知工具 fail-closed）；Phase 26 确定性能力
  经 QueryPlan seam + 确定性 finalizer/validator 消费。`get_narrative_memory` 维持排除
  （既有 D-01 决议）。
- **Skill 版本保持 1.0.0**（钉住现有全部消费方）。
- **normalization trail**：output.schema 声明但不强制（同一 schema 被 26-06 共享 validator
  校验不含 trail 的 repaired payload）；权威 required 在 backend `CitedAnswerArtifact` wire
  模型（26-06 已改），集成测试证明 official Artifact 保留 trail 且服务器重放
  `repaired_hash` 一致。

## 备注 / 偏离

- PLAN 路径 `agent-service/skills/` 实际为 `agent-service/src/skills/`（按真实路径修改）。
- 额外修改 skill-local fixtures（携带 normalization 才 schema-valid，输出契约的必要后果）。
- ToolRun 表不存在：工具调用血缘由 SkillRun frozen_manifest + run 行承载，artifact/revision
  ID 由确定性 finalizer 写入时分配。
- 独立验证首跑 ERROR 为并行 pytest 共享测试库的 schema 竞态（环境问题），串行稳定。
