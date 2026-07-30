# FAQ — 常见问题

> 最后更新：2026-07-30
> 本文档每个问题的状态基于 `feat/phase21-debtfix` 分支的实际代码。
> 状态标记：✅ 已实现 · ⚠️ 部分实现 · ❌ 未实现/待改进

---

## 导入与索引 ✅

### 导入时已经进向量库了吗？

**是的，自动完成。** `indexing_service.index_novel()` 自动执行三件事：

1. `chunking_service.chunk_novel()` — 每章切成 300-500 字的语块
2. `ai_service.embedding()` — **每块调 AI 模型算向量**
3. `vector_store.add_chunks()` — **存入 ChromaDB（向量数据库）**

嵌入和 Chroma 写入遇到短暂故障时会自动有限重试（指数退避，最多 2 次）。全部重试仍失败时，小说会标记为 `indexing_failed`，导入任务标记为 `failed`，不会伪装成可搜索的 `ready`；此时搜索接口返回明确提示，需调 `POST /api/novels/{id}/index` 重建。

断点续跑：启动时自动扫描 `embedding_status = 'pending'` 的块并续跑，不重新分块、不删除已有向量。

### 向量嵌入用的是 Embedding 模型还是聊天模型？

项目中 `ai_service.embedding()` 走的是**配置的路由模型**。如果用的是 Ollama，建议预先拉取专用 embedding 模型（如 `nomic-embed-text`），用聊天模型做嵌入开销大、效果差。

---

## AI 会话（Reader Chat）⚠️

### 分析前和分析后的 AI 会话逻辑一样吗？

**一样。** 证据检索走同一套逻辑（`retrieve_visible_evidence()`），始终查 4 个来源：

| 证据源 | 未分析 | 已分析 |
|---|---|---|
| hierarchy（分块层级） | ✅ | ✅ |
| timeline（时间线事件） | ❌ ABSENT | ✅ |
| relationship（人物关系） | ❌ ABSENT | ✅ |
| selection（用户选区） | ✅ | ✅ |

未分析时 timeline/relationship 来源返回空，LLM 只看得见原文上下文。

### LLM 能检索原文吗？为什么问几十章前的伏笔回答不了？❌

**不能。** 当前 Reader Chat 是「闭卷模式」：

```
用户提问 → 后端一次性预打包 ≤24 条证据 → 一次 LLM 调用（无 tools）→ 回答
```

LLM 不能：
- ❌ 查数据库或原文
- ❌ 信息不够时自己再去捞
- ❌ 搜索关键词

所以问「第 100 章时问第 10 章的伏笔」，除非那 24 条证据正好包含，否则 LLM 只能靠训练数据猜，不可靠。

### 伏笔数据为什么没效果？❌

clue（线索/伏笔）数据没有进入 Reader Chat 的证据检索源。即使分析过，LLM 也看不到结构化的 plant→payoff 链。

更深层的原因：不单是伏笔，**整个分析管线质量不稳定** → 预检索证据不准 → LLM 拿到的上下文是错的 → 回答不可靠。改进方向不是继续加分析管线，而是**让 LLM 自己查原文**。

### AI 回答的上下文包含什么？✅

```
1. 你选中的原文片段（服务器验证 text hash + offset，防止客户端篡改）
2. 最多 24 条 evidence，按优先级排序：
   - 原文块（ChunkHierarchyNode）
   - 时间线事件（MachineTimelineEvent）
   - 人物关系观测（RelationshipObservation）
3. 一条 system prompt
```

**所有 evidence 都剧透过筛到你的 `reading_progress` 截止章节**（除非开启了「显示全书」模式）。

### 引用是怎么做的？✅

每条答案自动生成 `ReaderMessageCitation`，标注了引用原文的 chapter_id / offset / hash。前端可以显示原文位置锚点。

### NM claims 会出现在聊天上下文中吗？✅

**目前不会。** NM claims 只被 NM builder 内部消费，不直接注入阅读器 chat。AI 回答依赖的是原文索引 + 时间线 + 关系图，而不是 NM 的抽象断言。

### 改进方案（待实现）：给 LLM 加原文搜索工具

**当前架构：**

```
喂什么答什么
└─ 高度依赖分析质量
```

**目标架构：**

```
用户提问
  ├─ 初始上下文：
  │   ├─ 选区原文
  │   └─ 时间线摘要（当前章前后 N 章事件列表）
  └─ 工具：search_novel_text(query, top_k)
       └─ 复用 indexing_service.search_similar()（BM25 + 向量搜索）

  → LLM 信息不够时自己搜原文
```

**预期效果：**

| 场景 | 当前 | 改后 |
|---|---|---|
| 问几十章前的伏笔 | ❌ 猜 | ✅ 搜原文找到相关段落 |
| 未分析的小说 | ❌ 只能答当前章 | ✅ 能搜全书原文 |
| 分析质量差的情况 | ❌ 跟着错 | ✅ 绕过分析直接读原文 |
| 前端 | — | 不变（工具调用透明） |

**不改的：** 前端界面、现有检索管线、分析管线。只改 Worker 层加 tool calling 支持。

### 当前提示词质量如何？✅

项目中 7 个 `.v1.txt` 提示词文件质量很高：

| 提示词 | 评价 |
|---|---|
| reader_chat_answer.v1.txt | ⭐⭐⭐⭐⭐ 反注入+证据白名单+JSON schema+不确定性处理 |
| timeline_chapter_extract.v1.txt | ⭐⭐⭐⭐⭐ 同上 |
| clue_semantic_judge.v1.txt | ⭐⭐⭐⭐⭐ 10 条权威规则+冲突标记 |
| relationship_semantic_judge.v1.txt | ⭐⭐⭐⭐⭐ 同上 |
| rag_fixture_generator.v1.txt | ⭐⭐⭐⭐ 依赖外部证据包质量 |
| rag_fixture_judge.v1.txt | ⭐⭐⭐⭐⭐ 盲评设计+接受阈值 |
| NM builder（脚本内） | ⭐⭐ 硬编码在 `.py` 中，可维护性差 |

所有 `.v1.txt` 提示词都包含：反注入、证据 ID 白名单、Strict JSON schema、不确定性处理、模型行为约束。

**瓶颈不在提示词，在输入给提示词的内容质量。**

---

## 时间线分析 ✅

### 跨章归并（Reconciliation）跨几章？

**全书所有已提取的章节一起归并。** 不是一章一章合。`TimelineReconciler.reconcile()` 收到的是**本书全部 EventCandidate 的完整列表**，一次调 LLM。

### 没有原文、硬靠事件名匹配吗？

**不是。** 发给 LLM 的每个事件都保留了完整的 `title`、`description`、`participants`、`evidence_refs`（含原文 chapter_id + offset + hash）。LLM 是**看着原文证据做语义判断**，不是只比事件名。

比如第六章的 `{title: "大闹天宫", evidence_ids: ["ch6:235-280"]}` 和第七章的 `{title: "天宫交战", evidence_ids: ["ch7:30-65"]}` — LLM 同时看到两边的原文段落来判断它们是不是同一件事。

### 归并的具体输出是什么？

```json
{
  "duplicate_groups": [["ev_6_1", "ev_7_3"]],
  "story_constraints": [{"event_a":"ev_1","event_b":"ev_2","relation":"before"}],
  "causal_edges": [{"source_id":"ev_1","target_id":"ev_2","edge_type":"causes", "evidence_ids":[...], "confidence":0.9}]
}
```

### 分析过程中前端能看到结果吗？

可以。后台运行期间每 **2.5 秒**轮询一次 `status()` + `getTimeline()`。结果以 `running_candidate` 版本逐步出现在前端。完成后自动切换到 `active` 版本。

---

## 人物关系提取 ⚠️

### 人物名从哪里来的？是从时间线事件里临时提取的吗？

**不是。** 关系的来源是 `KnowledgeRelationJudgment` 表——这是**之前阶段（Phase 04 知识管线）已经跑过的 AI 关系判定**，已经存好了人物对 + 关系类型 hint。`RelationshipCandidateService` 从中筛选出两端都是 `character` 类型、类型为 `ally/enemy/family/mentor/romantic` 的记录。

### 发给 LLM 的到底是什么？

发给 LLM 的不是一句话，而是一个**标准化的证据包**（`RelationshipEvidencePackage`）：

```json
{
  "source_ref": "孙悟空",
  "target_ref": "玉帝",
  "relation_type_hint": "enemy",
  "allowed_relation_types": ["ally","enemy","family","mentor","romantic"],
  "evidence": [
    {
      "evidence_id": "ev_xxx",
      "chapter_number": 7,
      "source_start": 235,
      "source_end": 280,
      "excerpt": "孙悟空举起金箍棒，向玉帝打去……"
    }
  ]
}
```

证据最多 **8 条**，每条正文截取 **700 字**。

System prompt 明确说：**原文内容不可信**（可能有 prompt injection），LLM 只能从 allowed_evidence_ids 中引用证据。

### confidence 多高才自动通过？

`AUTO_ACCEPT_THRESHOLD = 0.8`。≥ 0.8 自动写入 `RelationshipObservation` 表，< 0.8 标记为 `review` 待审。

### 关系演化观测（change/end）是否在生产链中形成？❌

**尚未形成。** 当前建立（establish）关系为主，change/end 类型的演化观测生产链未在样例中形成闭环。

---

## 叙事记忆（NM）⚠️

### NM 也是 LLM 参与的吗？

**全部都是 LLM 调用。** NM 是项目里**最重的 AI 流水线**——不只是问一次 LLM，而是 5 个阶段逐层构建。

### Stage 1（chapter_state）具体发过去什么？

每章调一次 LLM，发送：

1. **该章的完整原始正文**（作为 evidence）
2. **从上一章 carry forward 过来的 open loops / entity states**（延续的叙事线索）
3. 要求输出该章节下**所有的 claims**（6 种类型）

这不是随便问一问——每次都有：
- 严格 JSON Schema（`StrictStructuredOutput`）
- 预算门控（token / 费用上限）
- input 谱系哈希（prompt + schema + model + config 全部记录）
- 精确的 provenance 链接回原文

### 为什么树是平的？⚠️

实际只跑了约 12 章的 `chapter_state`，后续 4 个阶段（ARC_VOLUME_PLAN / ARC_VOLUME_AGGREGATE / GLOBAL_AGGREGATE / MANIFEST_VALIDATION）**代码已写好但未调度执行**（预算 / 优先级原因）。恢复构建可运行 `_nm_resume_loop.py`。

### NM 当前用于什么？✅

**只读预览（candidate_preview）。** NM 版本构建后即 **sealed**（不可变、可审计），没有任何 `promote` 操作，不驱动任何生产数据。前端树上的 "L2-L4" 标签和 "候选预览" 提示就是告诉你这是未发布的。

### arc_window_size 可配置吗？❌

`RunPolicy` 中 `arc_window_size` 固定为 3，不受外部配置覆盖。

---

## 预算与成本 ⚠️

### AI 会话有费用限制吗？

有。默认值：
- 每次对话上限：40 次调用、400K input tokens、$5
- 每本书上限：400 次调用、4M input tokens、$50

### 预算可以配置吗？⚠️

**部分可以。** 预算已持久化到 PostgreSQL（`ReaderBudgetLedger` 表），首次创建时使用硬编码默认值。但目前没有 API 或 UI 让用户修改这些值。

---

## 阅读器 ✅

### 书签怎么用？

在阅读器中选中正文后，点击选区浮层的「书签」按钮。系统会保存章节、选区起止位置、选中文本和内容校验哈希。如果章节内容发生变化，服务端会拒绝过期选区，避免书签定位到错误段落。

### 滚动模式支持自动衔接上下章吗？✅

支持。滚动模式下一次性渲染全部章节，滑到底自动前进到下一章，回到顶部自动倒退到上一章（阈值 64px）。进度自动追踪当前视野所在章节。沉浸模式底部控制栏在滚动模式下自动隐藏上下章按钮。

### 沉浸模式的目录入口和控制栏分开吗？❌

**未分开。** 目录按钮仍包在 `immersiveChrome` 条件块内，点按阅读区隐藏控制栏时目录按钮也一起消失。

---

## 通用

### 项目用了多少 AI 能力？

| 用途 | 调用频率 | 模型要求 |
|---|---|---|
| 向量嵌入（搜索索引） | 每语块一次，导入时 | Embedding 模型 |
| 时间线事件提取 | 每章一次 | 强推理模型（quality tier） |
| 时间线跨章归并 | 全书一次 | 强推理模型（quality tier） |
| 人物关系判决 | 每个候选对一次 | 轻量模型即可 |
| NM chapter_state | 每章一次 | 强推理模型 |
| 阅读器聊天回答 | 用户每次提问 | 按路由策略 |
| Eval 评测 | 按需 | 评测模型 |

### 一些文件导入后搜索不到内容？

检查小说详情页的状态：`indexing_failed` 或已嵌入数量少于分块数量，说明检索索引未完成。调 `POST /api/novels/{id}/index` 重建索引；重建成功后状态会恢复为 `ready`。

### 如何保存正文中的一段文字？

在阅读器中选中正文后，点击选区浮层的「书签」。系统会保存章节、选区起止位置、选中文本和内容校验哈希；如果章节内容发生变化，服务端会拒绝过期选区，避免书签定位到错误段落。
