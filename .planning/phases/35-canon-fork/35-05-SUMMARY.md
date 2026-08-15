# 35-05 SUMMARY — create-canon-fork Skill 集成 + 确定性 fork 物化

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`agent-service/src/skills/create-canon-fork/`** — 版本化 branch-aware Skill 资产
   （8 工具 allowlist = 7 只读 + create_canon_fork action，budget/permissions/approval
   actions/schemas）。
2. **工具注册 16→17**：create_canon_fork action 工具（facade/registry/schema/API）。
3. **`backend/app/services/canon_fork/materializer.py`** — `create_fork_proposal` 候选 gate +
   `materialize_approved_fork` 确定性物化（approval 后原子创建 branch；Original Canon 不可变、
   active pointer 恒 false、物化幂等）。
4. **`backend/app/api/canon_fork.py`**（修改）— `POST /{novel_id}/canon-fork/{fork_id}/materialize`。
5. **backend wire + integrity**：CanonForkProposalArtifact/CanonForkProposalPayload/
   CanonDeltaPayload + `_evaluate_canon_fork_proposal` 门。
6. **测试**：`create-canon-fork.test.ts` 49 + `test_phase_35_skill.py` 17 +
   `test_canon_fork_materializer.py` 12。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `cd agent-service && npx vitest run` | ✅ **825 passed / 21 files** |
| `cd agent-service && npx tsc --noEmit` | ✅ exit 0 |
| `pytest tests/integration/test_canon_fork_materializer.py tests/integration/agent_runtime/test_phase_35_skill.py -q`（两次） | ✅ 29 passed / 29 passed |
| `pytest tests/unit -q`（全量） | ✅ **1052 passed** |
| `pytest tests/integration/agent_runtime -q` | ✅ **219 passed** |
| `alembic heads` | ✅ 单 head `20260801_canon_contamination04`（无新 migration） |
| `from app.main import app` | ✅ OK |

## 关键设计

- Agent 输出 candidate-only 直到确定性校验 + Web Approval 完成；
- Agent Service 不能直接写 Original Canon/domain tables/published state；FastAPI 拥有
  state，确定性 Fork materializer 拥有 approved branch creation；
- 伪造 approval/错误 branch/fork/stale revision/取消/schema drift/forbidden Tool →
  fail-closed 零权威写入；Original Canon 不可变、active pointer 恒 false、物化幂等。

## 备注 / 偏差

- 增量接线（facade/registry/loader/schemas/API 路由）按 34-05 既有模式补充。
- 计数类测试同步更新（registry/skills-loader/governance 16→17、10→11、GOVERNANCE_OK 17）。
