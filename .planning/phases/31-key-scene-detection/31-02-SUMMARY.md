# 31-02 SUMMARY — Narrative Salience and Diversity Ranking

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 31 override)

## What Was Built

1. **`backend/app/services/key_scenes/scoring.py`** — `KeySceneScorer.score`（plot turn/
   emotional peak/quiet-emotional/visual/character salience/coverage/dialogue/arc 多信号
   确定性打分，`policy_hash` 版本化）、`compute_diversity_key`/`rank_with_diversity`
   （overlap repetition 惩罚 + diversity quota）；embedding 仅 capped bonus ≤0.05。
2. **`backend/app/services/key_scenes/candidates.py`** — `CandidateService.generate`
   （load snapshot → detect boundaries → cutoff → score → diversity → 严格
   `SceneCandidateSetContract` → 服务端门 → append-only 幂等持久化）、owner-scoped
   `list_sets`/`load_set_view`。
3. **`backend/app/api/key_scenes.py`** — `GET /api/novels/{id}/key-scenes`（列表）、
   `POST .../generate`（生成）、`GET .../key-scenes/{set_id}`（候选集 envelope）；
   owner-scoped、candidate-only、spoiler-safe。
4. **`backend/app/main.py`**（修改）— 注册 `key_scenes_router`。
5. **测试**：`test_scoring.py` 15 + `test_candidates.py` 11。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/key_scenes tests/integration/key_scenes -q` | ✅ **65 passed** |
| `pytest tests/unit/key_scenes/test_scoring.py tests/integration/key_scenes/test_candidates.py -q`（两次） | ✅ 26 passed / 26 passed |
| `alembic heads` | ✅ 单 head `20260801_key_scene`（无新 migration） |
| `pytest tests/unit -q`（全量） | ✅ **786 passed** |
| `pytest tests/integration/visual_bible -q` | ✅ **22 passed**（回归） |
| `from app.main import app` | ✅ OK |

## 关键设计

- 多信号、确定性、可解释打分；不把重要性简化为 embedding 相似度（embedding ≤0.05
  capped bonus）；
- diversity quota + overlap repetition 惩罚；
- owner/novel/source snapshot/spoiler cutoff/candidate-only 门在 API 全部生效；
- speaker/dialogue heuristic 仅辅助召回/排序，不进入 evidence/citation。

## 备注 / 偏差

- coverage 无专属 reason code（closed 词汇表无 coverage 码），计入 score_total 与
  breakdown，但不产生 SalienceReason。
- API 无 review 端点（属 31-03/31-04）；生成时已含 approved Visual Bible revision 的
  owner/approved/hash 重新验证门。
- 修复真实 bug：generate 幂等 replay 路径 `session.rollback()` 过期 ORM 对象触发
  MissingGreenlet——端点固化 owner_id/novel_id 为 int 再调用。
