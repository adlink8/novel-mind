# 28-02 SUMMARY — Chapter State Terminal Convergence

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 28 override)

## What Was Built

1. **`backend/app/services/narrative_memory/source_manifest.py`** — frozen source snapshot +
   DB 重算：冻结快照 checksum 与 DB 一致；持久化快照与 DB 分歧时逐章 BLOCKED
   （`source_snapshot_drift`）；正文变更被既有 V5 资格门整体 fail-closed。
2. **`builder_contracts.py`**（修改）— ChapterAnalysisArtifact（chapter_digest/chunk_digests/
   previous_context_summary/next_context_hint/continuity_notes，绑定 source/input hash、
   cutoff、max length、spoiler-policy lineage）+ digest 守卫 + RunPolicy.spoiler_policy_version。
3. **`builder_worker.py`**（修改）— start_run 冻结 manifest、process_run 逐章 drift
   fail-closed、artifact 写入 stage checkpoint、blocked→partial。
4. **Fixtures**：`long_book_v1.json`（12 章，含 partial/isolated 章节）。
5. **测试**：`test_chapter_terminality.py` 8 + `test_source_manifest.py` 8 +
   `test_builder_contracts.py`（+10）。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/integration/narrative_memory/test_chapter_terminality.py tests/unit/narrative_memory/test_source_manifest.py tests/unit/narrative_memory/test_builder_contracts.py -q` | ✅ **26 passed** |
| `pytest tests/integration/narrative_memory/test_chapter_terminality.py -q`（两次） | ✅ 8 passed / 8 passed |
| `alembic heads` | ✅ 单 head `20260801_2801`（无新 migration） |
| `pytest tests/unit/narrative_memory tests/adversarial/test_narrative_memory_safety.py -q` | ✅ **198 passed** |
| `pytest tests/integration/narrative_memory -q` | ✅ **119 passed**（3 failed 为既有 `.venv` 路径环境问题） |
| `pytest tests/unit -q`（全量） | ✅ **650 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- 12 章 long-book 重放：10 completed、2 isolated（`provider_transport_error`），零 silent
  pending；
- 章节 5 失败 → 仅重排队章节重跑（transport 调用 +1），sibling artifact 逐字节不变；
- digests 命名空间化 + 静态守卫 + DB 层断言（不在 `source_links.content_hash`、不在
  `text_chunks.content`）——永不作为 retrieval-index/EvidenceRef。

## 备注 / 偏差

- `builder_repository.py` 未修改（PLAN files_modified 列入）：既有
  `mark_stage/block_dependents/isolate_stage/recompute_terminal_states` 已提供终态机制，
  drift 阻断直接复用 `mark_stage(status="blocked_dependency", reason_code=SOURCE_DRIFT)`。
- 逐章 drift 阻断通过「持久化 manifest 与 DB 分歧」场景验证（直接改正文会被资格门先
  fail-closed）——两层防御均覆盖。
- `_finalize_run_status` 改进：drift-blocked 运行归类为 `partial`（原滞留 `running`）。
