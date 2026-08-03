# 28-05 SUMMARY — Agent integration (analyze-chapter / build-story-arc)

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 28 override)

## What Was Built

1. **`agent-service/src/skills/analyze-chapter/`** — 版本化 Skill 资产（SKILL.md、skill.yaml、
   input/output schema、fixtures）。
2. **`agent-service/src/skills/build-story-arc/`** — 版本化 Skill 资产。
3. **`agent-service/src/skills/loader.ts`**（修改）— `ALLOWLISTED_SKILL_DIRS` 加入两个新技能。
4. **`backend/app/schemas/agent_runtime.py`**（修改）— 新增 `ChapterAnalysisArtifact`/
   `StoryArcArtifact` wire 模型（strict extra=forbid）。
5. **`backend/app/services/agent_runtime/structured_output_integrity.py`**（修改）—
   `evaluate_integrity` 增加 `chapter_analysis`/`story_arc` 分支：共享 lineage/status/trail
   门 + 领域 DTO 校验、digest 绝不 EvidenceRef/检索索引（`assert_digests_never_evidence_refs`）、
   future-fact next hint 阻断（`hint_safe_at_cutoff`）、Outline/Mainline candidate-only。
6. **测试**：`analyze-chapter.test.ts` 53 + `build-story-arc.test.ts` 49 +
   `test_phase_28_skill.py` 20。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `cd agent-service && npx vitest run` | ✅ **433 passed / 14 files** |
| `cd agent-service && npx tsc --noEmit` | ✅ exit 0 |
| `pytest tests/integration/agent_runtime/test_phase_28_skill.py -q`（两次） | ✅ 20 passed / 20 passed |
| `pytest tests/integration/agent_runtime -q` | ✅ **96 passed** |
| `pytest tests/unit/narrative_memory tests/integration/narrative_memory/test_closure.py tests/integration/narrative_memory/test_manifest_parity.py -q` | ✅ **211 passed**（回归） |
| `pytest tests/unit -q`（全量） | ✅ **650 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- Phase 28 通过版本化 analyze-chapter / build-story-arc Skills 消费；
- ChapterAnalysisArtifact/StoryArcArtifact 只以 candidate-only terminal-state 校验后落库；
- digest 永不作为 EvidenceRef/检索索引；future-fact next hint 在 cutoff 处阻断；
- 取消/未知工具/schema drift/错误 owner/approval bypass → fail-closed。

## 备注 / 偏差

- 工具名 `get_clue_chain` → `get_clues`（注册集实际名，照写会 fail-closed）。
- backend wire 模型 + integrity gate 为必要扩展（无此 finalize 会对新类型
  `BLOCKED_UNKNOWN_TYPE`，happy-path 无法落库）。
- skill 目录按实际资产结构放 `agent-service/src/skills/analyze-chapter/` 与
  `build-story-arc/`（PLAN 写 `narrative-memory/`）。
