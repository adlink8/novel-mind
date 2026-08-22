# 27-04 SUMMARY — POV, Disclosure and Epistemic Authority

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped)

## What Was Built

1. **`backend/app/services/world_model/authority.py`** — 四 label authority envelope
   （`canon_fact`/`probable_inference`/`literary_interpretation`/`user_interpretation`）+
   conversion gate + disclosure timing + override 隔离。
2. **`backend/app/services/world_model/queries.py`**（修改）— `query_world_projection`
   （cutoff/POV/authority 过滤、candidate/override 分离）+ `world_projection_reader`。
3. **`backend/app/services/queryplan/`**（Phase 26 扩展）：
   - `contracts.py`（新）：共享 `WorldProjectionItem`/`WorldProjectionView` + `leaf_evidence_key`
     （四 label 校验、leaf-only key 校验）；
   - `adapters.py`：`WORLD_PROJECTION` 维度（unavailable/abstained/candidate_only/available）；
   - `evidence.py`：`freeze_world_projection_manifest`；
   - `schemas.py`：新增 `WORLD_PROJECTION` 维度；`service.py`：`ConsumerQueryPlanView.world_projection`。
4. **`frontend/src/components/analysis/world-model-evidence-panel.tsx`** — authority badge/
   disclosure/evidence jump/override/candidate-only 展示（+ 7 个 vitest）。
5. **`frontend/e2e/world-model-epistemic.spec.ts`** — 浏览器证明（12 cases，desktop/mobile/
   tablet，环境限制无法执行断言）。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/integration/world_model tests/adversarial/test_world_model_authority.py tests/adversarial/test_world_model_contamination.py -q` | ✅ **107 passed** |
| `pytest tests/unit/queryplan tests/integration/queryplan -q` | ✅ **164 passed**（回归） |
| `pytest tests/unit/world_model tests/integration/world_model -q` | ✅ **128 passed** |
| `alembic heads` | ✅ 单 head `20260801_2703`（27-04 无新 migration） |
| `cd frontend && npm run test -q` | ✅ **289 passed / 37 files** |
| `from app.main import app` | ✅ OK |

## 关键设计

- authority label 不丢失；查询/序列化禁止把 inference/interpretation 变成 fact；
- user interpretation 与 original candidate projection 隔离；无 active-pointer cutover；
- 缺失 projection 明确 `unavailable`，出现后 `available`，不 empty-success；
- 未授权转换、未来 fact、非叶子引用 fail-closed。

## 备注 / 偏差

- `CHARACTER_STATE`/`WORLD_RULES` 存量维度未改接 world reader（既有测试断言 reader_id=None）；
  parity 走新增 `WORLD_PROJECTION` 聚合维度，避免回归。
- candidate-only/override 的 omitted 记录：主 pipeline 在 dimension 粒度记录 partial，
  claim 粒度由 `freeze_world_projection_manifest` 提供并有测试覆盖。
- e2e 仅 `--list` 解析通过；运行被 Next canary webServer 超时阻断（pre-existing，与 26-04
  同因）。Panel 行为由 vitest + backend contract 覆盖。
