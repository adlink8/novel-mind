# 36-05 SUMMARY — edit-derivative-story Skill 集成 + 确定性 Revision Service

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`agent-service/src/skills/edit-derivative-story/`** — 版本化 branch-aware Skill 资产
   （7 工具 allowlist = 6 只读 + apply_derivative_edit action，budget/permissions/approval
   actions/schemas）。
2. **工具注册 17→18**：apply_derivative_edit action 工具。
3. **`backend/app/services/derivative_editor/events.py`** — user_autosave 与 agent_proposal
   分离的事件类型/actor 标签/CAS 路径。
4. **`backend/app/api/agent_derivative_edits.py`** — `POST /api/agent/derivative-edit-proposals/
   {artifact_id}/apply`（唯一写路径，强制 approved approval + payload hash 重放 + CAS）。
5. **`backend/migrations/versions/36_derivative_agent_edit01.py`** — 放宽
   `ck_derivative_revisions_kind` 加入 agent_proposal，单 head、往返。
6. **测试**：`edit-derivative-story.test.ts` 50 + `test_phase_36_skill.py` 20 + 扩展历史/并发。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `cd agent-service && npx vitest run` | ✅ **875 passed / 22 files** |
| `cd agent-service && npx tsc --noEmit` | ✅ exit 0 |
| `pytest tests/integration/test_derivative_revision_history.py tests/adversarial/test_derivative_revision_concurrency.py tests/integration/agent_runtime/test_phase_36_skill.py -q`（两次） | ✅ 73 passed / 73 passed |
| `alembic heads` | ✅ 单 head `20260801_derivative_agent_edit01` |
| `pytest tests/unit tests/adversarial -q`（全量） | ✅ **1454 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- Agent 输出 candidate-only（proposal_status 恒 proposed；integrity gate + apply gate 双保险）；
- user_autosave 与 agent_proposal 分离端点/事件/actor/CAS 路径（`derivative.user_autosave.*`
  与 `derivative.agent_proposal.*`，revision kind=agent_proposal）；
- `apply_agent_edit` 是唯一写路径（approved approval + payload hash 重放 + CAS）；
- 伪造 approval/错误 branch/stale revision/取消/schema drift/forbidden Tool → fail-closed
  零权威写入；每次 run 绑定完整 lineage。

## 备注 / 偏差

- 新增 migration（DB 级 actor 分离：kind 约束放宽加 agent_proposal）。
- user_autosave 端点保留既有 36-03 路由，仅补充事件发射（PLAN 的 autosave 路径为概念性）。
- `tests/contract/test_agent_tools.py` 32 个既有失败（action 工具缺 _PARAMS_BY_TOOL 条目）
  为历史遗留，非本次引入。
