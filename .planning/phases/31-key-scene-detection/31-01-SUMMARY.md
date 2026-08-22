# 31-01 SUMMARY — Key Scene Candidate Contract and Boundaries

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 31 override)

## What Was Built

1. **`backend/app/models/key_scene.py`** — 4 模型：`SceneCandidateSet`/`SceneCandidate`/
   `SceneEvidenceRange`/`SceneReviewDecision`（append-only 事件 + review_state projection）。
2. **`backend/app/schemas/key_scene.py`** — strict typed contract、reason-code 词表、review
   状态机、`SpeakerDialogueHeuristicSignal`、门函数。
3. **`backend/migrations/versions/20260801_key_scene.py`** — revision=`20260801_key_scene`、
   down_revision=`20260801_visual_bible`，单 head、upgrade/downgrade 可重放、`alembic check`
   零 drift。
4. **`backend/app/services/key_scenes/boundaries.py`** — 场景边界检测（复用既有 chunking
   scene/evidence 层级）；`verify_visual_bible_approval` 供 31-03 set freeze 调用。
5. **`backend/tests/unit/key_scenes/test_contracts.py`** — 39 项测试。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/key_scenes -q`（两次） | ✅ 39 passed / 39 passed |
| `alembic heads` | ✅ 单 head `20260801_key_scene` |
| `pytest tests/unit -q`（全量） | ✅ **771 passed** |
| `pytest tests/integration/visual_bible -q` | ✅ **22 passed**（回归） |
| `pytest tests/adversarial -q`（全量） | ✅ **239 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- 候选行（candidate/evidence range/review decision）append-only（`before_update`/
  `before_delete` 抛 ValueError）；set 行保留 review_state projection 可变；
- 逐候选 reject 通过 `key_scene_review_decisions.candidate_key` 记录，供 31-03 消费；
- speaker/dialogue heuristic signal 仅作候选召回/排序元数据（offsets、confidence、
  warnings），无 Canon/citation authority；
- evidence range、cast/place/time/POV、spoiler cutoff、diversity、reasons。

## 备注

- 既有测试陈旧（非本切片回归）：`test_candidate_authority_pg.py` 2 个用例断言 alembic
  current 含 `20260801_2801`（Phase 30 起已陈旧）；`test_qualification_command_pg.py` 3 个
  `.venv` 路径问题（记忆已记录）。
