# 30-01 SUMMARY — Visual Bible Candidate Artifact 契约

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 30 override)

## What Was Built

1. **`backend/app/models/visual_bible.py`** — 6 模型：`VisualBibleVersion`/`VisualEntity`/
   `VisualClaim`/`VisualEvidenceRef`/`VisualReferenceAsset`/`VisualBibleReviewEvent`
   （append-only + 不可变监听）。
2. **`backend/app/schemas/visual_bible.py`** — strict/frozen 契约 + canonical hash +
   server-side gate。
3. **`backend/migrations/versions/20260801_visual_bible.py`** — revision=`20260801_visual_bible`、
   down_revision=`20260801_2801`，单 head、upgrade/downgrade 往返对称、`alembic check` 零 drift。
4. **`backend/tests/unit/visual_bible/test_contracts.py`** — 34 项契约/版本/证据/权限/review
   门/ORM metadata/migration 链测试。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/visual_bible -q`（两次） | ✅ 34 passed / 34 passed |
| `alembic heads` | ✅ 单 head `20260801_visual_bible` |
| `pytest tests/unit -q`（全量） | ✅ **717 passed** |
| `pytest tests/adversarial -q`（全量） | ✅ **239 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- 契约覆盖：拒绝 extra 字段、越界 offset、缺证据 `canon_fact`、重复 stable ID、错误
  content/manifest hash、越 cutoff 证据；
- candidate 版本含 parent/schema/policy/manifest/source-snapshot lineage；四类 authority
  标签闭合；interpretation 不折叠 authority；review 动作 append-only/幂等；
- canon vs interpretation 标签；source/cutoff lineage；reusable IDs；无 generated asset
  静默成为 canon。

## 备注 / 偏差

- 额外增加 `VisualBibleReviewEvent` 表（第 6 张）满足 must_haves review append-only/幂等
  可验证——review 事件落在契约切片内，30-04 只需在其上建 service/API。
- `validate_evidence_against_source` 在 30-01 为纯函数；真实章节内容切片重验由 30-02/
  30-04 service 落地；前端 review workspace 属 30-04/30-05。
