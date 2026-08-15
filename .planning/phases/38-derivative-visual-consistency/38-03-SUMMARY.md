# 38-03 SUMMARY — Derivative Asset Storage and Cross-Chapter Consistency

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 35-39 override)

## What Was Built

1. **`backend/app/services/derivative_visual/assets.py`** — candidate 存储（generated IDs、
   allowlisted storage root、content checksum、完整 lineage）+ store gate + review。
2. **`backend/app/services/derivative_visual/consistency.py`** — 跨章节 identity/style
   consistency 评分（三章 score + reasons 进 review lineage）。
3. **`backend/app/services/derivative_visual/published_assets.py`** — 已发布资产查询
   （只返回已发布且 owner/project/fork 可见；Original 或未批准 asset blocked）。
4. **`backend/app/schemas/derivative_visual_asset.py`** — PublishedDerivativeVisualAsset 全套
   DTO + SSRF 门。
5. **`backend/app/api/derivative_visual_assets.py`** — 资产 API（store/list/load/bytes/
   consistency/review）。
6. **`backend/migrations/versions/38_derivative_asset01.py`** — revision=
   `20260802_derivative_asset01`、单 head、往返、`alembic check` clean（新增
   DerivativeVisualCandidateAsset + ReviewEvent 表）。
7. **测试**：`test_visual_asset_security.py` 22 + `test_derivative_visual.py` 扩展 11。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/integration/test_derivative_visual.py tests/adversarial/test_visual_asset_security.py -q`（两次） | ✅ 45 passed / 45 passed |
| `alembic heads` | ✅ 单 head `20260802_derivative_asset01` |
| `pytest tests/unit tests/adversarial -q`（全量） | ✅ **1709 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- 生成只写隔离 candidate asset（永不自动 publish）；consistency verdict 只驱动
  review_state（fail→blocked / concern|unavailable→needs_review / pass→candidate）；
- 重复 job 幂等（同 asset_key + 同 content → replay；不同 content → duplicate conflict）；
- 路径不可逃逸（version 级 containment root，../ 穿越 fail-closed）+ MIME/size allowlist；
- checksum/source mismatch blocked；score 缺输入明确 unavailable（<2 章或证据缺失）；
- 威胁清单全 fail-closed：wrong path/namespace、SSRF metadata（DTO URL 扫描）、asset IDOR、
  identity drift、style divergence（declared→concern/undeclared→blocked）、duplicate、
  missing asset、original overwrite（Original 行从未被写）。

## 备注 / 偏差

- 新增 models + migration（DTO 字段需持久化），符合「38-03 可能有新 migration」。
- 一致性证据/report 持久化在 candidate 行（JSONB）且不可变；新章节加入仅新 candidate 重算。
- style divergence 判定把 identity 行 divergence 中 style/palette/color 键视为已声明
  （D-38-02 语义），如需更细粒度可后续调整。
