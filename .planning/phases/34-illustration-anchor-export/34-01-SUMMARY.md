# 34-01 SUMMARY — Illustration Anchor Contract and Validation

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 34 override)

## What Was Built

1. **`backend/app/models/illustration_anchor.py`** — IllustrationAnchorProposal/
   IllustrationAnchor/AnchorRange；proposal append-only，anchor 仅 status 投影可变；
   `publish_shape`/`approval_shape`/offsets/paragraph fail-closed CHECK。
2. **`backend/app/schemas/illustration_anchor.py`** — AnchorStatus StrEnum、AnchorRange/
   AnchorCopy/AnchorPublishManifest 严格 frozen 契约、Proposal/Anchor contract、
   `validate_exact_source`/`validate_anchor_proposal_contract`/`validate_published_anchor`、
   hash 辅助。
3. **`backend/migrations/versions/20260801_illustration_anchors.py`** — revision=
   `20260801_illustration_anchors`、down_revision=`20260801_illustration_jobs`，单 head、
   upgrade/downgrade 往返、`alembic check` 零 drift。
4. **`backend/app/services/illustration_anchors/validation.py`** — `AnchorValidationService.
   validate_exact` 只产出 proposed/invalid，含 proposal_ready/rights/hash 门。
5. **测试**：`test_contracts.py` 37 项。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/illustration_anchors -q`（两次） | ✅ 37 passed / 37 passed |
| `alembic heads` | ✅ 单 head `20260801_illustration_anchors` |
| `pytest tests/unit -q`（全量） | ✅ **964 passed** |
| `pytest tests/integration/illustrations -q` | ✅ **30 passed**（回归） |
| `from app.main import app` | ✅ OK |

## 关键设计

- proposal 只接受 proposal_ready asset + 精确 source hash/range（错 hash 不得自动靠近定位）；
- valid anchor 必须绑定 approved action + published asset + publish manifest（DB
  `ck_illustration_anchors_publish_shape` 约束）；
- proposal 行仅 status/approval_request_id/published_* 等 6 个发布投影可迁移（镜像 Phase 33
  AssetRevision.approval_state 模式），其余不可变。

## 备注

- schema 字段名用 `presentation`（caption/alt_text/citation 嵌套 AnchorCopy），避免 pydantic
  `copy` 属性遮蔽警告。
- `published_asset_revision_id`/`publish_manifest_hash` 同时存在于 proposal（34-05 发布
  投影）与 illustration_anchors（读者/导出消费的稳定锚点）。
