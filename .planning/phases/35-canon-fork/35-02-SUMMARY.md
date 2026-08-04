# 35-02 SUMMARY — Canon Fork Snapshot and Cutoff

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`backend/app/models/canon_fork.py`** — fork 模型（append-only、`active=false` DB CHECK）。
2. **`backend/app/services/canon_fork/snapshot.py`** — 服务端 cutoff 推导 + snapshot/hash 密封
   + manifest 确定性哈希 + candidate-only 持久化。
3. **`backend/app/services/canon_fork/lineage.py`** — cutoff-scoped 源叶子 citation lineage
   冻结/校验。
4. **`backend/app/api/canon_fork.py`** — POST/GET `/api/novels/{novel_id}/canon-fork`。
5. **`backend/migrations/versions/36_canon_fork01.py`** — revision=`20260801_canon_fork01`、
   down_revision=`20260801_canon_space01`，单 head、upgrade/downgrade 往返、`alembic check`
   clean。
6. **测试**：`test_canon_fork.py` 11 + `test_canon_fork_scope.py` 19。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/integration/test_canon_fork.py tests/adversarial/test_canon_fork_scope.py -q`（两次） | ✅ 30 passed / 30 passed |
| `pytest tests/integration/test_canon_fork.py -q` | ✅ **11 passed** |
| `alembic heads` | ✅ 单 head `20260801_canon_fork01` |
| `pytest tests/unit -q`（全量） | ✅ **1025 passed** |
| `pytest tests/adversarial -q`（全量） | ✅ **297 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- 同一输入得到相同 manifest hash（replayed=True、同 id、同 manifest_hash）；
- owner/novel 不匹配返回 404（与 missing-novel 形状一致）；
- full-book 需要显式授权且 scope 可审计（未授权→403 full_book_requires_authorization；
  superuser→201，authorization.source=server_superuser、granted_full_book=true）；
- 服务端从 owner/novel 与明确授权计算 cutoff，seal source snapshot/hash、原作 version 和
  citation lineage；禁止客户端传入可扩大范围的 cutoff（future cutoff→400、
  stale source hash→409）；
- 只创建 candidate fork，不创建 active pointer（DB CHECK active=false + AST 检查）。

## 备注 / 偏差

- 需要新 migration（canon_forks 表必须有）；另需显式 owner_id/novel_id 单列索引避免 drift。
- full-book 授权来源采用 `user.is_superuser` 作为显式服务端授权（无现成 grant 表），
  authorization 记录可审计；Phase 36+ 可在此 seam 扩展细粒度 grant。
