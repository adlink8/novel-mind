# 30-02 SUMMARY — Visual Bible Evidence Materialization and Owner-Scoped API

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 30 override)

## What Was Built

1. **`backend/app/services/visual_bible/evidence.py`** — `VisualBibleEvidenceService.
   materialize_version_claims`：重读 owner 小说 Chapter 正文，校验 source snapshot hash/
   chapter 范围/spoiler cutoff/offsets/slice content hash；失败返回 reason-coded unresolved
   （`stale_snapshot_lineage`、`evidence_content_mismatch`、`evidence_lineage_mismatch`、
   `chapter_missing`、`chapter_number_mismatch`、`beyond_cutoff`、`claim_hash_mismatch`、
   `owner_scope_mismatch`）。
2. **`backend/app/services/visual_bible/authority.py`** — `VisualBibleAuthorityService`：
   `create_revision`（strict contract gate、immutable candidate 持久化、idempotent replay +
   IntegrityError 并发回放）+ `apply_review`（append-only 幂等 review events）+ owner-scoped
   读视图 `list_versions`/`load_version_view`。
3. **`backend/app/api/visual_bible.py`** — 4 路由：`GET/POST /{novel_id}/visual-bible`、
   `GET /{novel_id}/visual-bible/{version_id}`、`POST /{novel_id}/visual-bible/{version_id}/review`。
4. **`backend/app/main.py`**（修改）— 注册 `visual_bible_router`（prefix `/api/novels`）。
5. **测试**：`test_scope.py` 15 用例。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/visual_bible tests/integration/visual_bible -q` | ✅ **49 passed** |
| `pytest tests/integration/visual_bible/test_scope.py -q`（两次） | ✅ 15 passed / 15 passed |
| `alembic heads` | ✅ 单 head `20260801_visual_bible`（无新 migration） |
| `pytest tests/unit -q`（全量） | ✅ **717 passed** |
| `pytest tests/adversarial -q`（全量） | ✅ **239 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- 跨 owner/version、错 hash、spoiler cutoff、无证据 canon claim 全部 fail-closed（404/409/
  422 reason-code）；
- source 不被改写（`test_approval_never_touches_asset_or_source_chapter`）；
- 重复创建不产生隐性 active pointer（idempotent replay 同一 id，list total=1）；
- Phase 30 不调用 image provider（无 provider 调用）。

## 备注 / 偏差

- `main.py` 注册 router（PLAN 清单未列但 API 需注册才可达）；
- 409 结构化冲突体用 `JSONResponse` 返回 `{kind, unresolved[]}` 而非 `{"detail":...}`；
- `apply_review` 将幂等 event_key 检查置于 from_review_state 校验之前（重试幂等回放）。
