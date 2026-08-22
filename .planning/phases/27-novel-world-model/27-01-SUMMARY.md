# 27-01 SUMMARY — Shared Event Fact and Causal Edge

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, recorded in `.planning/STATE.md` + `config.json`)

## What Was Built

1. **`backend/app/services/world_model/`** — 事件事实与因果边契约：
   - `contracts.py`：append-only event fact 与 caused/triggered/responded/blocked 因果边契约
     （owner/novel/version/cutoff、source EvidenceRefs、effective interval、authority、
     confidence、gate status）；
   - `claims.py`：证据门控因果声明校验；
   - `gates.py`：evidence-gated 因果 gate（`co_occurrence_only` 拒绝无证据因果）；
   - `event_repository.py`：append-only durable repository（拒绝 update/wrong-owner/
     stale-version）；
   - `event_queries.py`：owner/version/cutoff 查询 API，返回完整 lineage、EvidenceRefs、
     authority、gate 与冲突。
2. **`backend/app/models/world_model_event.py`** — 3 张 immutable durable 投影表。
3. **Migration `20260801_2701_world_event_projection.py`**：revision=`20260801_2701`、
   down_revision=`20260801_2601`（当前 head），单 head、upgrade/downgrade 可逆。
4. **Fixtures**：`backend/tests/fixtures/world_model/events_v1.json`（8 场景语料）。
5. **测试**：`test_gates.py` 17 + `test_world_model_authority.py` 13 +
   `test_event_replay.py` 12。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/world_model tests/adversarial/test_world_model_authority.py tests/integration/world_model -q` | ✅ **42 passed** |
| `pytest tests/integration/world_model/test_event_replay.py -q`（两次） | ✅ 12 passed / 12 passed |
| `alembic heads` | ✅ 单 head `20260801_2701` |
| `pytest tests/unit/queryplan tests/integration/queryplan tests/adversarial -q` | ✅ **306 passed**（回归） |
| `pytest tests/unit -q`（全量） | ✅ **565 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- 因果必须有独立证据 + gate，不能由 co-occurrence 或时间相邻自动升级；
- 冲突保留而非覆盖；projection 永不被覆盖（append-only）；
- authority 不会隐式变成 canon_fact（D-01/D-04）；
- 无 active-pointer/promotion 写入（D-02）。

## 备注 / 偏离

- Task 0 先跑 `check_phase_execution_gate.py`：当前仓库非零 blocked（Phase 22 0/3），
  按用户 2026-08-03 override 继续。
- **budget gate 未实现**：PLAN action 提及但验收/must-haves 不含 budget，world_model
  边界无预算机制，判定属 worker/qualification 层职责，延后。
- `alembic check` 报"Target database is not up to date"（共享测试库落后 head）——环境
  状态非实现缺陷；迁移链线性无分支。
