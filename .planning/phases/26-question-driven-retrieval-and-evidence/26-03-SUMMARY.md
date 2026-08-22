# 26-03 SUMMARY — Source Lookup、EvidenceRef 物化与 Manifest 冻结

**Status:** COMPLETE | **Date:** 2026-08-02 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped)

## What Was Built

1. **`backend/app/services/queryplan/evidence.py`** — leaf EvidenceRef 物化与 Frozen Manifest：
   - `materialize_evidence_ref`：对冻结快照重切，校验快照 lineage/章节号/章节完整性/
     cutoff/偏移/slice hash，任一不匹配抛稳定 code（stale_snapshot_lineage /
     chapter_missing / chapter_number_mismatch / chapter_hash_mismatch / beyond_cutoff /
     invalid_offsets / stale_content_hash）；
   - `FrozenManifest`：frozen dataclass、内容寻址（manifest_id == checksum）、
     `verify_manifest` 检测 text/hash/offset/owner/version/cutoff/snapshot 漂移；
   - omitted/fallback 记录。
2. **`backend/app/services/queryplan/service.py`** — `QueryPlanService`：
   adapters → fusion → materialize → freeze → producer → `business_validate_answer`；
   `QueryPlanAnswer` 仅在 leaf-only gate 通过后创建；summary/score/routing/chat ref
   在 answer 创建前被拒。
3. **测试**：`test_manifest.py` 23 用例 + `test_queryplan_evidence.py` 16 用例。

## 验收

| 项 | 结果 |
|---|---|
| `cd backend && venv/Scripts/python.exe -m pytest tests/integration/queryplan/test_manifest.py tests/adversarial/test_queryplan_evidence.py -q` | ✅ **39 passed** |
| 回归（queryplan+adversarial+reader_chat unit） | ✅ **205 passed** |
| ruff | ✅ 全绿 |
| 前置 gate | ✅ 当前仓库仍 BLOCKED（Phase 22 0/3），符合 fail-closed 默认 |

## 设计决策

- **无 Alembic migration**：Manifest 由 plan+snapshot 确定性导出、checksum 内容寻址
  （与 reader_chat `freeze_manifest_from_stored` 的 checksum 重放语义一致），不需要新表。
- **determinism 语义**：同一 plan 对象 + 同一快照 → 相同 checksum；两次独立 parse
  （trace_id 不同）产生不同 manifest 是正确行为（每次 parse 是新 trace），重试按已存
  checksum 重用。
- answer_producer（模型调用）保持注入式，服务端本地 gate 是确定性边界。

## 备注

- 调用链可 AST 证明：`QueryPlanService.execute → freeze_manifest → business_validate_answer
  → validate_answer_against_manifest`，满足 key_link。
