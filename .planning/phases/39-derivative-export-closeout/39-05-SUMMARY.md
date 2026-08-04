# 39-05 SUMMARY — prepare-export Agent Integration with Deterministic Preparation/Materialization

**Status:** COMPLETE | **Date:** 2026-08-05 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`agent-service/src/skills/prepare-export/`** — versioned branch-aware skill 包
   （SKILL.md / skill.yaml / input.schema.json / output.schema.json / examples / tests）。
   read tools allowlist = 4 只读（get_novel/get_chapter/search_novel_text/get_narrative_memory）
   + 2 action（`approve_export` approval_required_for、`materialize_export`）；write_permissions 空。
2. **`agent-service/src/skills/loader.ts` / `src/tools/registry.ts`** — allowlist 14→15、
   DOMAIN_TOOL_NAMES 21→23；governance/registry/skills-loader 测试计数同步。
3. **`backend/app/services/derivative_export/preparation.py`** — 确定性 `prepare_export`：
   只读 approved/published derivative revisions/assets/citations/policy，owner/project/fork/
   version parity + membership + stale hash + security + reproducibility 门，冻结
   `ExportPreparationArtifact`（artifact/revision IDs、SkillRun/ToolRun IDs、source
   snapshot、base revision、input/content hashes、evidence refs、validator report、
   approval lineage、preparation_hash）。
4. **`backend/app/services/derivative_export/materializer.py`** — 确定性 `materialize_export`：
   只接受 `ExportPreparationArtifact(status=approved)` + action=approve_export 且
   status=approved 的 ApprovalRequest + preparation_hash/fork scope 匹配，经 39-01/02
   package/serializer 服务产出 bundle；download 只读不改 artifact status。
5. **`backend/app/schemas/agent_runtime.py`** — `ExportPreparationArtifact` wire 模型 +
   `structured_output_integrity.py` `_evaluate_export_preparation` 门禁分支。
6. **`backend/app/services/agent_tools/facade.py`** — `approve_export` + `materialize_export`
   action（TOOL_NAMES 21→23）+ schemas（extra=forbid）+ agent_tools.py 两路由。
7. **`backend/app/api/derivative_export.py`** — agent/prepare + agent/approve + agent/materialize
   三路由。
8. **测试**：unit `test_preparation.py`（13p）、integration `test_derivative_export_preparation.py`
   （8p）、agent_runtime `test_phase_39_skill.py`（23p）、skill vitest（46p）。

## 独立测试验证（2026-08-05，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `npx vitest run tests/skills/prepare-export.test.ts` ×2 | ✅ 46 passed / 46 passed |
| `npx tsc --noEmit` | ✅ exit 0 |
| `pytest test_preparation.py test_derivative_export_preparation.py test_phase_39_skill.py -q -o timeout=600` ×2 | ✅ 44 passed / 44 passed（13+8+23） |
| `pytest tests/adversarial/test_agent_tools_adversarial.py -q` | ✅ 89 passed |
| `pytest tests/contract/test_agent_tools.py -q` | ✅ 198 passed |
| `pytest tests/integration/agent_runtime -q -o timeout=600` | ✅ 308 passed（含 phase_38 22、phase_39 23） |
| `alembic heads` | ✅ 单 head `20260802_derivative_asset01`（无新 migration） |
| `from app.main import app` | ✅ OK |
| 额外回归（执行子代理） | agent-service 全量 vitest 1020p、skills 802p；backend unit 1258p、adversarial 550p |

## 边界证明（全部 blocked/cancelled/rejected 且零权威写入）

- **forged approval**（确认后篡改 payload_hash）→ `approval_hash_mismatch`，artifact 保持
  candidate，无 bundle。
- **stale preparation_hash** → `preparation_hash_mismatch`，0 ApprovalRequest。
- **wrong scope**（cross-owner artifact/novel）→ `artifact_not_found` / 404-hide。
- **pending / rejected / cancelled(expired) approval** → `approval_not_approved`，无 bundle。
- **cancellation**（run 取消）→ cancelled，0 artifact/revision/ApprovalRequest。
- **wrong fork scope / wrong action** → `fork_scope_mismatch` / `approval_not_found`。
- **schema drift**（status/review_state/source/evidence/branch）→ finalize blocked。
- **stale artifact revision** → `artifact_revision_stale`。
- **Original 写尝试** → Original 行不变，无越权 published/bundle。
- **download 只读** → 重复下载字节一致，Artifact status 不变。

## 备注 / 偏差

- 无新 migration（ExportPreparationArtifact 复用 agent_runtime 既有持久化模式）。
- e2e（Playwright）未执行（前端无新增，Next canary webServer 超时是既有环境限制）。
- `tests/contract/test_openapi_contract.py` 子进程超时为既有环境问题，非本次回归。
