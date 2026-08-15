# 37-05 SUMMARY — continue-derivative-story Skill 集成

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`agent-service/src/skills/continue-derivative-story/`** — 版本化 branch-aware Skill 资产
   （7 只读 + allow_divergence/publish_derivative_revision actions，budget/permissions/
   approval actions/schemas：DraftArtifact/ContinuityReport + disabled-by-default
   BranchSuggestion）。
2. **工具注册 18→20**：allow_divergence / publish_derivative_revision action 工具。
3. **`backend/app/services/derivative_generation/agent_boundary.py`** — Agent 边界：
   request_divergence_override / request_publish_derivative_revision /
   consume_publish_approval / draft_hash_for_candidate / revalidate_approved_divergence。
4. **backend wire + integrity**：DraftArtifact/ContinuityReportPayload/BranchSuggestionPayload +
   `_evaluate_derivative_draft` 门。
5. **测试**：`continue-derivative-story.test.ts` 52 + `test_phase_37_skill.py` 24。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `cd agent-service && npx vitest run` | ✅ **927 passed / 23 files** |
| `cd agent-service && npx tsc --noEmit` | ✅ exit 0 |
| `pytest tests/integration/agent_runtime/test_phase_37_skill.py -q`（两次） | ✅ 24 passed / 24 passed |
| `alembic heads` | ✅ 单 head `20260802_derivative_override01`（无新 migration） |
| `pytest tests/unit tests/adversarial -q`（全量） | ✅ **1622 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- divergence 严格按 allow_divergence approval → 完整 revalidation → 独立
  publish_derivative_revision approval → 确定性发布顺序；两个 approval 绑定完全相同
  draft_hash + canon_delta_hash；复用/漂移/跳过 → fail-closed；
- BranchSuggestion 六字段 + enabled_by_default=false，candidate-only、不自动 fork、不复用
  approval；
- Agent 输出 candidate-only 直到确定性校验 + Web Approval 完成；伪造 approval/错误
  branch/stale/取消/schema drift/forbidden tool → 零权威写入，Original 零变更。

## 备注 / 偏差

- 实现需后端 + agent-service 接线（两个新 action 工具、registry 18→20、loader allowlist、
  DraftArtifact wire 模型 + integrity gate）。
- Agent 边界代码放新模块 `agent_boundary.py` 而非 overrides.py（37-04 adversarial 静态断言
  overrides.py 不得出现 allow_divergence/publish_derivative_revision）。
- 顺带修复 contract test `_PARAMS_BY_TOOL` 对 4 个既有 action 工具的 KeyError 缺口。
