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
  └── run_reader_chat_worker()
        LLM 根据 manifest 中的 evidence 生成答案
        写入 ReaderMessage + ReaderMessageCitation
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

## LLM 调用

- System prompt：`prompts/reader_chat_answer.v1.txt`
- Context：manifest 中的 evidence entries 注入为上下文
- 模型：按路由策略选择（quality / balanced / budget）
- 输出：`ReaderAnswerEnvelope`（答案正文 + 引用列表）
- 要求：每个引用必须来自 manifest 中的 `allowed_evidence_ids`（validation check）

## 对话生命周期

```python
POST /reader-chat/{novel_id}/conversations
  → 创建 ReaderConversation（status="active"）

POST /reader-chat/{novel_id}/conversations/{id}/ask
  → 返回:
    - 答案正文
    - 引用列表（每条含原文位置 + 片段）
  → 后台写入:
    - ReaderMessage（一问一答）
    - ReaderMessageCitation（每条答案引用的原文）
    - ReaderModelCallAttempt（模型调用记录 + 用量 + 延迟）

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
| `worker.py` | LLM 调用 + 写入 message + citations |
| `prompts/reader_chat_answer.v1.txt` | System prompt |

---

> **常见追问**：AI 回答读到的是什么？NM claims 会进上下文吗？引用怎么做的？→ [FAQ](faq.md#读者聊天reader-chat)