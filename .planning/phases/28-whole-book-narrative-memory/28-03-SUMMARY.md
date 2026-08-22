# 28-03 SUMMARY — Semantic Story Arc, Volume and Global

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 28 override)

## What Was Built

1. **`backend/app/services/narrative_memory/arc_planner.py`**（修改，+580 行）— EvidenceSummary/
   ChapterTerminalState/BoundaryUncertainty/GapRange/OverlapRange/ArcCandidateRange/
   OutlineCandidateArtifact + `plan_outline_arcs`（evidence-backed 边界算法 D-05）。
2. **`backend/app/services/narrative_memory/global_builder.py`**（修改，+373 行）—
   VolumeProjection/GlobalProjection/MainlineCandidateArtifact + `project_mainline_candidate`。
3. **`backend/app/services/narrative_memory/hierarchy.py`**（新建）— `build_hierarchy_candidate`
   仅消费终态章节，产出 chapter→arc→volume→global 连续层级。
4. **`backend/tests/integration/narrative_memory/test_hierarchy_coverage.py`**（新建）—
   gap/overlap/boundary 与 hierarchy lineage 测试。
5. **`backend/tests/adversarial/test_narrative_memory_safety.py`**（扩展）— 2 个 AST 扫描
   （新 3 模块无 pointer/promotion/cutover/chat/provider 路径）。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/integration/narrative_memory/test_hierarchy_coverage.py tests/adversarial/test_narrative_memory_safety.py -q` | ✅ **27 passed** |
| `pytest tests/integration/narrative_memory/test_hierarchy_coverage.py -q`（两次） | ✅ 16 passed / 16 passed |
| `alembic heads` | ✅ 单 head `20260801_2801`（无新 migration） |
| `pytest tests/unit/narrative_memory -q` | ✅ **189 passed** |
| `pytest tests/integration/narrative_memory -q` | ✅ **135 passed**（3 failed 为既有 `.venv` 路径环境问题） |
| `pytest tests/unit -q`（全量） | ✅ **650 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- Chapter State 收敛为连续 Arc/Volume/Global candidate ranges；gap/overlap/uncertain
  boundaries 显式保留（coverage_analysis、detect_arc_overlaps 可查询）；
- hierarchy 输出 immutable candidate-only（frozen Pydantic + 嵌套 checksum），每层带
  source_snapshot_hash/hierarchy_checksum/input_hash；
- Outline/Mainline candidates `candidate_status="candidate"` 硬编码，绝不因生成而入 Canon；
- blocked/isolated 章节只进 gaps，不伪装成完整事实。

## 备注 / 偏差

- 边界算法：evidence-backed scoring + window fallback（证据时按 claim 密度/不确定性/置信度
  差分割 ≥0.5 阈值，无证据时回退窗口；weak_evidence/adjacent_gap 标记 uncertain）。
- 未接入 `builder_worker.py`（不在 PLAN artifact 列表）；hierarchy 可从持久化 stage 终态行
  直接重建（DB 测试已验证与 frozen manifest 一致）。
- 测试子代理修复了既有 CI 库坏状态（`alembic upgrade head` 重建 128 表），非代码回归。
