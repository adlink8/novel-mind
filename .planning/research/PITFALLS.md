# Pitfalls Research

**Domain:** NovelMind v0.8 分层叙事记忆与层级 RAG（单书纵向 MVP）  
**Researched:** 2026-07-15  
**Confidence:** HIGH（项目内 lineage、pointer、owner、spoiler 与预算风险）；MEDIUM（自动摘要/评测器对具体小说数据的失效率，必须由冻结 fixture 实测）

## Risk Model

本里程碑把一个已验证的 `chapter → scene → evidence` 底座扩展成 `ChapterState → StoryArc → GlobalStoryModel`。这会增加两类系统性风险：

1. **语义风险随层级放大。** 抽象摘要可能产生事实不一致；如果下一层只读取上一层自由文本，错误会成为新的“输入事实”。关于“逐层放大”的判断是基于 pipeline 结构与摘要事实一致性研究作出的工程推论，不是声称现有论文已经测得 NovelMind 的具体放大率。
2. **控制面风险跨版本扩散。** source snapshot、owner、spoiler cutoff、active pointer 或评测 authority 任一错配，都会让一棵结构完整的树承载错误来源或越权内容。

因此，v0.8 的安全顺序必须是：**先审计 source/owner/version → 再生成 claim → 每层验证 derivation → 最后评测；任何失败都停留在 immutable candidate，绝不通过“高层摘要看起来合理”获得资格。**

### Severity convention

- **P0:** 可能泄露其他 owner/未来内容，或把未验证候选切成 active。
- **P1:** 破坏事实可信度、证据链、source consistency 或造成失控付费。
- **P2:** 性能、可维护性或产品解释性显著退化，但不会直接污染 authority。

## Critical Pitfalls

### Pitfall 1（P1）：逐层摘要漂移与错误放大

**What goes wrong:**  
ChapterState 把人物、因果或时间写错；StoryArc 再把这个错误概括为“阶段性转折”；GlobalStoryModel 最后形成内部一致但与原文不符的全书结论。越高层文本越流畅，越容易掩盖错误起点。

**Why it happens:**

- abstractive summarization 本身存在事实不一致问题；DeFacto 等研究将“摘要只能包含输入支持的信息”作为需要专门纠正的目标，而不是默认性质。
- builder 把上一层 `summary` 当作权威输入，而不是把 child claims 和 leaf evidence 当作有待重新验证的候选。
- 单一 node-level citation 覆盖多条结论，无法定位哪条 claim 无支持。
- repair prompt 只让模型“改得更合理”，却未重新绑定 source evidence。

**How to avoid:**

- 上层输入使用 typed child claims + bounded evidence excerpts；自由文本仅作展示，不作唯一事实输入。
- 每条 claim 独立保存 support refs、confidence、narrative interval、spoiler max chapter 和 derivation checksum。
- 每升一层执行 deterministic entailment prerequisites：支持集合非空、scope/hash/offset 有效、冲突未被静默合并；失败 claim 保留 `unsupported|conflicted|unknown`，不得自动补全。
- 评测按 level 和 claim kind 分桶；不能只评最终回答流畅度。

**Warning signs:**

- 层级越高 unsupported claim rate、entity substitution、时间/因果冲突率越高。
- Global claim 找不到唯一或足够明确的 child claims，只引用整个 arc。
- 同一问题从 raw evidence 回答与从 Global 回答结论相反，但高层得分仍高。
- repair 次数随层级增加，或 repair 后 support refs 不变而文字大幅变化。

**Recovery:**  
冻结候选并标记 `blocked_semantic_drift`；从首个失败层开始重建，不覆盖低层合格资产。先重新验证/重建受影响 ChapterState，再重建包含它的 arc 与 Global；保留旧候选供 diff，禁止用只改文字的 post-edit 宣称修复。

**Phase to address:** B（上层 contract/provenance）预防；C（bottom-up builder）执行；F（qualification）做分层 drift adversarial。

---

### Pitfall 2（P1）：证据链在中间层断裂或被“宽泛引用”伪装

**What goes wrong:**  
Global/Arc 节点有 `evidence_ids` 字段，但这些 ID 不属于对应 child claim、已被重建删除、offset/hash 不匹配，或只证明相邻事实而不证明该结论。系统表面“有引用”，实际无法从 claim 沿父子关系解析到原文。

**Why it happens:**

- 只在 node 层挂一组 evidence，而不是 claim-level derivation。
- 将相似度命中、timeline/relationship/clue ID 或 LLM rationale 当作事实支持。
- 在序列化/迁移时只保存 excerpt，丢失 evidence ID、chapter、offset、content hash。
- verifier 只检查 FK 存在，不检查 source snapshot、语义作用域与完整路径。

**How to avoid:**

- 使用 W3C PROV 式 `derived_from` 思路：claim → direct child claim/evidence → leaf evidence，路径每一跳都带版本与 checksum。
- promotion-readiness 前从新鲜 PostgreSQL 重走每条路径，重算 content hash、offset、owner/novel/snapshot；任何断链都硬失败。
- timeline/relationship/clue 只能作为版本化 enrichments，其自身 evidence 必须继续解析到 Phase 07 leaf；聊天、相似度、模型解释永不进入 support 集合。
- API 最终 citation 只返回 leaf evidence，高层节点只作为 routing trace。

**Warning signs:**

- `citation_count > 0`，但 `resolved_leaf_count = 0` 或两者差距持续扩大。
- 多条不同 claims 总是引用同一大 evidence 集合。
- carry-forward 后 node checksum 未变，但 child/evidence manifest 已变化。
- evaluator 能评分摘要，却无法输出逐 claim 的 supporting span。

**Recovery:**  
停止该候选的查询资格，运行 provenance repair audit；唯一稳定 evidence identity 可重绑，否则标记 `needs_rebuild`。从断链最早节点向上失效；不要猜测最近文本或用向量近邻自动重绑。

**Phase to address:** B 建模；D（retrieval/evidence resolver）端到端验证；F 将 100% claim→leaf 可解析设为硬门。

---

### Pitfall 3（P1）：source snapshot / hierarchy / domain version 混用

**What goes wrong:**  
ChapterState 来自 hierarchy build A，StoryArc 混入 timeline version B 或 relationship/clue 的旧 evidence；各条数据单独合法，但组合后不属于同一小说快照。重试时又读取最新 active，形成同一 candidate 内部 lineage 漂移。

**Why it happens:**

- 只按 `novel_id` 查询“最新数据”，未冻结 source_snapshot_hash、hierarchy_build_id/checksum 和可选 domain versions。
- cache key 省略 prompt/schema/model/config 或输入 manifest。
- optional source outage 被当作 healthy empty，重试后悄悄补入新版本。
- active pointer 在长任务期间变化，worker 每阶段重新解析 active。

**How to avoid:**

- dry-run 开始时冻结完整 input manifest；每个 checkpoint、model call、node、cache entry 都绑定它。
- 审计按资产/版本给资格，不允许“同 novel 即兼容”；所有 evidence 必须属于冻结 hierarchy/snapshot。
- retry 只 rehydrate 原 manifest；若 source 已不可读取则暂停，而不是切到最新 active。
- optional sources 明确区分 `available_empty`、`source_unavailable`、`lineage_mismatch`，后两者不能产生负事实。

**Warning signs:**

- 一个 candidate 中出现多个 source_snapshot_hash/hierarchy_checksum。
- cache hit 发生在 prompt/schema/model/config 或 input manifest 变化后。
- 同一 build 重试前后节点数量、domain enrichment 数或费用变化，但 build ID 不变。
- optional source 从 unavailable 变为 empty，却没有新的 candidate/version。

**Recovery:**  
隔离整个混合-lineage candidate，禁止逐行“洗白”；回到冻结 manifest 重跑受影响层。若无法重建原输入，创建新 candidate version 并保留旧版本为 failed audit artifact。

**Phase to address:** A（asset audit/eligibility）首先阻断；C 冻结运行 manifest；F 做 mid-run pointer-change/cache adversarial。

---

### Pitfall 4（P0）：高层元数据与 coarse-to-fine 路由泄露剧透

**What goes wrong:**  
正文 evidence 被 cutoff 过滤了，但 arc 标题“最终背叛”、Global claim、future node count、筛选项、路由分数、空缺提示或 trace 暗示未来内容。另一种错误是先检索全书节点再在回答阶段过滤，模型已经看到了剧透。

**Why it happens:**

- 把 spoiler 当 UI 展示过滤，而不是 candidate-set 和 context assembly 的服务端边界。
- 高层节点横跨 cutoff，开发者只按 `start_chapter` 判断可见。
- running candidate 或 full-book eval path 被误复用于普通 reader query。
- 聚合、reranking、cache 在 cutoff 之前执行。

**How to avoid:**

- 在节点召回、分数、计数、聚合、trace 和模型 context 之前解析 persisted cutoff。
- 节点可见性按 claim 的 support evidence/max chapter；跨 cutoff arc 只能投影截止点可推导的 claims，不能展示完整 arc summary/title。
- 缺失/非法进度只允许第一章；full-book 只复用既有 per-novel persisted `timeline_full_book` 明确开关。
- cache key 包含 owner、novel、version、cutoff/full-book policy；response envelope 由 visible set 派生。

**Warning signs:**

- cutoff 前后返回相同 Global/Arc 文本，仅 evidence 数不同。
- 日志显示先取全书 top-k 再 filter。
- 未来章节的 node ID/title 出现在 trace、available filters、counts 或 cache hit metadata。
- 无 reading progress 时结果超过第一章。

**Recovery:**  
按 P0 事件处理：立即禁用上层 query path、回退 Phase 07 evidence baseline，清理受影响共享 cache，审计日志确认 owner/cutoff 范围；修复后用 future-title/count/trace/cache adversarial 全量复验。

**Phase to address:** D 在 query architecture 阻断；F 做 API/property/adversarial 验证；任何后续消费者 cutover 再独立复验。

---

### Pitfall 5（P0）：owner / novel / version 越界与共享缓存泄漏

**What goes wrong:**  
调用者提供另一个用户的 version/node/evidence ID，服务只检查 ID 存在就返回；或 cache/vector result 未包含 tenant scope，把 A 用户小说的高层摘要作为 B 用户候选。即使小说文本相同，也不能跨 owner 复用私有分析状态。

**Why it happens:**

- 在 root novel 处鉴权一次，子查询/derivation expansion 未重复 owner+novel+version scope。
- node ID 或 content hash 被误认为全局授权凭证。
- 缓存 key、向量 metadata、background job resume token 缺少 owner/novel。
- 显式历史 version path 绕过 active/running 可访问性证明。

**How to avoid:**

- 每个 root/child/evidence 查询同时约束 owner_id、novel_id、candidate_version_id；不可访问资源统一 404。
- 服务端证明显式 version 属于当前 owner/novel 且状态允许；永不信任客户端 evidence IDs。
- cache key 与 retrieval metadata 包含 tenant scope；禁止跨 tenant semantic cache。
- qualification 注入 IDOR、跨 owner 相同 hash、跨 novel 相同 chapter number 与伪造 parent ID。

**Warning signs:**

- repository/service 方法只接收 `node_id` 或 `version_id`，没有 owner/novel。
- SQL child expansion 通过 parent_id 单条件查询。
- cache hit 日志缺 owner/novel/version/cutoff。
- 403/200 行为暴露资源是否存在，而现有项目约定是 404。

**Recovery:**  
立即禁用受影响 endpoint/cache，清除可能跨 tenant 的 materialization；查询审计日志界定泄漏，轮换/废弃不安全 cache namespace。修复所有 traversal 层的复合 scope 后执行 PostgreSQL IDOR 集成测试。

**Phase to address:** B 在数据/API contract 固定 scope；D 在 traversal/retrieval 落实；F 用真实 PostgreSQL adversarial 阻断。

---

### Pitfall 6（P1）：错误 invalidation 范围——过小污染、过大重跑

**What goes wrong:**  
改动一个 evidence 后只重建 ChapterState，却 carry-forward 仍引用旧 claim 的 StoryArc/Global，产生 stale 上层结论；反过来，每次小改都整书重建，失去增量价值并放大成本。

**Why it happens:**

- 依赖只以 chapter number 或时间戳表达，缺少明确 `derived_from` 和输入 checksum。
- 认为 arc 边界天然稳定；实际上章节变化可能让相邻 arc 合并/拆分并影响后续 ordinal。
- 将“内容相同”与“lineage 相同”混为一谈。
- dirty-set 算法只覆盖直接 parent，未做传递闭包或边界影响分析。

**How to avoid:**

- 持久保存每节点 direct inputs、input manifest checksum 与依赖边；dirty set 取反向传递闭包。
- ChapterState 可精确局部；包含该章的 arc 和 Global 必须失效。若 arc segmentation 输入/边界改变，保守扩大到相邻/后续受影响 arcs。
- carry-forward 要求 node bytes/checksum、direct-input checksums、policy lineage 全部一致。
- 用删除、插入、章节重排、offset 变化、仅 metadata 变化等 fixture 验证 invalidation oracle。

**Warning signs:**

- source hash 变化而 Global checksum 不变，且没有证明受影响 claim 集为空。
- rebuild report 只有“重建 N 个节点”，没有 why/dirty path/carry-forward proof。
- 插入章节后 arc ordinal 变化，但后续 arcs 仍被 carry-forward。
- 每次 dry-run model calls 近似全量，即使只有一个 chapter 变化。

**Recovery:**  
停止有疑问候选；重新计算 dependency graph 和 dirty closure。对历史缺少 direct-input manifest 的节点一律扩大重建范围，不猜测。保留旧 candidate，用 diff 证明 stale 引用清零后再恢复资格。

**Phase to address:** B 定义 derivation；E（local invalidation/rebuild）实现和 property test；F 检查成本与 dirty-set 正确性。

---

### Pitfall 7（P1）：递归 fan-out、repair 和重复 embedding 导致成本爆炸

**What goes wrong:**  
章节、arc、global 每层都对所有 child 多轮摘要、judge、repair、embedding；失败重试又重新读取最新输入。单书 dry-run 的调用数呈层数×节点×repair 放大，预算耗尽后留下无法解释的半成品。

**Why it happens:**

- 未在调用前用节点数和 worst-case tokens 估算总预算。
- “每层最多一次 repair”在多节点下仍是大倍数。
- cache key 不精确导致漏命中，或为省事完全禁用 carry-forward。
- global query 对每次请求都运行 map-reduce，而不是复用合格候选。
- optional enrichment 每个 claim 单独查询/调用，产生 N+1。

**How to avoid:**

- 审计后、调用前生成 cost plan：各层 node count、call upper bound、input/output token upper bound、repair reserve、embedding 与 query cost。
- novel 和 run 双预算 ledger，worst-case 原子预留；unknown pricing、任一级余额不足均零 provider call。
- exact cache 只缓存 evidence-valid complete output；相同 lineage carry-forward；provider_retries=0，repair 显式且独立预留。
- 批量加载 optional domain assets，限制 evidence package 和 fan-out；MVP 不做在线 recursive map-reduce UI。

**Warning signs:**

- 实际 calls 超过预估 upper bound，或 repair rate/平均 input tokens 随层级上升。
- cache hit ratio 接近 0，单章未变仍触发模型调用。
- 大量 `paused_budget` 出现在 Global 阶段，前面费用已花完。
- cost report 只有总额，没有 layer/node/cache/repair 分解和 avoided cost。

**Recovery:**  
立即暂停 worker，保留 durable checkpoints 和已结算 attempts；禁止“加预算后从头来”。缩小 evidence package/fan-out 或修复 cache lineage后从首个未完成 stage 恢复；若输入 contract 改变，创建新 candidate，旧费用单独记账。

**Phase to address:** A 审计提供规模；C 在 worker/budget/cache 预防；D 限制 query fan-out；F 对 upper-bound、cost completeness 和 restart 做资格门。

---

### Pitfall 8（P0）：dry-run 或并发任务误切 active pointer

**What goes wrong:**  
候选构建完成或“评测通过”后，通用 promotion helper 自动移动 active；或者两个并发资格任务用 stale revision 覆盖彼此。现有 timeline/relationship/clue/chat 随后读到未批准新层或错误版本。

**Why it happens:**

- 把 `qualified`、`prepared`、`active` 合并成一个状态或在事务外写 pointer。
- dry-run 复用 production promotion API，只有布尔参数区分。
- verifier 读取调用方提交的成功报告，没有重新验证 manifest/DB authority。
- pointer update 未 row lock/CAS，或 manifest 校验与更新不在同一事务。

**How to avoid:**

- v0.8 根本不提供可达的 commit promotion 路径；dry-run verdict 仅为 `qualified_candidate|blocked`。
- 独立 candidate 表/namespace，现有 consumer 不查询；测试对所有现有 pointer 做 before/after snapshot。
- 未来 promotion 必须沿用 timeline 模式：重新计算 immutable manifest、owner/novel scope、row lock、expected revision CAS、append-only journal、事务回滚。
- release CLI 自行执行固定命令并从新鲜 PostgreSQL 读取 authority；self-hash 或调用者布尔值不能授权。

**Warning signs:**

- dry-run code import/call `commit_promotion`、更新 `is_candidate=False` 或写 active pointer model。
- 构建测试只断言候选存在，没有断言所有 pointer/revision 未变。
- qualification 成功即触发 consumer cache invalidation/索引 alias 切换。
- pointer journal 缺 from/to/expected/resulting revision 或 manifest。

**Recovery:**  
立即按 pointer journal 回滚到已知合格版本并冻结写入口；核对是否有 consumer 读取污染版本并清除派生 cache。若 journal/manifest 不完整，不做“猜测回滚”，从数据库备份与独立 authority 审计恢复。

**Phase to address:** A/B 固定 candidate-only 状态机；C dry-run 实现不可达 promotion；F 用 write spy、并发/CAS 与 pointer byte-identical 检查阻断。

---

### Pitfall 9（P1）：评测假阳性与“自己证明自己”

**What goes wrong:**  
层级方案在少量合成问题或同模型 judge 上胜出，于是被标记 qualified；但正确 evidence 没被召回、答案只是措辞像 reference，或评测 fixture 被开发过程看过并过拟合。现有 v0.3 仅 10/100 confirmed、指标为 0 的缺口也可能被一份单书报告错误“关闭”。

**Why it happens:**

- 只测 end-to-end answer score，不分 retriever/router/generator/provenance。
- 用生成候选的同模型/同 prompt 家族作唯一 judge；LLM judge 存在位置偏差等系统偏差。
- baseline 与 hierarchical 使用不同 snapshot、cutoff、预算或问题集。
- 缺失 metrics 被 0、空列表或 cached success 替代；release verifier 信任 self-reported digests。
- 在 frozen test 上调阈值/提示，造成测试污染。

**How to avoid:**

- 采用 RAGChecker/RAGAS 启发的模块化指标：route hit、leaf evidence recall/ranking、context precision、claim faithfulness、answer relevance、拒答、provenance、spoiler、cost/latency分别报告。
- hard gates 用 deterministic/PostgreSQL checks，不交给 LLM judge；judge 只处理语义质量，并做顺序交换/重复稳定性或独立仲裁。
- baseline 与 candidate 同 source snapshot、cutoff、fixture、预算口径；local/arc/global/no-answer/spoiler 分桶，禁止只报 macro average。
- dev fixture 与 frozen qualification fixture 分离；policy/threshold 在查看 frozen 结果前固化 hash。
- verifier 自行执行固定命令、校验输出 digest并新鲜读取 authority；metrics null、依赖 blocked、样本未 confirmed 均 fail closed。

**Warning signs:**

- candidate “胜出”但 leaf recall 没提升或为 0，只有 judge score 上升。
- 交换 A/B 展示顺序后 judge 结论翻转。
- 单书 dry-run 报告声称修复全项目 v0.3 评测缺口。
- report 缺 per-bucket 样本数、confidence/variance、失败样本和调用成本。
- frozen fixture/policy 在结果不佳后被同一提交修改。

**Recovery:**  
撤销 qualification（候选仍保留但状态 blocked），冻结现有输出用于误差分析；补充/确认 gold evidence 和 failure taxonomy，重新校准 judge，再用未见 fixture 重跑。不得通过删除难题或放宽 hard gates 恢复绿色。

**Phase to address:** F 为主；A 在报告中显式继承 v0.3 gap；D 提供可诊断 routing trace。

---

### Pitfall 10（P1）：上层自由文本不可审计、不可失效、不可纠错

**What goes wrong:**  
数据库只存一段 chapter/arc/global summary 和少数 metadata。之后无法知道一句话对应哪些 evidence、哪个字段改变、是否包含剧透，也不能只失效受影响 claim；人工修正只能覆盖整段文本。

**Why it happens:**

- 为快速 demo 直接复用 chat completion 文本。
- schema 只验证 JSON 外壳，不约束 claims、deltas、unknown、support refs 和 source interval。
- 把“自然语言更灵活”当成不建领域 contract 的理由。
- 将 reasoning/rationale 持久化后误当 authority。

**How to avoid:**

- strict schema 以 typed claims、state deltas、entities、intervals、support refs、unknown/conflict 为权威；rendered summary 是可重建 projection。
- LLM extra fields、包外 IDs、错误 offsets/hash、非法 enum 全部拒绝；无事实支持时输出空/unknown。
- checksum 覆盖结构化 canonical form；diff、invalidation、评测和未来 override 都针对 claim identity。
- 不存或不使用自由 rationale 作为事实；用户可见说明从结构化 claims + citations 渲染。

**Warning signs:**

- schema 核心字段是 `summary: str`，support refs 仅在 node 顶层。
- 修改一个人物状态导致整段 summary 无法稳定 diff。
- evaluator 需要再次让 LLM 从 summary 抽 claims 才能检查事实。
- 同一输入重跑只因措辞变化产生全树 churn/cache miss。

**Recovery:**  
不要为旧自由文本反向猜测 claim lineage。保留为 non-authoritative legacy artifact，从其原始 frozen child/evidence 重新生成 strict candidate；没有原始 manifest 的文本不能迁移为 qualified。

**Phase to address:** B 必须在任何上层生成前解决；C 只接受 strict output；F 做 extra-field/free-text-only/unstable rerun adversarial。

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|---|---|---|---|
| node-level 一组 citations | schema 简单 | claim 无法独立验证、失效或纠错 | 仅可作为 UI 聚合，不可作为 authority |
| 直接读取“当前 active”而不冻结 manifest | 少传参数 | 长任务和重试混入新 snapshot/version | Never |
| `source_unavailable` 当 empty | 下游分支少 | 把未知误报为“没有关系/线索” | Never |
| cache key 只含 query/node ID | 命中率看似高 | 跨 owner/version/cutoff 污染 | Never |
| dry-run 复用 promotion endpoint | 少写一个入口 | 误切 active 的 P0 风险 | Never；最多复用纯 manifest validator |
| 只存 rendered summary | 快速 demo | 不可审计、不可精确 diff/invalidate | Never for qualified nodes |
| 用最终 LLM judge 总分作 release | 报告简单 | 掩盖 retrieval、provenance、spoiler 失败和 judge bias | Never |
| 小改也整书重跑 | 实现容易 | 成本/延迟不可控，无法证明复用价值 | 仅在审计表明 lineage 全局失效时 |
| 为省成本跳过 leaf resolver | 查询更快 | 高层文本直接成为“证据” | Never |
| MVP 在线查询实时构建 global map-reduce | 展示直观 | 延迟、费用、取消与一致性复杂度暴涨 | Never in v0.8 |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|---|---|---|
| Phase 07 hierarchy | 只查 build_id，不重算 active/immutable/manifest/tree | 审计 unique active pointer、snapshot、manifest、coverage、offset/hash；不合格在 provider call 前阻塞 |
| Phase 08 timeline | 把 running candidate 与 active 混合补全 | 冻结明确 version；只读 evidence-valid rows；source/hierarchy lineage 不一致则 unavailable |
| Phase 09 relationship | outage 返回空关系并写入“无变化” | 保留 `source_unavailable|lineage_mismatch|available_empty` 三态；只用 accepted observations + evidence |
| Phase 11 clue | 把 lifecycle current state 或 payoff 当静态摘要 | 以冻结 version 重放 lifecycle；cue/payoff evidence 都需解析且按 cutoff 投影 |
| Phase 10 chat | 用聊天回答/引用提升 ChapterState | 永不作为事实输入；聊天只允许未来消费 qualified 上层记忆 |
| LiteLLM/provider | SDK 自动 retry/fallback 到不同模型 | 固定 deployment、provider_retries=0、显式一次 repair、每次调用前独立预算预留 |
| PostgreSQL jobs | checkpoint 只存 stage 名 | 同时冻结 input manifest/checksum；resume 时验证一致，否则暂停/新建 candidate |
| Vector/BM25 | top-k metadata 未带 tenant/version/cutoff | SQL/metadata 双重 scope，服务端复核每个 evidence；相似度仅召回 |
| Existing promotion helpers | 直接调用 chunk/timeline commit | v0.8 只复用纯校验/CAS设计知识；dry-run 不可到达 pointer mutation |
| Evaluation CLI | 调用方注入 metrics/digest/success | CLI 固定命令，自行捕获输出，fresh DB observer，缺失即 blocked |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|---|---|---|---|
| 每层全量 fan-out | calls/tokens 随节点数与层数乘法增长 | build 前 upper-bound、bounded packages、batch enrichment、exact cache | 长篇章节数上升或 repair rate > 0 时立即放大；不设虚构固定阈值 |
| N+1 derivation resolution | 每个 claim/child/evidence 单独 SQL | 批量按 version + IDs 取数，预载 adjacency，仍逐项 scope/hash 校验 | claims×层数进入数千时 p95 急升 |
| 查询时递归 map-reduce | global query 秒级/分钟级且重复付费 | 预构建 candidate；query 仅 route/expand/rank，限定 top-k/fan-out | 任意交互式 reader path 都不可接受 |
| cutoff 后过滤 | 先扫描/embedding 全书再丢弃 | SQL/query candidate set 先按 visible claim/evidence 过滤 | 既是性能浪费也是 P0 剧透风险 |
| 过细 claim 粒度 | 节点/边/embedding 爆炸、摘要无压缩收益 | 冻结 typed delta 粒度和每节点上限，合并只合并同一支持语义 | 单章 claims 接近原文句数时已失去价值 |
| 过粗 arc/global | context 短但召回定位差、宽泛 citations | 连续 arc、claim-level support、leaf rerank | 多条独立主线/人物并行时最明显 |
| cache checksum 不稳定 | 相同语义因字段顺序/措辞每次 miss | canonical JSON、结构化 claims、稳定排序/IDs | 第二次同 lineage dry-run仍大量调用即已失败 |
| invalidation 过大 | 单章变化接近全量成本 | dependency closure + boundary-aware conservative scope | 增量变更频繁时不可持续 |

## Security Mistakes

| Mistake | Risk | Prevention |
|---|---|---|
| client version/node/evidence ID 当授权 | P0 跨 owner 数据泄漏 | 每跳 owner+novel+version scope，服务端证明可访问状态，不可访问统一 404 |
| tenant-agnostic semantic/cache key | P0 相同文本/hash 跨用户泄漏 | cache/vector namespace 与 key 包含 owner/novel/version/cutoff；禁跨 tenant cache |
| 先全书检索后 spoiler filter | P0 模型/context/log 已看到未来 | visible candidate set first，聚合/trace/cache 都在 cutoff 后 |
| 把 retrieved text 当指令 | prompt injection 影响 builder/judge | evidence 作为带 ID 的数据字段，固定 system contract，无 tools/DB/network，strict schema |
| 将 model rationale/log 原样暴露 | 泄漏原文、prompt、未来或内部配置 | 只暴露清洗后的 reason codes/leaf citations；日志按 owner 与敏感字段策略 |
| qualification artifact 含全文或密钥 | CI/report 泄漏私有小说/provider secret | 仅存 hashes、IDs、度量与最小脱敏 excerpt；永不记录 token/key |

OWASP RAG Security Cheat Sheet 特别指出多租户向量存储需要防止跨边界泄漏，并把被检索内容引发的 prompt injection 视为 RAG 特有攻击面；这里必须覆盖上层 node cache 与 derivation traversal，而不仅是底层向量库。

## UX Pitfalls

本 MVP 不新增用户界面，但下游产品化时要避免：

| Pitfall | User Impact | Better Approach |
|---|---|---|
| 把 Global summary 展示成确定事实 | 用户无法区分模型综合与原文 | 每条结论可展开到 chapter/evidence；显示 unknown/conflict |
| 高层标题剧透 | 未读用户在导航即被泄露 | 基于 visible claims 生成截止点投影，不显示 future arc title/count |
| “分析完成”掩盖部分失败 | 用户误信缺失内容是“没有发生” | 区分 complete、partial、source_unavailable、blocked 与 candidate |
| citation 点开落不到准确原文 | 证据感变成装饰 | 跳转 chapter + source offsets，校验 content hash |
| 自动切换检索策略无解释 | 回答质量变化不可预测 | 保留 baseline fallback 和可诊断 routing trace，产品默认可简化展示 |
| 将 dry-run 报告当产品上线 | 用户路径无回滚保障 | 先独立 consumer qualification，再 opt-in cutover |

## "Looks Done But Isn't" Checklist

- [ ] **资产审计：** 有资产数量不等于可复用；验证 source snapshot、manifest、tree、offset/hash、owner 与 active 唯一性，且审计零 provider call。
- [ ] **ChapterState：** 有 JSON 不等于可审计；逐 claim 验证 typed delta、support refs、unknown/conflict 和 canonical checksum。
- [ ] **StoryArc：** 有章节范围不等于连续/稳定；验证无 gap/overlap、边界变化的 dirty propagation。
- [ ] **GlobalStoryModel：** 有流畅摘要不等于可信；验证每条 claim 可下钻 leaf，不能只引用 arc summary。
- [ ] **coarse-to-fine：** 能返回答案不等于真的分层检索；检查 routing trace 与各层候选，最终 citations 仅 leaf evidence。
- [ ] **spoiler：** evidence 被裁剪不等于安全；检查 title/count/filter/score/trace/cache/context 均无未来信息。
- [ ] **owner scope：** root novel 鉴权不等于 traversal 安全；对 parent/child/evidence/cache/history version 做 IDOR。
- [ ] **incremental rebuild：** calls 变少不等于正确；用 oracle 验证 dirty closure，检查未变 carry-forward checksums 和 stale refs 为 0。
- [ ] **budget：** 有总费用不等于可控；验证 pre-call reservation、unknown pricing 零调用、repair/cache/avoided-cost 分解。
- [ ] **dry-run：** candidate 合格不等于可上线；比较所有现有 pointer/revision/consumer outputs before/after 均不变。
- [ ] **evaluation：** judge 说好不等于 release-ready；分开 retrieval/generation/provenance、安全与成本，并做 judge 顺序稳定性。
- [ ] **release authority：** report 有 hash 不等于独立；verifier 必须固定命令、内部捕获 digest、fresh PostgreSQL authority、metrics 非空。
- [ ] **v0.3 gap：** 单书 fixture 通过不等于关闭 100 confirmed/faithfulness/cost 的全项目缺口；报告必须明确范围。

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---|---:|---|
| 摘要漂移/错误放大 | MEDIUM–HIGH | 冻结候选；定位首个失败 claim/layer；重建该 ChapterState 与上游依赖 arc/global；保留 diff |
| 证据断链 | MEDIUM | provenance audit；唯一 identity 才 relink，否则从断链节点重建；候选保持 blocked |
| snapshot/version 混用 | HIGH | 整个 candidate 隔离；按原 frozen manifest 重跑，无法复现则创建新 version |
| spoiler 泄漏 | HIGH/P0 | 禁用上层 query、回退 evidence baseline、清 cache、界定日志影响、对抗复验 |
| owner 越界 | HIGH/P0 | 下线 endpoint/cache、范围调查、废弃 cache namespace、全 traversal 复合 scope + IDOR 复验 |
| invalidation 过小 | MEDIUM–HIGH | 重算 dependency closure；旧节点无 manifest 则扩大重建；清 stale refs |
| invalidation 过大/成本爆炸 | MEDIUM | 从 checkpoint 暂停；修 cache/粒度/fan-out；按冻结输入恢复，不从头盲跑 |
| active pointer 误切 | HIGH/P0 | 依 journal/CAS 回滚已知合格版本；冻结写入口；清 consumer cache；审计污染窗口 |
| 评测假阳性 | MEDIUM | 撤 qualification；锁定失败输出；补 gold/failure taxonomy；独立 judge/未见 fixture 重跑 |
| 自由文本 legacy | HIGH | 降级为 non-authoritative artifact；从 frozen evidence 重新生成 strict claims，不反向猜 lineage |

## Pitfall-to-Phase Mapping

这里沿用 `FEATURES.md` 的建议交付顺序，供 ROADMAP 规划时映射成正式 phase：

| Pitfall | Prevention Phase | Verification |
|---|---|---|
| snapshot/version 混用 | **A. Audit & eligibility** | per-asset reason codes；单 candidate lineage cardinality=1；mid-run active change 不影响 frozen manifest |
| owner scope 基础缺失 | **A + B** | audit/query contract 都要求 owner+novel+version；跨 owner same-hash fixtures |
| 自由文本不可审计 | **B. Contracts & provenance** | strict schema 拒绝 summary-only、extra fields、包外 refs；canonical replay checksum 稳定 |
| 证据断链 | **B + D** | 100% claim→leaf traversal，hash/offset/snapshot 重算；断链 hard fail |
| 摘要漂移/错误放大 | **B + C. Bottom-up builder** | 分层 unsupported/conflict/drift 指标；首层错误不会被上层 accepted |
| 成本爆炸 | **C** | preflight upper bound、双预算、unknown price zero-call、checkpoint resume、cache hit/avoided cost |
| active pointer 误切 | **C + F** | dry-run pointer/revision byte-identical before/after；promotion imports/write spies 为空 |
| spoiler 元数据泄漏 | **D. Retrieval & evidence** | first-chapter default；future title/count/score/trace/cache/context adversarial 为 0 |
| owner traversal/cache 越界 | **D** | real PostgreSQL IDOR；每跳 scope；tenant cache namespace tests |
| invalidation 过小/过大 | **E. Local rebuild** | change oracle 覆盖 edit/insert/delete/reorder/boundary；dirty closure 与 carry-forward proof |
| 评测假阳性 | **F. Single-book qualification** | same-source baseline、per-bucket metrics、judge swap stability、fresh DB fixed-command verifier |
| 全项目 gap 被错误关闭 | **F** | report 明确 single-book scope，并原样列出 v0.3 100-confirmed/faithfulness/cost residual |

## Top Three Risks

1. **逐层摘要漂移 + 自由文本 authority（P1）：** 最难被肉眼发现，因为上层通常更连贯；必须通过 claim-level contract 和逐层 derivation gate 在生成前解决。
2. **snapshot/evidence lineage 断裂或混用（P1）：** 会生成结构完整但来源错误的“可信假象”；必须在任何 provider call 前审计，并在资格时从新鲜 DB 全链重算。
3. **spoiler/owner/pointer 控制面越界（P0）：** 发生概率可通过既有模式降低，但后果最大；v0.8 必须 candidate-only、visible-set-first、复合 scope，并对所有现有 pointer 做零变化证明。

## Sources

### External primary/official sources

- Liu et al., **On Improving Summarization Factual Consistency from Natural Language Feedback**, ACL 2023. https://aclanthology.org/2023.acl-long.844/ — abstractive summary 需要明确事实一致性约束与纠错，而非天然可靠。
- Tang et al., **Understanding Factual Errors in Summarization: Errors, Summarizers, Datasets, Error Detectors**, ACL 2023. https://aclanthology.org/2023.acl-long.650/ — 不同 summarizer/error type 下事实错误与 detector 表现具有差异，支持分层/分类型诊断而非单总分。
- Sarthi et al., **RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval**, ICLR 2024. https://openreview.net/forum?id=GN921JHCRw — 递归构建不同抽象层并跨层检索的直接架构依据；本研究据此识别生成层级间传播风险。
- W3C, **PROV-DM: The PROV Data Model**. https://www.w3.org/TR/prov-dm/ — entity/activity/derivation/collection 的权威溯源模型，支持 node/claim/evidence 派生链。
- Microsoft, **GraphRAG Getting Started**. https://microsoft.github.io/graphrag/get_started/ — 官方建议先用教程数据与便宜模型实验，避免直接承担大索引成本。
- Microsoft, **GraphRAG Query Engine Overview**. https://microsoft.github.io/graphrag/query/overview/ — local/global/basic/DRIFT 的成本与上下文差异，支持保留 raw baseline 与限制 global fan-out。
- Ru et al., **RAGChecker: A Fine-grained Framework for Diagnosing Retrieval-Augmented Generation**, NeurIPS 2024 Datasets and Benchmarks. https://proceedings.neurips.cc/paper_files/paper/2024/hash/27245589131d17368cccdfa990cbf16e-Abstract-Datasets_and_Benchmarks_Track.html — retrieval 与 generation 分模块诊断，反对仅用 end-to-end 总分。
- Es et al., **RAGAS: Automated Evaluation of Retrieval Augmented Generation**, EACL 2024. https://aclanthology.org/2024.eacl-demo.16/ — context relevance/recall、faithfulness 与 answer relevance 分离评测。
- Shi et al., **Judging the Judges: A Systematic Study of Position Bias in LLM-as-a-Judge**, IJCNLP-AACL 2025. https://aclanthology.org/2025.ijcnlp-long.18/ — LLM judge 存在系统性位置偏差，支持交换顺序/重复稳定性与 deterministic hard gates。
- OWASP, **RAG Security Cheat Sheet**. https://cheatsheetseries.owasp.org/cheatsheets/RAG_Security_Cheat_Sheet.html — RAG prompt injection、多租户向量/检索边界与跨域数据泄漏风险。
- NIST, **Artificial Intelligence Risk Management Framework: Generative Artificial Intelligence Profile (NIST AI 600-1)**. https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf — provenance、pre-deployment testing 与 incident disclosure 的风险治理依据。

### Local authoritative sources

- `.planning/PROJECT.md`、`.planning/STATE.md`、`.planning/ROADMAP.md` — v0.8 active requirements、既有 gaps 与 Phase 07–11 决策。
- `.planning/research/FEATURES.md` — MVP feature boundary、复用资格、delivery order 与 hard gates。
- `backend/app/services/chunking/promotion.py` — qualified-only validation、source/manifest checks 与 prepare/commit separation 的既有模式。
- `backend/app/services/timeline/promotion.py` — owner/novel scope、immutable manifest recheck、row lock、expected-revision CAS、pointer journal 与 rollback 模式。
- `backend/app/services/timeline/query.py`、`backend/app/services/relationships/query.py`、`backend/app/services/clues/query.py` — visible-set-first、version isolation 与 owner/novel scoped query 先例。
- `backend/app/services/reader_chat/retrieval.py`、`worker.py` — frozen context manifest、server cutoff、evidence recheck、budget/checkpoint 先例；聊天不是事实源。
- `.planning/phases/07-semantic-hierarchical-chunking/07-CONTEXT.md` 与 Phase 08–11 CONTEXT/SPEC — raw fallback、candidate isolation、source_unavailable、append-only、spoiler、qualification 和 no-domain-write 边界。

---
*Pitfalls research for: NovelMind v0.8 分层叙事记忆与层级 RAG纵向 MVP*  
*Risk posture: fail closed, candidate only, claim-level provenance, visible set first, fresh-authority qualification.*
