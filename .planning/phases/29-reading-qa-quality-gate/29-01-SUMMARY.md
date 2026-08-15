# 29-01 SUMMARY — Reading QA Gold Set

**Status:** COMPLETE | **Date:** 2026-08-03 | **Execution override:** user-authorized
(Phase 22 3/3 gate skipped, Phase 29 override)

## What Was Built

1. **`backend/app/services/qualification/gold_set.py`** — 八桶 gold-set loader + fingerprint
   （键序无关、hash 敏感）+ curator agreement（=1.0 可重算）+ 证据 re-slice 校验。
2. **`backend/app/services/qualification/rubric.py`** — source/cutoff/authority 评分 +
   leakage/owner/spoiler/lineage 阻塞门（违例→blocked，`qualified_candidate` 仅在无违例时
   出现，D-05 两值裁决）。
3. **`backend/evals/reading_qa_v1.json`** — 冻结数据集：单书《雾港谜案》6 章 + 14 样本
   （local×2、cross_chapter×2、global×1、causal×2、character_knowledge×2、world_rule×2、
   no_answer×1、spoiler×2），每样本 source answers + cutoff label + 证据 refs + 内嵌
   source snapshot + fingerprint。
4. **测试**：`test_gold_set.py` 33 + `test_qualification_lineage.py` 11。

## 独立测试验证（2026-08-03，独立测试子代理）

| 命令 | 结果 |
|---|---|
| `pytest tests/unit/qualification tests/adversarial/test_qualification_lineage.py -q` | ✅ **44 passed** |
| `pytest tests/unit/qualification/test_gold_set.py -q`（两次） | ✅ 33 passed / 33 passed |
| `pytest tests/unit -q`（全量） | ✅ **683 passed** |
| `pytest tests/adversarial -q`（全量） | ✅ **234 passed** |
| `from app.main import app` | ✅ OK |

## 关键设计

- 单书 gold set 含全部八桶冻结样本 + source answers + cutoff labels；
- dataset fingerprint 与 curator agreement 可复现；
- leakage/owner/spoiler/lineage 违例阻止 qualification（fail-closed）。

## 备注

- 八桶命名按 D-01 扩展为 `local/cross_chapter/global/causal/character_knowledge/
  world_rule/no_answer/spoiler`（Phase 29 新数据集词汇，供 29-02..29-05 消费）。
- 无 DB/迁移：gold set 为数据 + 校验逻辑，纯单元测试。
