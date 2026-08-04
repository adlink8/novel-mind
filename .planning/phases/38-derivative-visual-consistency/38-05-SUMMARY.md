# 38-05 SUMMARY — illustrate-derivative-scene Agent Integration

**Status:** COMPLETE | **Date:** 2026-08-05 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`agent-service/src/skills/illustrate-derivative-scene/`** — versioned v1.0.0
   branch-aware skill 包（SKILL.md / skill.yaml / input.schema.json /
   output.schema.json / examples / tests）。read tools allowlist = 7 只读
   （get_novel/get_chapter/search_novel_text/get_timeline/get_relationships/
   get_clues/get_narrative_memory）+ action `publish_derivative_visual`
   （`approval_required_for` 含该 action）；write_permissions 空、forbidden
   空间显式声明；strict unknown fields 拒绝。
2. **`agent-service/src/skills/loader.ts` / `src/tools/registry.ts`** —
   loader allowlist 注册 + registry/governance 声明（GOVERNANCE_OK 21）。
3. **`backend/app/services/derivative_visual/agent_boundary.py`** —
   `request_publish_derivative_visual`（候选门 + 幂等重放，只创建 pending
   ApprovalRequest，绑定候选冻结血缘：asset_id/divergence/consistency/owner/
   novel/fork）与 `consume_publish_derivative_visual_approval`（唯一发布路径，
   委托 38-04 review seam）。Agent 永不直接写发布状态。
4. **`backend/app/services/agent_tools/facade.py`** — `publish_derivative_visual`
   action 工具（TOOL_NAMES 20→21）+ 参数 schema（extra=forbid）+ API 路由
   （`agent_tools.py:472`）。
5. **`backend/app/schemas/agent_runtime.py` + `structured_output_integrity.py`** —
   `BranchVisualBibleArtifact` / `BranchIllustrationRevision` wire 模型 + integrity
   gate 分支（status/review_state/branch/source hash fail-closed）。
6. **`backend/tests/integration/agent_runtime/test_phase_38_skill.py`** — 22 集成测试。
7. **`agent-service/tests/skills/illustrate-derivative-scene.test.ts`** — skill 契约测试。
8. **同步门禁**：`tests/adversarial/test_agent_tools_adversarial.py`（ACTION_TOOLS 补
   publish_derivative_visual）、`tests/contract/test_agent_tools.py`（_PARAMS_BY_TOOL）。

## 独立测试验证（2026-08-05，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `npx vitest run tests/skills/illustrate-derivative-scene.test.ts` ×2 | ✅ 47 passed / 47 passed |
| `npx tsc --noEmit` | ✅ exit 0 |
| `pytest tests/integration/agent_runtime/test_phase_38_skill.py -q -o timeout=600` ×2 | ✅ 22 passed / 22 passed |
| `pytest tests/adversarial/test_agent_tools_adversarial.py -q` | ✅ 89 passed |
| `pytest tests/contract/test_agent_tools.py -q` | ✅ 182 passed |
| `pytest tests/integration/agent_runtime -q -o timeout=600`（全目录） | ✅ 285 passed |
| `alembic heads` | ✅ 单 head `20260802_derivative_asset01`（无新 migration） |
| `from app.main import app` | ✅ OK |
| 补充 vitest（governance/registry/skills-loader） | ✅ 51 passed（GOVERNANCE_OK 21） |

## 边界证明（全部 blocked/cancelled/rejected 且零权威写入）

- forged approval（hash 不匹配）、expired/rejected/pending approval、wrong action
- wrong owner/branch/fork scope、candidate outside novel scope
- stale input hash / stale candidate revision（revision/hash 不重放）
- validator fail（identity drift → consistency fail → blocked，drifted source hash）
- cancellation（run/approval 取消无写入）
- schema drift / unknown tool/action
- Original authority 写尝试（Original 行不变 + 无越权 published）
- idempotent replay（同 event/payload 不重复扣写）

## 备注 / 偏差

- 无新 migration（38-05 复用 38-01..04 表结构）。
- Phase 22 门禁保持既有 0/3 未动。
- 与 37-05 `publish_derivative_revision` 同构：action 只创建 pending ApprovalRequest，
  确定性 seam 在批准后才发布。
