---
adr: 0003
title: 多级认知分层抽取与先验驱动分析范式 (Multi-Tier Cognitive Ingestion Paradigm)
status: accepted
date: 2026-08-23
snapshot: >
  本文档基于对《关于我转生成史莱姆这件事》(Novel 91, 515章) 全书分析实战审计与性能分析沉淀。
  对比了传统流水线（2000+次 HTTP 逐章爬梳耗时数小时）与大模型先验直出（数十秒高密度物化）的本质差异。
closes:
  - NM-ARCH-INGEST-001
  - NM-PERF-ANALYSIS-001
references:
  - docs/adr/0001-layer-registry.md
  - docs/adr/0002-narrative-unit-vs-narrative-memory.md
  - docs/architecture/05-import-pipeline.md
  - docs/architecture/06-rag-pipeline.md
---

# ADR-0003: 多级认知分层抽取与先验驱动分析范式

## 1. 背景与现状痛点 (Context & Problem)

NovelMind 既有的分析提取流水线（`ChapterBatch`、`TimelineWorker`、`CluesWorker`）采用的是**单一的“零先验（Zero-Prior）自底向上冷启动”**设计：
1. **机械化逐章扫描**：不论导入的是全世界熟知的经典名作（如《转生史莱姆》、《龙族》、《三体》），还是用户自己撰写的原创生书，系统一律将其视为未知黑盒。
2. **严重的 I/O 与时间开销**：对一本 500 章、500 万字的长篇小说，需要触发 1,500 ~ 2,500 次独立的外部 LLM HTTP API 调用。受模型生成速度、网络往返以及 Provider 速率限制（RPM/TPM）影响，完整分析通常耗时 **1 ~ 3 小时**。
3. **宏观视野割裂（只见树木不见森林）**：微观逐章抽取往往过于关注局部的琐碎对话，难以像人类专家一样自顶向下把握横跨数百章的宏观史诗转折、莫比乌斯伏笔闭环与世界法则。

然而在工程实践中发现：**现代大模型参数空间中已经完整内化了主流经典 IP 的全书知识；互联网上也存在极其完善的百科与人物设定资料。**

---

## 2. 核心决定：三级智能认知梯度 (Decision: Three-Tier Cognitive Hierarchy)

自本 ADR 起，系统确立 **“多级认知分层抽取范式（Tier 1 ➔ Tier 2 ➔ Tier 3）”** 作为小说多维知识构建的官方架构演进路线：

```mermaid
graph TD
    A[小说导入完成: 基础切块与 ChromaDB 向量化完毕] --> B{第一级: 模型参数空间是否已内化该作品?}
    
    B -- "是 (知名经典作品, 如《史莱姆》《龙族》)" --> C[Tier 1: 参数化先验宏观直出 (Parametric Memory)]
    B -- "否" --> D{第二级: 互联网是否存在公开设定与百科?}
    
    D -- "是 (连载网文/人气小说)" --> E[Tier 2: 联网搜索与百科速查提取 (WebSearch / Wiki)]
    D -- "否 (用户原创/私密稿件/生僻文本)" --> F[Tier 3: 原生 ChapterBatch 逐章深度挖掘 (Raw Text RAG)]
    
    C --> G[本地 ChromaDB 向量库进行证据对齐与章节反查]
    E --> G
    F --> G
    
    G --> H[严格写入 PostgreSQL Candidate 候选区 (带 evidence_span 与 SHA-256 校验)]
```

### 认知梯队定义 (T1 / T2 / T3)

| 梯队 | 认知来源 | 触发条件 | 产出方式 | 预期耗时 | 适用代表作品 |
|---|---|---|---|---|---|
| **Tier 1**<br>(参数化先验) | 大模型内部记忆<br>*(Parametric Memory)* | 经典名作、全球主流 IP、经典文学 | 大模型直接全局自顶向下解构，瞬间产出 30+ 角色档案、全部高潮场景与底层世界法则。 | **15 ~ 30 秒** | 《转生史莱姆》《龙族》《诡秘之主》《三体》《哈利波特》 |
| **Tier 2**<br>(外脑百科速查) | 萌娘百科 / 百度百科 / Fandom Wiki | 连载中热门网文、中等知名度作品 | 启动专用 WebSearch / Wiki 解析工具，抓取人物表、势力图、剧情分卷，快速重构知识图谱。 | **30 ~ 60 秒** | 起点/晋江热门签约小说、动漫化连载作品 |
| **Tier 3**<br>(原文逐章挖掘) | 本地原文向量库 + ChapterBatch | 用户原创未公开手稿、冷门生书 | 启动后台 Worker，以受限并发窗口逐章执行 `analyze-chapter`，严格自底向上爬梳。 | **30 ~ 120 分钟** | 作者原创草稿、私密小说、无公开记录文本 |

---

## 3. 防幻觉与安全保障：先验生成 + 本地证据反查 (Evidence Grounding Gate)

为确保 Tier 1 与 Tier 2 在极速生成的同时**绝对不产生幻觉、不偏离用户导入的实际文本**，必须严格执行 **“先验生成 ➔ 向量反查 ➔ 候选门禁”** 的双向校验闭环：

1. **宏观结构快速合成（Top-Down Candidate Synthesis）**：
   * Tier 1 / Tier 2 负责生成结构化的 Visual Bible、Key Scenes、World Rules 与 Clues 实体。
2. **微观证据毫秒级定位（Vector Semantic Alignment）**：
   * 系统利用导入期在本地构建的 **ChromaDB 向量索引（`text_chunks`）**，对生成的场景和事件进行快速语义反查。
   * 自动对齐并填充真实的 `chapter_id`、`chapter_number`、`source_start`、`source_end` 及 `evidence_span`。
3. **不可变正史保护（Canon Immutability & Candidate Gate）**：
   * 所有 Tier 1 / Tier 2 生成的数据，一律以 `review_state='candidate'` 写入 PostgreSQL。
   * 必须通过 64 位 SHA-256 幂等哈希校验，不直接修改正史，由用户在 `/analysis` 工作台进行审查确认。

---

## 4. 收益与影响评估 (Consequences)

### 积极影响 (Positive)
1. **用户体验质的飞跃**：主流小说导入后，无需等待数小时，**数十秒内**即可在工作台呈现丰富饱满的 40+ 角色视觉设定、全书名场面与伏笔图谱。
2. **算力与 Token 成本极大节约**：避免了对已知作品进行数千次冗余的逐章 API 问答，将宝贵的高精度算力留给真正的原创生书（Tier 3）。
3. **更高质量的宏观叙事理解**：自顶向下的架构解构天然具备跨卷全局视野，有效避免了自底向上逐章分析容易产生的视野割裂与碎片化。
4. **架构 100% 向后兼容**：完全复用既有的 PostgreSQL 数据表结构（`visual_bible_*`、`key_scene_*`、`world_model_*`、`machine_clues`、`relationship_observations`），前端无感适配。

### 约束与注意事项 (Constraints)
* 当版本发生重大偏差（如用户上传的是某小说的“同人魔改版”而非“原著原版”）时，Tier 1 先验必须通过本地向量反查发现相似度断崖，并平滑降级（Fallback）回 Tier 3 原文逐章挖掘模式。
