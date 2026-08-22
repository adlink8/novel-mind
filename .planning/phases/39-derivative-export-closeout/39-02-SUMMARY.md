# 39-02 SUMMARY — Provenance Package and Audit Contract

**Status:** COMPLETE | **Date:** 2026-08-05 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`backend/app/services/derivative_export/package.py`** — bounded provenance package
   builder：`package_id = derivative-export:{project_id}:{snapshot_hash}`（非猜测 artifact
   ID）；entry 名仅 allowlisted 固定值 + 内容哈希 + 固定索引派生，client 输入永不进入
   归档；路径 token（`/` `\` `..` NUL）fail-closed；`MAX_PACKAGE_ENTRIES=10_000`、
   `MAX_PACKAGE_ENTRY_BYTES=50MiB`、`MAX_PACKAGE_TOTAL_BYTES=100MiB`；`package_hash`
   覆盖 metadata + 全部 entries（自引用排除可复算）；跨 owner/Original/future/stale
   citation/missing asset 不生成成功包。
2. **`backend/app/services/derivative_export/audit.py`** — 三维 status audit contract：
   implementation_readiness / sample_data_coverage / quality_qualification 独立 status +
   blocked_reasons + evidence；quality 由真实状态推导（Phase 22 0/3 → blocked），
   falsely-green 构造被 validator 拒绝；blocked reason 由 `canonical_export_hash` 可复算；
   无 promotion/state 写能力（FORBIDDEN_AUDIT_WORDS 禁止 promote/active_pointer）。
3. **`backend/app/api/derivative_export.py`** — 新增 `POST .../export/package`
   （`X-Package-Manifest-Hash` + `X-Export-Manifest-Hash` 头）与 `GET .../export/audit`
   （owner-scoped，返回三维 status + report_hash 复算）。
4. **`backend/app/services/derivative_export/__init__.py`** — 导出 package/audit 符号。
5. **测试**：unit `test_package_manifest.py`（17p）+ adversarial `test_derivative_export_isolation.py`
   （+22p）+ integration `test_derivative_export.py`（+2p）。

## 独立测试验证（2026-08-05，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/integration/test_derivative_export.py tests/adversarial/test_derivative_export_isolation.py -q` | ✅ 59 passed |
| `pytest tests/adversarial/test_derivative_export_isolation.py tests/unit/derivative_export/test_package_manifest.py -q` ×2 | ✅ 67 passed / 67 passed（稳定） |
| `pytest tests/unit/derivative_export tests/integration/test_derivative_export.py -q`（39-01 回归） | ✅ 40 passed |
| `pytest tests/adversarial -q -p no:randomly`（全量） | ✅ 550 passed |
| `alembic heads` | ✅ 单 head `20260802_derivative_asset01`（无新 migration） |
| `from app.main import app` | ✅ OK |

## Fail-closed 边界（T-39-02-01/02/SC）

- **zip-slip / path traversal**：恶意 asset_id（`../`、`..%2f`、`a/b`、`a\b`、`a..b`、NUL）
  → `asset_path_denied`，证据保留。
- **archive size/entry bounds**：monkeypatch 边界 → `package_too_large` /
  `package_too_many_entries`。
- **IDOR（cross-owner / manifest owner）**：`revision_owner_mismatch` /
  `manifest_owner_mismatch`。
- **Original space / Original asset namespace**：`namespace_denied` /
  `asset_namespace_denied`。
- **future citation**（chapter/revision）：`citation_chapter_unknown` /
  `citation_revision_unknown`。
- **stale citation**：`citation_hash_mismatch` / `citation_source_snapshot_mismatch`。
- **rejected / missing asset**：`asset_not_approved` / `missing_asset_blocks_package` /
  `asset_bytes_missing`（reader 哈希重放失败阻断）。
- **hash mutation**：篡改 manifest/entries → package_hash 不重放。
- **falsely-green audit**：Phase 22 0/3 却构造 quality=verified → validator 拒绝。
- **blocked reason 复算**：报告 reason == `replay_quality_qualification_blocked_reason`。
- **stdlib-only**：zip writer 仅 zipfile/hashlib/json（AST 静态检查确认，无新第三方依赖）。

## 备注 / 偏差

- 无新 migration。
- `sample_data_coverage` 证据基于 runtime 可观察事实；browser UAT 执行证据在 39-03/04。
- Phase 22 保持 0/3 blocked；audit 如实输出 blocked 结论（D-39-04），未用 Phase 39 通过替代。
