# 39-01 SUMMARY — Derivative-Only Reproducible Markdown/EPUB Export

**Status:** COMPLETE | **Date:** 2026-08-05 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`backend/app/services/derivative_export/snapshot.py`** — `ExportSnapshot`（frozen
   pydantic）+ `seal_export_snapshot` + `FrozenDerivativeExport`（asset_reader 哈希校验
   seam）+ `ExportSnapshotService.build`（owner/project/fork-scoped，fail-closed）。
   消费 37-04 `PublishedDerivativeRevision` 与 38-03/04 `PublishedDerivativeVisualAsset`；
   `validate_published_revision` / `validate_published_asset` / `validate_asset_membership`
   逐字段 parity（owner/project/fork/version/snapshot/approval/review/citation/source
   refs/asset_hashes membership），任一缺失或不一致抛 `ExportSnapshotError`。
2. **`backend/app/services/derivative_export/manifest.py`** — 冻结 DTO（chapter/revision/
   asset/citation/missing）+ `canonical_export_hash` + `derivative_export_manifest_hash` +
   `seal_derivative_export_manifest`（强制 `manifest_hash == snapshot.snapshot_hash`）。
3. **`backend/app/services/derivative_export/markdown.py`** — 确定性 Markdown serializer，
   只消费同一 frozen snapshot。
4. **`backend/app/services/derivative_export/epub.py`** — stdlib-only 确定性 EPUB3
   （zipfile + 手工 XHTML/OPF/ncx，固定时间戳、mimetype 首条 STORED、allowlisted entries、
   bounded sizes、内容哈希寻址资产文件名、缺失/哈希漂移字节降级为显式 placeholder）。
5. **`backend/app/api/derivative_export.py`** — `POST .../export/prepare` +
   `GET .../download?format=markdown|epub`（`require_owned_novel`、响应带
   `X-Export-Manifest-Hash` / `X-Export-Snapshot-Hash` 头、storage seam）。
6. **`backend/app/main.py`** — 注册 `derivative_export_router`（prefix `/api/novels`）。
7. **Wave0 fixtures**：unit `test_serializers.py`（14p）、integration `test_derivative_export.py`
   （7p）+ `test_derivative_export_audit_fixture.py`（3p）、adversarial
   `test_derivative_export_isolation.py`（28p）、security `test_derivative_export_snapshot.py`
   （7p）、`tests/fixtures/derivative_export_roundtrip_fixtures.py`。

## 独立测试验证（2026-08-05，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/derivative_export tests/integration/test_derivative_export.py -q` ×2 | ✅ 21 passed / 21 passed（稳定） |
| `pytest tests/adversarial/test_derivative_export_isolation.py -q` | ✅ 28 passed |
| `pytest tests/integration/test_derivative_export_audit_fixture.py -q` | ✅ 3 passed |
| `pytest tests/security/test_derivative_export_snapshot.py -q` | ✅ 7 passed |
| 39-01 全量（5 文件） | ✅ 59 passed |
| `alembic heads` | ✅ 单 head `20260802_derivative_asset01`（无新 migration） |
| `from app.main import app` | ✅ OK |
| 上游 38 回归（visual_namespace_isolation + derivative_visual） | ✅ 50 passed |

## Fail-closed 边界（T-39-01-01/02）

- **跨 owner/project/fork**：逐字段 parity + `require_owned_novel` 404；项目不存在 404。
- **Original/future scope**：`space != fanfiction_canon` / `asset_namespace_denied` /
  `source_snapshot_mismatch` blocked；archived 项目拒绝。
- **stale revision**：`revision.version_id != chapter_version_id` →
  `revision_version_stale`（含 SQL bump revision 集成测试）。
- **stale citation hash**：`canonical_citation_hash(keys)` 重放校验。
- **asset_hashes membership**：revision 引用 hash 必须 ∈ published asset 集合。
- **rejected/unapproved asset**：`asset_not_approved`；rejected 候选不出现。
- **missing bytes**：`MissingDerivativeAssetRecord` 显式 + serializer 双保险 sha256，
  绝不 invent URL / 静默 drop。
- **路径/traversal**：asset_id 含 `/`、`\`、`..`、`\x00` → `asset_path_denied`；
  zip entry 只由 content_hash+索引派生。
- **EPUB 加固**：stdlib-only（AST 校验）、固定时间戳、allowlisted prefixes、
  `epub_too_large` / `chapter_too_large`。
- **不可重现/不可对齐**：snapshot/manifest 单一 hash replay 校验。

## 备注 / 偏差

- 无新 migration（只用上游表）。
- frontend/e2e UAT 属 39-03/04 范畴，本 plan 仅 backend。
- EPUB 互操作性未经外部 validator（Calibre）验证，按 39-RESEARCH 记录为 unverified。
- Phase 22 0/3 保持独立风险（D-39-04），未触碰。
