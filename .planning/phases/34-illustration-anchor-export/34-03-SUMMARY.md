# 34-03 SUMMARY — Anchor Repair after Text/Version Changes

**Status:** COMPLETE | **Date:** 2026-08-04 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 34 override)

## What Was Built

1. **`backend/app/services/illustration_anchors/repair.py`** — 纯 `classify_anchor_repair`
   （valid/needs_repair/invalid + 证据 diff）、`repair_proposal_key`、
   `AnchorRepairService.revalidate`（owner/novel scope + status 投影持久化）、
   `propose_anchor_repair`（候选 proposal + pending ApprovalRequest，payload 带 repair
   lineage）、`approve_anchor_repair`（复用 34-05 确定性 publish，旧 anchor 保留）。
2. **`backend/app/api/illustration_anchors.py`**（修改）— 3 个 owner-scoped 端点：
   `POST {anchor_id}/revalidate`、`POST {anchor_id}/repairs`、`POST repairs/{proposal_id}/approve`。
3. **测试**：`test_repair.py` 23 + `test_scope.py` 16。

## 独立测试验证（2026-08-04，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/illustration_anchors tests/integration/illustration_anchors -q` | ✅ **91 passed** |
| `pytest tests/unit/illustration_anchors/test_repair.py tests/integration/illustration_anchors/test_scope.py -q` | ✅ **39 passed** |
| `alembic heads` | ✅ 单 head `20260801_illustration_anchors`（无新 migration） |
| `pytest tests/unit -q`（全量） | ✅ **987 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- `revalidate` 持久化 anchor status 投影（模型唯一可变列），stale 显式呈现、span 永不
  重定位；
- `propose` 仅在 needs_repair 时接受调用方提供的新精确 span（AnchorValidationService 门禁
  proposal-ready + cleared 资产 + 精确 hash/range）；
- `approve` 先重校验旧 anchor 仍 stale，再走确定性 publish，旧 anchor 行完整保留为历史
  （可回滚、可审计）；
- repair proposal_key 由 `repair:{anchor_id}:{span_token}` 派生（同 span 幂等 replay）。

## 备注 / 偏差

- 复用既有 approval action（publish_illustration/attach_illustration_to_text），repair 语义
  由 payload 中 repair_anchor_id/repair_of_anchor_key/reason 标识。
- `test_scope.py` 与 visual_bible/test_scope.py 同名——单目录运行不受影响。
