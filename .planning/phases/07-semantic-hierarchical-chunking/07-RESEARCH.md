# Phase 07 Research — Semantic and Hierarchical Chunking

## 结论

Phase 07 应扩展现有 `ChunkingService`，而不是引入通用 RAG/agent 框架。推荐架构是：规则产生完整候选切片及每个边界的 `confidence + reason_codes`，仅把低置信边界发送给 LiteLLM；LLM 只能裁决边界和最小上下文保留，不能生成正文、事实、持久化命令或发布决定。所有输出先经严格 Pydantic/JSON Schema 和 offset/hash 校验，再由确定性状态机生成不可变 candidate；Phase 06 质量门是唯一发布仲裁者。

## 本地事实

- `backend/app/services/chunking_service.py` 当前按换行拆段、合并短于 50 字的段落，以 300–500 字为目标合并；超长段落按 `。！？；…` 分句，并产出 `chunk_type`、`chunk_index`、`word_count` 和章节位置。
- 当前 chunk 没有稳定源码 offset、source snapshot、chunker/prompt/model/schema lineage，也没有 chapter → scene → evidence 父子关系。
- Phase 05 已验证不可变 candidate、active pointer、prepare/commit promotion、精确 checksum、增量 content-hash delta、reconcile 和 rollback/restore。Phase 07 应复用协议语义，不另造“直接覆盖 active”路径。
- Phase 06 已验证冻结 fixture、lineage、依赖健康、成本/延迟 policy、`quality_comparable` 和 fail-closed arbiter。其 06-08 计划补 chunker lineage；Phase 07 的任何 A/B 报告必须提供该 lineage。
- 运行时已有 `litellm>=1.83.10`、Pydantic 2、异步 `litellm.acompletion`，并有本地 `qwen3.5:9b`。不需要 LangChain、LlamaIndex 或 LangGraph。

## 目标架构

```text
immutable source snapshot
  -> normalize text while preserving source offsets
  -> rule boundary proposals (all boundaries get confidence/reason codes)
  -> high-confidence deterministic split/merge
  -> low-confidence bounded LiteLLM adjudication
  -> schema + offset + context-preservation validation
  -> rule fallback for every invalid/unavailable decision
  -> chapter -> scene -> evidence immutable candidate manifest
  -> candidate index/reconcile
  -> Phase 06 frozen A/B + cost/safety gate
  -> prepare/commit active pointer OR retain previous active
```

### 规则初切与置信度

边界单位是两个相邻 atomic spans 之间的位置；atomic span 来自保留 offset 的段落/句子扫描，不允许 LLM 改写 span。

| 信号 | 建议 reason code | 对 split confidence 的影响 |
|---|---|---|
| 章节起止 | `CHAPTER_EDGE` | 强制边界，1.0 |
| 空行/标题/显式分隔符 | `STRUCTURAL_BREAK` | 强正向 |
| 时间或地点转换标记 | `TIME_SHIFT` / `LOCATION_SHIFT` | 正向 |
| 说话者/叙事视角显著变化 | `SPEAKER_SHIFT` / `POV_SHIFT` | 正向，但歧义时交 LLM |
| 引号未闭合、指代/连接词跨界 | `OPEN_QUOTE` / `COREFERENCE_RISK` | 强负向 |
| 300–500 字目标 | `TARGET_SIZE` | 软约束 |
| 超过硬上限 | `HARD_MAX_SIZE` | 强制切分；优先最近合法句界 |
| 小于硬下限 | `UNDER_MIN_SIZE` | 倾向 merge，但不得跨章节 |

锁定三段策略：`confidence >= 0.75` 执行规则结论；`0.40 <= confidence < 0.75` 才调用 LLM；`confidence < 0.40` 表示规则证据冲突或信息不足，LLM 可以裁决，但任何 `abstain`/失败都采用保守规则回退。confidence 是版本化规则分数，不宣称为统计概率。章节边界、非法 offset、硬大小安全边界永不交给 LLM 覆盖。

### 层级语义

- `chapter`：现有章节的不可跨越根节点，绑定 `chapter_id` 和 source snapshot。
- `scene`：连续 evidence spans 的容器；允许场景略超目标大小，但必须受版本化 hard max 约束。scene 文本只能由其 children 按 offset 拼接。
- `evidence`：检索与引用的最小原文块，通常 300–500 字；每个 evidence 只属于一个 scene，必须能由 source slice 精确重建。
- 原始 deterministic baseline chunks 保留为 A 组和证据底座；hierarchical candidate 是 B 组。不得因 B 组上线删除 A 组 lineage。

## Provenance、Manifest 与版本

每次 build 冻结：`owner_id, novel_id, source_snapshot_id/hash, normalized_text_hash, chapter hashes, chunker_version, rule_config_hash, schema_hash, prompt_hash/version, model provider/id/revision, decoding, dependency health, parent_build_id, build_mode(full|incremental), sorted node/edge manifest, manifest_checksum`。

每个节点保存 source 坐标（建议 Unicode code-point offsets，并显式记录 `offset_unit="unicode_codepoint"`）、`source_start/end`、`text_hash`、父节点 ID、顺序、reason codes、rule confidence、decision source（rule/llm/fallback）和 decision audit ID。读取时必须以 snapshot 原文重新切片核验 hash；normalized offset 与 source offset 若不同，必须有可逆 offset map，不能只保存清洗后坐标。

稳定 ID 由作用域、snapshot/content hash、层级、offset 和 chunker major version确定；显示顺序使用独立 ordinal，避免前文插入导致全章身份漂移。manifest 排序后 hash，candidate 一经完成不可修改。

## Candidate A/B、增量重切与回滚

- A：当前 active chunker/build；B：新 candidate。两者读取同一冻结 source snapshot 和同一 frozen eval cases。
- candidate build 与 active pointer 分事务；只有 manifest/reconcile 完整且 Phase 06 返回 `passed|qualified` 才可 prepare promotion。commit 必须比较 expected old pointer、防 stale write，并写 journal。
- 增量输入以 chapter content hash 和规则/prompt/schema/model config hash 决定。正文变更只重切受影响章节；但边界上下文窗口触及相邻章节时也纳入 affected set。全局规则/schema major 变化默认 full rebuild。
- 新 candidate 必须携带未受影响节点，而非原地修改 active collection；完成 exact-ID reconcile 后才可评测。
- rollback 指向已验证的 previous build/checksum；不得重新运行 LLM。rollback 后 reconcile active pointer、manifest 与实际索引，失败则保持阻断状态并报警。

## 故障与降级矩阵

| 故障 | 行为 | 是否可发布 |
|---|---|---|
| LLM timeout/outage/rate limit | 最多 1 次有界重试；保存错误类别；使用规则 fallback | 仅当 fallback 指标和整体质量门通过 |
| JSON/schema/refusal 无效 | 原始输出仅审计；最多 1 次带验证错误的修复重试；仍失败则 fallback | 同上 |
| offset/hash/context preserve 不合法 | 不重试语义解释，直接拒绝该输出并 fallback | 同上 |
| 预算预测超限 | 调用前不发请求，未处理边界全部 fallback，状态 `budget_fallback` | 仅当成本门和质量门通过 |
| 运行中达到 hard budget | 停止后续调用，已完成决定保留，剩余 fallback | 仅当报告完整、可比较且通过 |
| DB/索引/reconcile/manifest 失败 | candidate `blocked_dependency|invalid_manifest`，active 不变 | 否 |
| Phase 06 输入、baseline 或 lineage 缺失 | `quality_comparable=false`, metrics=null | 否 |

fallback 必须是版本化、可复现的规则决定，并记录 `fallback_reason`；严禁自动切换未记录模型后继续声称与 baseline 可比较。

## 质量门与成本安全

建议锁定首版门槛，后续只能通过版本化 policy 和新 baseline 修改：

| 维度 | 首版门槛 |
|---|---|
| schema/offset/text hash/parent-child integrity | 100%；任何失败阻断 |
| chapter coverage | 原文非空字符覆盖 100%，重复覆盖 0，跨章节点 0 |
| hard max violation / orphan evidence | 0 / 0 |
| frozen boundary F1（split 类） | B >= 0.90 且相对 A 不回归 |
| boundary precision / recall | 各自报告；critical false split（引号、实体指代、事件证据被断裂）= 0 |
| scene purity / coherence | 人工金标或经校准 judge；平均 >= 4/5，且无 critical 失败 |
| retrieval | Recall@5 相对 A 回归 <= 2pp；MRR/NDCG 各自不回归 > 3pp |
| answer faithfulness | 复用 Phase 06：critical unsupported claim rate = 0；95% LB >= policy |
| fallback | 总率报告；outage/budget/schema 分开；资格运行不得隐藏 fallback |
| latency/cost | p95 和总 token/cost 均不超过 policy；相对 A 成本 <= +15% |

成本在调用前以 `low_confidence_boundary_count × max_attempts × (max_input_tokens + max_output_tokens) × versioned price` 计算 worst case。默认每个边界最多 2 次总尝试、输出上限 180 tokens；并发受 semaphore 限制。对本地 Ollama，货币成本可记 0，但 token、wall time、GPU/CPU profile 仍必须记录，不能用 `$0` 绕过资源门。LiteLLM 报价/usage 仅作测量输入；版本化价格表和原始 usage 同时落审计，因为历史 issue 显示 provider/stream/custom pricing 曾出现不一致。

## 主要风险

1. **LLM 越权改写正文或制造事实**：schema 不包含自由文本正文；只接受枚举决定和来自输入 span 的 offset；脚本重新切片。
2. **局部边界最优破坏全局大小/层级约束**：LLM 后必须跑 deterministic normalization，强制 chapter、coverage、hard max 和 parent-child invariants。
3. **prompt injection**：原文放入明确的数据区，system prompt 声明其内指令无效；无工具、无 DB 凭据、无网络能力；输出 strict schema。
4. **provider structured-output 差异**：即使 `response_format` 被声明支持，也始终本地 Pydantic 二次校验并保留 deterministic fallback。
5. **offset 漂移**：规范化前后维护 offset map；hash 从 source slice 重算；禁止按“搜索相同文本”恢复位置。
6. **candidate 半发布或并发覆盖**：复用 Phase 05 immutable build、prepare/commit journal、expected pointer 与 reconcile。
7. **评测污染**：dev 调阈值，frozen set 只做资格评测；prompt/model/rule/schema 改动产生新 lineage。
8. **成本失控**：只调用低置信边界；预检 hard budget、每 call token ceiling/timeout、单边界幂等键和停止开关。

## 评测与测试策略（设计，不在本阶段运行）

- 参考集从 10–20 个高质量章节起步，覆盖中文对话、未闭合引号、多人物快速轮换、时间/地点跳转、回忆嵌套、超长段、极短段、无标点、重复文本和 prompt injection 文本。
- 边界金标允许 `split|merge|acceptable_either`，避免把合理歧义伪装成唯一答案；critical 边界单独标注。
- code-based：schema、reason enum、offset/hash、coverage/no-overlap、树完整性、checksum/idempotency、budget、状态转换、pointer CAS、reconcile、rollback。
- transcript contract：固定 LLM JSON/timeout/refusal/malformed/over-budget 响应，不调用模型，验证 retry/fallback 和 raw-output 非真值。
- integration：真实 PostgreSQL/Chroma 的 candidate 隔离、并发 promotion、增量 carry-forward、删除传播、rollback/reconcile；live 模型只在 nightly qualification。
- product eval：同 snapshot 的 A/B boundary F1、scene coherence、检索 Recall/MRR/NDCG、答案 faithfulness、fallback、tokens/cost/p95 latency；最终由 Phase 06 deterministic arbiter 决定。
- LLM judge 只评 scene coherence 等主观维度，必须先与人工标注校准（相关性 >= 0.7，critical false accept = 0）；结构、offset、发布和成本绝不交给 judge。

## 资料来源

- LiteLLM JSON mode / structured outputs: https://docs.litellm.ai/docs/completion/json_mode
- LiteLLM async streaming/completion: https://docs.litellm.ai/docs/completion/stream
- LiteLLM retries: https://docs.litellm.ai/docs/completion/reliable_completions
- LiteLLM exception mapping: https://docs.litellm.ai/docs/exception_mapping
- LiteLLM custom cost callback: https://docs.litellm.ai/docs/observability/custom_callback
- Pydantic conversion/provider pitfall: https://github.com/BerriAI/litellm/issues/6848
- Structured streaming parse limitation: https://github.com/BerriAI/litellm/issues/7501
- Ollama JSON mode transformation pitfall: https://github.com/BerriAI/litellm/issues/7355
- Cost calculation inconsistency: https://github.com/BerriAI/litellm/issues/4965

