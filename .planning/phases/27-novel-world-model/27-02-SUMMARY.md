# 27-02 SUMMARY — Character State, Goal, Motivation and Knowledge

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped)

## What Was Built

1. **`backend/app/services/world_model/knowledge.py`** — epistemic 契约与 gate：
   - `EpistemicClaim`/`KnowledgeCandidateProjection` 契约（subject、proposition、known_at、
     cutoff、POV/source、authority、lineage、transition）+ checksum；
   - `EpistemicGate`：D-01/D-05/D-06/transition 校验，Reader Chat/用户对话来源任何 authority
     一律拒绝（fail-closed）；
   - `build_knowledge_projection`。
2. **`backend/app/services/world_model/queries.py`** — 内存 cutoff/POV → disclosure/authority
   查询引擎，`EpistemicAnswer`（answered/abstained/candidate_only）。
3. **`backend/app/services/world_model/knowledge_repository.py`** — append-only、幂等、
   byte-equivalent replay、拒绝 stale-version/跨 owner。
4. **`backend/app/services/world_model/knowledge_queries.py`** — DB cutoff/POV 查询 API（只读）。
5. **`backend/app/models/world_model_knowledge.py`** — 单表 append-only `world_model_knowledge`。
6. **Migration `20260801_2702_world_knowledge_projection.py`**：revision=`20260801_2702`、
   down_revision=`20260801_2701`，单 head、upgrade/downgrade 可逆、旧行兼容。
7. **Fixtures**：`epistemic_v1.json`（9 场景）。
8. **测试**：`test_knowledge.py` 26 + `test_world_model_contamination.py` 14 +
   `test_knowledge_replay.py` 15。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/world_model tests/adversarial/test_world_model_contamination.py tests/integration/world_model -q` | ✅ **84 passed** |
| `pytest tests/integration/world_model/test_knowledge_replay.py -q`（两次） | ✅ 15 passed / 15 passed |
| `alembic heads` | ✅ 单 head `20260801_2702` |
| `pytest tests/unit/queryplan tests/integration/queryplan tests/adversarial -q` | ✅ **320 passed**（回归） |
| `pytest tests/unit -q`（全量） | ✅ **591 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- 人物历史按 cutoff/POV 演化且保留错误信念（mistaken belief）、未披露事实（hidden fact）
  与矛盾（contradiction）；状态 transition 不跳过无证据节点；
- D-06 从严：Reader Chat/用户对话来源任何 authority 一律拒绝（fail-closed）；
- contradiction 以单表显式 `epistemic_status=contradiction` 标签保留（可查询且简洁）。

## 备注 / 偏离

- Migration 放 `backend/migrations/versions/`（仓库实际目录，alembic.ini script_location），
  非 PLAN header 写的 `backend/alembic/versions/`。
- 独立验证实际收集 84 项（含 27-01 回归），本地自测估算 97 项——实际收集数为权威。
