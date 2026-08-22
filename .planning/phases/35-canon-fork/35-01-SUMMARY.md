# 35-01 SUMMARY — Triple Knowledge Spaces Contract

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`backend/app/services/canon_fork/contracts.py`** — 三空间 closed 枚举、`CanonScope`/
   `CanonCitation`/`CanonWriteIntent`、`assert_original_readonly`/`assert_original_pipeline_input`、
   canonical scope hash。
2. **`backend/app/models/canon_space.py`**（修改）— 增加 `source_snapshot_hash`/
   `through_chapter`/`full_book_authorized`/`read_only` + 3 个 check constraint + append-only
   事件（仅 status 可变）+ lineage 索引。
3. **`backend/app/schemas/canon_space.py`** — CanonSpaceArtifactCreate（original 写入 schema
   不存在）/View/Query。
4. **`backend/migrations/versions/20260801_canon_space01.py`** — revision=`20260801_canon_space01`、
   down_revision=`20260801_illustration_anchors`，单 head、upgrade/downgrade 往返、`alembic
   check` 零 drift。
5. **测试**：`test_contracts.py` 23 + `test_canon_space_isolation.py` 9 +
   `test_canon_contamination.py` 18 + `test_canon_space_schema.py` 9。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/canon_fork tests/adversarial/test_canon_space_isolation.py tests/adversarial/test_canon_contamination.py -q` | ✅ **50 passed** |
| `pytest tests/unit/canon_fork/test_contracts.py tests/integration/test_canon_space_schema.py -q`（两次） | ✅ 32 passed / 32 passed |
| `alembic heads` | ✅ 单 head `20260801_canon_space01` |
| `pytest tests/unit -q`（全量） | ✅ **1025 passed** |
| `pytest tests/adversarial -q`（全量） | ✅ **278 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- 三空间只能以显式 authority/namespace/version/citation 规则存在；原作空间查询默认只读
  （无 original write schema；CanonWriteIntent 拒绝 original；DB read_only 标记双向绑定）；
- 严格 DTO → ORM composite scope → migration constraints 链；
- 无 derivative 数据进入 original evaluation/facet 生产（5 条 original pipeline 门禁 +
  contamination 负向测试 + DB 空间隔离）。

## 备注 / 偏差

- `test_canon_space_boundaries.py::test_retrieval_entry_points_reject_non_original_space_before_io`
  是既有过期签名失败（retrieval 入口 space 参数已移除）——contamination 测试改用
  `inspect.signature` 断言检索入口无 space 参数 + 契约门禁验证。
