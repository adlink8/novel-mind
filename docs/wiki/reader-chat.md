# 读者聊天

## 触发方式

阅读器中选择一段文字 → 点击「问 AI」→ `POST /reader-chat/{novel_id}/conversations/{id}/ask`

## 上下文组装管线

```
提问
  │
  ├── validate_selection()
  │     服务端验证用户选中的文字是否确实存在于该小说（chapter hash + text hash + byte offset 校验）
  │     防止客户端伪造/篡改选择文本
  │
  ├── resolve_progress_snapshot()
  │     读取 reading_progress → 剧透门控截止章节
  │
  ├── retrieve_visible_evidence()
  │     从 4 个来源检索相关证据，都剧透过筛到截止章节
  │
  ├── assemble_context_manifest()
  │     冻结所有证据为不可变的 ContextManifest（SHA-256 校验和）
  │     持久化到 ReaderContextManifest 表
  │
  └── dispatch_reader_chat_job()
        把生成任务交接为 SkillRun(queued, origin='reader_chat')
        agent-service poller 拉模式执行，finalize 门后答案物化回 ReaderMessage
```

## 证据检索（4 级优先级，按需递进）

| 优先级 | 来源 | 内容 |
|---|---|---|
| 0 | 用户选中文字 | 服务端验证后的原文片段，作为 primary evidence |
| 1 | ChunkHierarchyNode | chunk build 中的证据叶块，剧透过筛 |
| 2 | MachineTimelineEvent | 时间线事件（附带证据引用），剧透过筛 |
| 3 | RelationshipObservation | 人物关系观测（通过 `Phase09RelationshipObservationReader` 接入） |

**硬上限**：最多 24 条 evidence。

**每条 evidence** 包含：
- `evidence_key`（可追溯的稳定 ID）
- `source_type`（hierarchy / timeline / relationship）
- `chapter_id`, `source_start`, `source_end`, `content_hash`
- `excerpt`（截断到 700 字符）

## 剧透控制

所有检索到的 evidence 不得超过 `reading_progress` 指向的章节。

具体实现：`retrieve_visible_evidence()` 接受 `cutoff_chapter` 参数，所有检索来源都必须满足 `chapter_id ≤ cutoff_chapter`。

skill 运行时的域工具同样受 cutoff 门控：

- `search_novel_text`：命中行按阅读 cutoff 过滤（cutoff 后过滤会消耗命中配额，故**过取 4 倍**（上限 50）再过滤再截断；持久化 full_book 开关除外）
- `get_evidence_span`：除 chapter+offsets 外支持 **chunk_id 物化通道**——服务端在章节原文中定位 chunk 内容并确定性推导 offsets（先精确子串匹配，失败则空白规范化回退，多次命中取第一个），模型无需自行数字符

## Skill Run 执行（嵌入式 Novel Agent 运行时）

问答不再由 FastAPI 直接调 LLM。ask 端点只创建 `ReaderGenerationJob` 并入队 `SkillRun(queued, origin='reader_chat')`；执行由 agent-service 的 queued-run poller 完成，**全程 pull**（轮询 → 原子 claim + lease → 执行 → finalize），绝不 FastAPI→agent-service。

两种执行模式：

| 模式 | 机制 | 用于 |
|---|---|---|
| **agentic** | pi 工具循环：模型自己编排域工具（`search_novel_text` 检索 → `get_evidence_span` 物化 evidence_key → 输出结构化 JSON） | answer-reading-question（阅读问答） |
| **guided** | 程序确定性检索+物化 → 编号摘录菜单（**绝不含 evidence_key**）→ 模型单次网关补全（用 evidence_indices 引用菜单编号）→ 程序把编号映射回真实 evidence_key | build-visual-bible、detect-key-scenes（backfill skill） |

关键纪律：

- **模型只产语义字段**：全部哈希/血缘（schema_hash / policy_hash / manifest_hash / claim_hash / source_snapshot / cutoff）由投影层程序注入，模型提供的同名字段一律忽略
- **fail closed 零写入**：引用未物化证据、越界章节（超 cutoff 或 chapter 0 前言页）、枚举越界一律抛错；poller 有界修复环（MAX_REPAIR_ROUNDS=2）把校验错误+可用菜单喂回同一会话定向修正，超出则 run 诚实失败
- **finalize 是唯一写入口**：Structured Output Integrity 门 + 预算 fail-closed + 冻结 manifest 引用白名单校验；cancelled/failed 一律 0 artifact 行
- **运行中预算熔断**：工具调用计数超 max_calls 立即 abort，不再放任工具循环烧穿预算
- 答案 artifact 由 `materialize_reader_chat_answer()` 投影回原对话（ReaderMessage + ReaderMessageCitation），引用必须来自 manifest 的 `allowed_evidence_ids`（validation check 不变）

## 按需分析回填（chat_backfill）

问答证据维度不足时（manifest 的 `prompt_inputs.source_status` 标记 `unavailable`/`absent`），不直接回答，而是触发按需分析：

```
维度不足
  │
  ├── create_backfill_runs()
  │     按 DIMENSION_TO_SKILL 映射选 skill：每次最多 MAX_BACKFILL_SKILLS=2 个，
  │     按维度优先级选择，同 skill 去重；同 novel+维度在途 run 跳过
  │     写 SkillRun(queued, origin='chat_backfill')
  │     detect-key-scenes / build-visual-bible 额外锚定 source snapshot hash +
  │     cutoff（按命名空间重放哈希，命名空间用错会永远 stale）
  │
  ├── ReaderGenerationJob 挂起 paused_dependency
  │     status_reason = waiting_analysis:<维度列表>
  │
  ├── agent-service poller claim 并执行 backfill skill
  │     产物：candidate-only Artifact + 域表 candidate 行
  │     （visual_bible_versions / key_scene_sets，review_state='candidate'）
  │
  └── 全部 backfill 物化完成（status_reason 以 materialized: 前缀确认）
        → reconcile_reader_chat_after_backfill() 重建 manifest → 重新入队 answer run
```

维度→skill 映射（`DIMENSION_TO_SKILL`）：

| 维度 | skill | 物化目标 |
|---|---|---|
| character_state / world_projection | propose-world-model-candidates | 世界模型候选 |
| knowledge | propose-world-model-candidates | knowledge 候选（claim_kind=character_knowledge） |
| relations | build-visual-bible | visual_bible_versions（candidate） |
| raw_text | detect-key-scenes | key_scene_sets（candidate，带 leaf evidence_ranges） |
| events_causality / timeline | build-story-arc | artifact-only（不写域表） |

- **诚实失败**：无映射维度（如 relationship_observation）→ job 置 `backfill_unavailable`，绝不挂空等待态假补足
- **candidate-only**：backfill 产物只有用户在审查界面显式批准（append-only review 事件）后才成为可用权威，物化器绝不 promotion
- 挂起有超时（30 分钟）与终态恢复分类：backfill failed/cancelled/超时/物化失败都会让 job 诚实失败，而非永久停摆

## 对话生命周期

```python
POST /reader-chat/{novel_id}/conversations
  → 创建 ReaderConversation（status="active"）

POST /reader-chat/{novel_id}/conversations/{id}/ask
  → 创建 ReaderGenerationJob + 冻结 ContextManifest
  → dispatch_reader_chat_job() → 入队 SkillRun(origin='reader_chat')
  → 证据充足：poller 执行 → finalize → 答案物化
  → 维度不足：触发 chat_backfill，job 挂起 waiting_analysis:<维度>；
    backfill 物化完成后 reconcile 重建 manifest 并重新入队 answer run
  → 物化写入:
    - ReaderMessage（一问一答）
    - ReaderMessageCitation（每条答案引用的原文）
    - 运行血缘记在 SkillRun + job.model_lineage（runtime='pi', skill_run_id）

GET /reader-chat/{novel_id}/conversations/{id}/messages
  → 分页读取历史消息
```

## 关键限制

- NM claims **不在**阅读器 chat 的证据来源中——NM 目前只被 builder 内部消费
- 上下文只包含「你读过的内容」——不涉及未读章节
- 每条回答自动带有原文引用脚注

## 关键代码位置

| 文件 | 职责 |
|---|---|
| `context.py` | 上下文组装（validate_selection → progress → retrieve → manifest） |
| `retrieval.py` | 多源证据检索 + 剧透过筛 |
| `conversations.py` | 对话 CRUD |
| `worker.py` | 后台任务入口 `dispatch_reader_chat_job`（交接 SkillRun；不直接调模型） |
| `prompts/reader_chat_answer.v1.txt` | System prompt |
| `agent_runtime/reader_bridge.py` | reader chat ↔ SkillRun 桥接：入队、backfill 挂起/恢复（reconcile）、答案物化 |
| `agent_runtime/backfill.py` | 维度→skill 映射（`DIMENSION_TO_SKILL`）+ backfill run 创建（去重、快照锚定） |
| `agent_runtime/finalize.py` | run 终态 finalizer：integrity 门、预算 fail-closed、引用白名单、零写入纪律 |
| `agent-service/src/poller.ts` | queued-run 轮询/claim/执行分发（agentic / guided）+ 有界修复环 + 预算熔断 |
| `agent-service/src/guided/` | guided 模式：`retrieval.ts` 确定性检索+物化、`executor.ts` 单轮补全、`translate.ts` 编号→key 翻译 |
| `agent-service/src/structured-output/` | 投影层：模型语义 → 完整契约（哈希/血缘程序注入） |

---

> **常见追问**：AI 回答读到的是什么？NM claims 会进上下文吗？引用怎么做的？→ [FAQ](faq.md#读者聊天reader-chat)