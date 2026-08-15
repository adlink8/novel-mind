# 27-05 SUMMARY — propose-world-model-candidates Skill 集成

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped)

## What Was Built

1. **`agent-service/src/skills/propose-world-model-candidates/`** — 版本化 Skill 资产：
   - `skill.yaml`：版本化 manifest（schemas、6 只读域工具 allowlist、budget、permissions、
     approval actions）；
   - `SKILL.md`：Phase 27 消费契约 + fail-closed 语义；
   - `input.schema.json`/`output.schema.json`（WorldModelCandidateArtifact 信封）；
   - examples/tests fixtures。
2. **工具注册扩展 7→12**：`get_events`/`get_character_state`/`get_character_knowledge`/
   `get_world_rules`/`get_evidence_span` 注册到 agent-service `DOMAIN_TOOL_NAMES` 与 backend
   `facade.TOOL_NAMES`/API/schemas，接线 world_model 查询服务（只读、cutoff/POV 服务端强制）。
3. **`agent-service/tests/skills/propose-world-model-candidates.test.ts`** — 49 用例：
   loader 接受 pinned manifest、拒绝未知工具/schema drift/未声明权限/缺失血缘。
4. **`backend/tests/integration/agent_runtime/test_phase_27_skill.py`** — 16 用例：端到端
   Runtime→Tool→Artifact→Validator/Approval 边界，正向链 + 10 个对抗 fail-closed。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `cd agent-service && npx vitest run` | ✅ **331 passed / 12 files** |
| `cd agent-service && npx tsc --noEmit` | ✅ exit 0 |
| `pytest tests/integration/agent_runtime/test_phase_27_skill.py -q`（两次） | ✅ 16 passed / 16 passed |
| `pytest tests/integration/agent_runtime -q` | ✅ **76 passed** |
| `pytest tests/contract/test_agent_tools.py tests/adversarial/test_agent_tools_adversarial.py tests/adversarial/test_external_evidence_adversarial.py -q` | ✅ **196 passed** |
| `pytest tests/unit/world_model tests/integration/world_model -q` | ✅ **128 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- `WorldModelCandidateArtifact` 是 Phase 27 唯一官方 Agent 输出；确定性 WorldModel
  Validator/Gate 发布 typed projections；Agent 禁止直接发布 Canon fact；
- 每次 run 绑定 owner/novel/branch、SkillVersion、SkillRun、ToolRuns、input hash、source
  snapshot、model/runtime lineage、output Artifact revision；
- Validator/ApprovalRequest/Publisher 角色不委托给 prompt（全部后端确定性）；
- `get_world_rules` 按 `disclosure_cutoff` 服务端截止点过滤（防剧透，与 Phase 26 一致）。

## 备注 / 偏差

- **必要范围扩展**：5 个新工具此前不在 25.2-02 注册表（facade 仅 7 工具），为满足 pinned
  manifest 可加载/可注册/可端到端消费，注册到 agent-service + backend（只读、cutoff/POV
  服务端强制）——超出 PLAN files_modified，但缺它验收无法达成。
- 全量 backend 套件的 8 失败/6 错误为既有环境问题（Chroma 未启动、NarrativeSearch API
  drift、retrieval_policy 身份断言、openapi 超时），均不 import 本次改动模块。
- 测试子代理启动了 CI 服务（`docker compose -f docker-compose.ci.yml up -d`）——Postgres:5433
  与 Chroma:8001 仍在运行，后续测试可复用。
