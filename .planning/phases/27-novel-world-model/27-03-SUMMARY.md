# 27-03 SUMMARY — World Entity, Rule, Faction, Place and Item

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped)

## What Was Built

1. **`backend/app/services/world_model/entities.py`** — typed entity/faction/place/item
   契约：membership、ownership、spatial/item state、aliases、source lineage；别名相似度只
   产生 review candidate，不静默合并。
2. **`backend/app/services/world_model/rules.py`** — rule/exception 契约：规则例外为一等
   记录。
3. **`backend/app/services/world_model/provenance.py`** — lineage 保留。
4. **`backend/app/services/world_model/entity_repository.py`** — append-only、拒绝历史
   update/wrong-owner/静默 merge。
5. **`backend/app/services/world_model/entity_queries.py`** — versioned 查询 API（保留 alias
   review、exception、EvidenceRef、authority、candidate status）。
6. **`backend/app/models/world_model_entity.py`** — immutable durable 投影。
7. **Migration `20260801_2703_world_entity_projection.py`**：revision=`20260801_2703`、
   down_revision=`20260801_2702`，单 head、upgrade/downgrade 可逆、旧数据兼容。
8. **Fixtures**：`entities_v1.json`。
9. **测试**：`test_entities.py` + `test_world_model_authority.py`（扩展）+
   `test_entity_replay.py`。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/world_model tests/adversarial/test_world_model_authority.py tests/integration/world_model -q` | ✅ **140 passed** |
| `pytest tests/integration/world_model/test_entity_replay.py -q`（两次） | ✅ 14 passed / 14 passed |
| `alembic heads` | ✅ 单 head `20260801_2703` |
| `pytest tests/unit/queryplan tests/integration/queryplan tests/adversarial -q` | ✅ **332 passed**（回归） |
| `pytest tests/unit -q`（全量） | ✅ **622 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- 别名冲突可审查且不 false merge；规则例外和归属/空间状态可查询，原始 lineage 不丢失；
- D-06：chat 内容绝不作为 canon；
- 不确定合并保持 candidate/review 状态。

## 备注 / 连带修改

- 多个集成测试文件的 migration head 引用改为**动态发现单 head**（`_current_head` helper），
  避免未来每次 head 变化都要手工改断言。
- `STATE.md`/`22-RESEARCH.md` 记录 Stub-SUT Nightly finding（Phase 22 调查）：stub 管线
  下完整 G2 部署过早，延后到真实模型评分（Phase 29 / Pi-gateway live adapters）。
- 独立验证实际收集 140 项（低于估算 150+），全部通过——收集数为权威。
