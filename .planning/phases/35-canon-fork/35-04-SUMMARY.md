# 35-04 SUMMARY — Negative Contamination Tests

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`backend/app/services/canon_fork/contamination.py`** — 共享 derivative-write guard
   adapter + phase gate + audit recorder。
2. **`backend/app/services/facet/`**（新）、**`backend/app/services/knowledge_units/indexing.py`**、
   **`backend/app/services/eval_service.py`**（修改）— 三个写入入口接入 guard。
3. **`backend/app/models/canon_contamination.py`** — audit 表 ORM。
4. **`backend/migrations/versions/35_canon_contamination_guard04.py`** — revision=
   `20260801_canon_contamination04`、down_revision=`20260801_canon_fork01`，单 head、
   upgrade/downgrade 往返、`alembic check` clean；composite unique `(owner_id, novel_id,
   space, pipeline, attempt_hash)` + FK + check（space derivative-only / pipeline
   Original-only / blocked_reason 非空）。
5. **测试**：`test_canon_fork_phase_gate.py` 11 + 扩展 isolation 17 + contamination 25。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/adversarial/test_canon_space_isolation.py tests/adversarial/test_canon_contamination.py tests/integration/test_canon_fork_phase_gate.py -q`（两次） | ✅ 53 passed / 53 passed |
| `alembic heads` | ✅ 单 head `20260801_canon_contamination04` |
| `pytest tests/unit -q`（全量） | ✅ **1052 passed** |
| `pytest tests/adversarial -q`（全量） | ✅ **329 passed** |
| `pytest tests/integration/test_postgres_migrations.py -q` | ✅ **6 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- Derivative/Interpretation 永不进入 Original index/eval/facet；deliberate contamination
  抛 `ContaminationBlockedError(space_excluded)` 并回滚；
- 失败事务后 Original chapters 快照字节级不变（integration 前后对比）；
- phase gate 缺 preflight → blocked；verdict 仅 candidate/blocked；Phase 22
  ledger/STATE 未被触碰（read-only 测试断言）。

## 备注 / 偏差

- 文件路径映射：PLAN 引用的 `narrative_memory/indexing.py`、`quality/evaluation_corpus.py`
  在本 checkout 不存在——实际写入链映射为 `knowledge_units/indexing.py`（index）、
  `eval_service.py`（eval）、新建最小 `facet/producer.py`（facet 生产逻辑属 Phase 37-38）。
- `test_postgres_migrations.py`/`test_canon_space_schema.py` 的 stale head 断言已更新为新
  head。
