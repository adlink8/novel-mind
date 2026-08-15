# 35-03 SUMMARY — Isolated Retrieval and Citations

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`backend/app/services/canon_fork/retrieval.py`** — scope-before-ranking 检索服务 +
   三空间 adapter（Original/Interpretation/Fanfiction 独立 namespace/index）+ `resolve_canon_scope`
   服务端 scope 派生。
2. **`backend/app/services/canon_fork/citations.py`** — citation 重验证链（owner → fork/version
   → cutoff → offset/hash 四层）+ per-cited-space provider + 纯函数 gate。
3. **`backend/app/api/canon_retrieval.py`** — `GET /{novel_id}/canon-retrieval` +
   `POST /{novel_id}/canon-retrieval/revalidate`。
4. **`backend/app/main.py`**（修改）— 注册 canon_retrieval_router。
5. **测试**：`test_retrieval.py` 26 + `test_canon_citation_boundaries.py` 18。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/canon_fork/test_retrieval.py tests/adversarial/test_canon_citation_boundaries.py -q`（两次） | ✅ 44 passed / 44 passed |
| `pytest tests/unit/canon_fork -q` | ✅ **50 passed** |
| `alembic heads` | ✅ 单 head `20260801_canon_fork01`（无新 migration） |
| `pytest tests/unit -q`（全量） | ✅ **1052 passed** |
| `pytest tests/adversarial -q`（全量） | ✅ **314 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- scope-before-ranking：先 cutoff predicate → snapshot replay predicate → 再排序；
  `CanonRetrievalService.retrieve` 先 validate_scope 再 load_scoped_candidates；
- 任何跨空间 candidate、未来内容或 stale hash 均无法进入结果（BLOCKED/ABSENT，非假空成功）；
- 结果带 authority、lineage（namespace/version/snapshot hash）和 evidence refs；
- 空维度 fallback 不是空成功：absent（干净空命名空间）与 blocked（行存在但全部不合格）显式区分。

## 备注 / 偏差

- 真实 bug 修复：`Chapter.content` deferred 列 async 访问触发 MissingGreenlet——Original
  adapter 与 leaf provider 加 `undefer(Chapter.content)`。
- 补充 citation 快照门：`ref.source_snapshot_hash` 必须从 resolved leaf 重放（冒烟发现
  stale ref 可通过），已在 revalidation_gate 增加 ref→resolved 快照比对。
- RetrievalBlockReason.UNSEALED/EMPTY_SOURCE 定义预留供后续 phase。
