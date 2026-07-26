---
adr: 0002
title: Narrative Unit 与 Narrative Memory 的系统边界、Active 语义与消费顺序
status: accepted
date: 2026-07-26
snapshot: >
  本 ADR 的事实性描述以 git commit 9f01680 时的代码为准，
  核对文件：backend/app/models/knowledge_unit.py、
  backend/app/models/narrative_memory.py（及 *_builder/_qualification/_rebuild）、
  backend/app/services/knowledge_units/search.py、
  backend/app/api/search.py、backend/app/api/narrative_memory.py、
  backend/app/services/reader_chat/retrieval.py。
closes:
  - NM-GOV-001
related:
  - NM-GOV-004
  - NM-DATA-010
references:
  - .planning/ARCHITECTURE-LAYERING-DATA-GOVERNANCE-AUDIT-2026-07-17.md
  - docs/adr/0001-layer-registry.md
---

# ADR-0002: Narrative Unit vs Narrative Memory

## 背景

代码中并存两套名称高度相近的"叙事"系统（审计 NM-GOV-001）：

- **Narrative Unit（NU，Phase 05-06）**：`backend/app/models/knowledge_unit.py`
  （注意：模型文件名为单数 `knowledge_unit.py`，服务包为
  `backend/app/services/knowledge_units/`）；
- **Narrative Memory（NM，Phase 13-17、20）**：
  `backend/app/models/narrative_memory.py` 及 builder/qualification/rebuild
  配套模型。

两者表前缀都是 `narrative_*`，但**用途、数据模型、版本语义、发布权限完全
不同**。本 ADR 固定边界，防止维护者把二者当成同一发布生命周期的两个阶段。

## 1. 两套系统如实描述

### 1.1 Narrative Unit — 生产检索的问答式知识单元

- **用途**：把 Phase 04 已接受的知识判定（`knowledge_relation_judgments`，
  `backend/app/models/knowledge.py:391`）固化为可检索的 Q/A 单元，服务生产
  检索（`/api/search`）。
- **数据模型**（knowledge_unit.py）：
  - `narrative_source_snapshots` / `narrative_source_snapshot_items`：
    冻结的输入清单（ORM 事件强制不可变，knowledge_unit.py:535-544）；
  - `narrative_units`：单元本体，字段含 `question/answer/subject_key/
    relation_type/confidence`，强制判定与证据 lineage（`source_judgment_id`、
    `primary_evidence_id`、`evidence_count > 0` CHECK）；
  - `narrative_unit_evidence_links`：归一化证据链；
  - `narrative_index_builds`：不可变候选索引 manifest（Chroma
    `collection_name` 仅为投影）；
  - `narrative_active_pointers`：**权威 Active 指针**（docstring：
    "Chroma is only its projection"，knowledge_unit.py:382）；
  - `narrative_promotion_journals`：prepare/commit/rollback 晋升审计
    （`PROMOTION_JOURNAL_STATUSES = ('prepared','committed','failed',
    'rolled_back')`，knowledge_unit.py:54）；
  - `narrative_source_watermarks` / `narrative_refresh_runs`：增量刷新审计。
- **版本/索引语义**：`canonical_id + version` 唯一
  （`uq_narrative_units_canonical_version`）；单元状态机
  `draft/candidate/active/failed/deprecated/rolled_back`
  （knowledge_unit.py:37-44）。
- **Active 含义**：`NarrativeActivePointer`（owner+novel+domain_profile 唯一）
  指向唯一一个 `NarrativeIndexBuild`；晋升经 CAS + Promotion Journal，可回滚。
  **NU 拥有完整的 R* 生命周期直到 Active（ADR-0001 §3）。**

### 1.2 Narrative Memory — candidate-only 的层级叙事状态

- **用途**：构建 S4-S6（chapter_state / story_arc / volume / global_story）
  层级叙事认知（ADR-0001 §1），当前仅供只读候选预览与离线实验。
- **数据模型**（narrative_memory.py）：
  - `narrative_memory_versions`：不可变候选版本身份，冻结
    `hierarchy_build_id/hierarchy_checksum/source_snapshot_hash/prompt_hash/
    schema_hash/model_lineage/decoding_hash/config_hash/policy_hash`；
  - `narrative_memory_nodes`（`node_kind IN ('chapter_state','story_arc',
    'volume','global_story')`）、`narrative_memory_claims`（六类 typed
    claim）、`narrative_memory_edges`（`contains/derives_from`）；
  - `narrative_memory_source_links`：claim 下钻到 Phase 07 S1 证据叶子
    （FK → `chunk_hierarchy_nodes(build_id,node_id)`，
    narrative_memory.py:365-370）；
  - `narrative_memory_manifests` / `narrative_memory_validation_reports`：
    封存与结构校验（verdict 仅 `qualified_candidate|blocked`）；
  - 构建/资格/重建：`narrative_memory_build_*`（builder）、
    `narrative_memory_qualification_*`（Phase 15）、
    `narrative_memory_rebuild_*`。
- **Active 含义：不存在。** 模块 docstring 明确 "This module deliberately
  contains no execution lifecycle or production selector"
  （narrative_memory.py:1-5）；没有任何 active pointer 表，没有 promotion
  journal。`qualified_candidate` 是 NM 版本能到达的最高状态——它表示
  "有资格成为候选"，**不是** Active。
- Phase 20 API（`backend/app/api/narrative_memory.py:1-4`）docstring：
  "No promotion, no builder start, no active-pointer resolution"。

### 1.3 是否替代关系

**当前没有替代关系。** NU 与 NM 输入不同（NU 吃 Phase 04 accepted
judgments；NM 吃 Phase 07 hierarchy + 可选 Facet lineage）、粒度不同
（NU 是扁平 Q/A 单元；NM 是 S4-S6 层级状态树）、发布权限不同（NU 可
Active；NM 禁止）。"NM 未来取代 NU 作为检索层"是 Phase 30 才允许讨论的
假设，不是现状。

## 2. 固定消费顺序（生产契约）

1. **生产检索默认：raw chunks + Narrative Unit。**
   `/api/search`（`backend/app/api/search.py:33,64`）注入
   `NarrativeRetrievalStrategy`
   （`backend/app/services/knowledge_units/search.py:209-278`），
   mode 为 `chunks | units | hybrid`：chunks 走 hybrid_search_service
   （`text_chunks` + 向量），units 走 NU active build，hybrid 融合
   （`fuse_results`）。**此策略中没有 NM 的接入点。**
2. **Reader Chat 不消费 NM。** 证据打包优先级
   `SOURCE_PRIORITY = selection(0) > hierarchy(1) > knowledge(2) >
   timeline(3) > relationship_observation(4)`
   （`backend/app/services/reader_chat/retrieval.py:29-35`），无 NM 来源。
3. **NM 仅两个合法消费面：**
   - Phase 20 只读候选预览：`/api/narrative-memory/*`（versions/tree/
     claims/source_links），UI 必须带"候选·预览未发布"标识；
   - Phase 15 离线资格实验：`RetrievalStrategy.HIERARCHICAL_CANDIDATE`
     对照 `LEAF_RAW_BASELINE`
     （`backend/app/services/narrative_memory/qualification_contracts.py:123-125`），
     结果只写入 qualification 表，不进入生产路由。
4. **变更授权：** 任何让 NM 进入生产检索/Reader Chat 的改动（新增 active
   pointer、promotion、router 接入）都必须由未来 Phase 30 显式授权，且先补齐
   审计 NM-GOV-004 列出的前置条件（唯一 Active Pointer、CAS promotion、
   before/after manifest、rollback journal、Reader Chat 对照、与 NU 的消费
   优先级裁决）。在此之前，"Phase 20 complete"不得被解读为"NM 已是生产
   权威"。

## 3. 命名域建议（建议、不强制迁移）

为消除 `narrative_*` 前缀撞名，建议未来新增代码采用两个不同命名域：

| 现名 | 建议名 | 域含义 |
|---|---|---|
| Narrative Unit（`narrative_units` 等） | **RetrievalUnit** | 生产检索单元域（有 Active/Promotion） |
| Narrative Memory（`narrative_memory_*`） | **StoryMemory** | 层级叙事状态域（candidate-only） |

**明确标注：这是命名建议，不要求迁移存量表名、类名或 API 路径。**
存量代码继续使用现名；仅在（a）新建模块/表，或（b）Phase 30 决策要求时
才引入新命名域，且需单独的迁移 ADR。

## 4. 后果

- 关闭 NM-GOV-001：两系统的用途、依赖、Active 语义与消费顺序自此有唯一
  权威描述；PROJECT/REQUIREMENTS 及后续 Phase 24 router 契约引用本 ADR。
- 违反消费顺序（例如在生产 router 中直接读 `narrative_memory_claims`）
  属于架构违规，应在 code review 与（Plan 23-02 起的）契约测试中拦截。
