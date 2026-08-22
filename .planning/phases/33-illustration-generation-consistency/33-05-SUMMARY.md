# 33-05 SUMMARY — illustrate-scene Skill 集成

**Status:** COMPLETE | **Date:** 2026-08-03/04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 33 override)

## What Was Built

1. **`agent-service/src/skills/illustrate-scene/`** — 版本化 branch-aware Skill 资产
   （8 工具 allowlist 含 `generate_image_candidate` action、零写权限、budget、无
   ApprovalRequest/Publisher）。
2. **工具注册 13→14**：`generate_image_candidate` 注册到 registry/facade/schema/API
   （候选作业创建，不自动 dispatch）。
3. **`backend/app/schemas/agent_runtime.py`**（修改）— `IllustrationRevisionPayload` +
   `IllustrationRevisionArtifact` wire 模型。
4. **`backend/app/services/agent_runtime/structured_output_integrity.py`**（修改）—
   `_evaluate_illustration_revision` 分支。
5. **测试**：`illustrate-scene.test.ts` 59 + `test_phase_33_skill.py` 16。

## 独立测试验证（2026-08-03/04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `cd agent-service && npx vitest run` | ✅ **714 passed / 19 files** |
| `cd agent-service && npx tsc --noEmit` | ✅ exit 0 |
| `pytest tests/integration/agent_runtime/test_phase_33_skill.py -q`（两次） | ✅ 16 passed / 16 passed |
| `pytest tests/integration/agent_runtime -q` | ✅ **188 passed** |
| `pytest tests/integration/illustrations -q` | ✅ **30 passed** |
| `pytest tests/unit -q`（全量） | ✅ **927 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- 终态严格 candidate → validated → proposal_ready；Phase 33 永不创建 ApprovalRequest/调用
  publisher/发 published 状态（每个对抗用例断言）；
- Phase 34 只读 handoff 只接受 proposal_ready（`build_proposal_ref` 对非 proposal_ready
  抛 ValidationError）；
- 每次 run 绑定 owner、novel、authority_space、可选 branch/fork、SkillVersion、SkillRun、
  ToolRuns、source/input hashes、model/runtime lineage、Artifact revision；
- 伪造 approval、错误 branch/fork、stale revision、取消、schema drift、forbidden action →
  fail-closed 无权威写入。

## 备注 / 偏差

- 新增 `generate_image_candidate` 工具注册 + `IllustrationRevisionArtifact` wire 模型 +
  integrity gate 分支为必要扩展（不注册则 loader allowlist/finalize fail-closed）。
- Phase 33 确定性 validator 以测试内 helper 组合真实 gate（`evaluate_illustration_proposal_gate`）
  + 真实域 review 服务实现，未新增生产服务文件。
- `generate_image_candidate` 创建候选作业后不自动 dispatch（测试显式调
  `run_illustration_worker`）；生产后台调度属独立关注点。
