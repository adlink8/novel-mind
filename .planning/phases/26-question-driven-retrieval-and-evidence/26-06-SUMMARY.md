# 26-06 SUMMARY — Structured Output Integrity

**Status:** COMPLETE | **Date:** 2026-08-02 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped)

## What Was Built

1. **`agent-service/src/structured-output/normalizer.ts`** — 共享保守 normalizer：
   - 仅允许声明式 alias、enum canonicalization、无歧义 container-shape 修复；
   - 任意模糊匹配/默认值/类型强转/未知字段丢弃/嵌套猜测全部禁止（blocked 语义冻结）；
   - immutable raw capture + raw_hash/repaired_hash + actions/warnings。
2. **`agent-service/src/structured-output/validator.ts`** — 严格 post-repair schema/lineage
   validator + fail-closed blocked；`repaired_hash` 漂移（payload 被改）→ blocked；
   导出 `assertValidStructuredOutput` 供 26-05 消费。
3. **`backend/app/services/agent_runtime/structured_output_integrity.py`** — FastAPI 严格
   schema/lineage 适配器 `evaluate_integrity`，唯一 finalizer 写入前调用。
4. **`backend/app/services/agent_runtime/finalize.py`**（修改）— 预算检查后、引证校验与
   `create_artifact_with_first_revision` 之前调用 fail-closed integrity gate（blocked →
   `failed_validation`、零写入）。
5. **`backend/app/schemas/agent_runtime.py`**（修改）— 新增 `NormalizationTrail` wire 模型；
   `CitedAnswerArtifact.normalization` 成为必需字段。
6. **测试**：`agent-service/tests/structured-output-integrity.test.ts`（34 用例，含跨语言
   hash 基准）+ `backend/tests/integration/agent_runtime/test_structured_output_integrity.py`
   （17 用例）。

## 独立测试验证（2026-08-02，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `cd agent-service && npx vitest run` | ✅ **257 passed / 11 files** |
| `cd agent-service && npx tsc --noEmit` | ✅ exit 0 |
| `cd backend && venv/Scripts/python.exe -m pytest tests/integration/agent_runtime -q` | ✅ **41 passed** |
| `pytest .../test_structured_output_integrity.py -q`（两次） | ✅ 17 passed / 17 passed（稳定） |
| `pytest tests/adversarial tests/unit/queryplan tests/integration/queryplan -q` | ✅ **293 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- **零受保护字段合成**：`evidence_refs/owner/owner_id/novel_id/cutoff/authority/branch/
  fork/approval` 纳入 `PROTECTED_FIELDS`；normalizer 修复路径不得触及（contract-invalid），
  服务端 `extra=forbid` + 显式检查阻断。
- **heuristic candidate-only**：无 evidence_refs 的 cited_answer → 稳定
  `BLOCKED_NO_EVIDENCE`，不进 cited-answer 网关。
- **external evidence**：`prohibited_from_canon` 常量检查前置，`evaluate_integrity` 单测覆盖。

## 备注 / 偏离

- 额外修改 `test_skill_runtime.py`（不在 PLAN files_modified）：`CitedAnswerArtifact.
  normalization` 设为必需后，既有 stub 需携带 trail 保持全绿——新 wire 契约的自然结果。
- `evaluate_integrity` 门顺序：no-evidence 检查置于 schema 校验之前，heuristic 结果获
  稳定 `BLOCKED_NO_EVIDENCE` 原因；行为仍 fail-closed。
- 未修改 `reader_chat.py`：evidence_refs 白名单仍由既有 `validate_answer_against_manifest`
  （finalize 内）承担。
