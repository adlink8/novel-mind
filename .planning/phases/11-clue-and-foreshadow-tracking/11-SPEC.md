# Phase 11: Clue and Foreshadow Tracking — Specification

**Created:** 2026-07-13  
**Domain:** fiction only  
**Requirements:** REQ-CLUE-01..07  
**Depends on:** Phase 04, Phase 07, Phase 08; Phase 09/10 contracts are defined but their implementations are absent

## Goal

用户可以在分析工作台中查看、筛选、核验证据化的线索与伏笔，沿追加式五状态生命周期观察其首现、强化、回收或驳回；系统默认严格按阅读进度防剧透，重分析产生可比较版本且不会覆盖人工决定。

## Hard Boundary

Phase 11 仅支持小说。它不恢复历史语料支持，不实现小说写作/续写，不修改人物关系图或聊天业务，也不把剧情摘要、章节摘要、主题、节奏等分析中间件暴露为新菜单。

Phase 09/10 尚未实施。Phase 11 只能定义和消费以下可选只读协议：

- 版本化人物关系观察引用：仅作为带 owner、novel、version、evidence refs 的关联信号。
- 只读会话来源：只能提供选区/引用标识作为候选召回信号；聊天文本和模型回答不能成为线索事实或生命周期证据。
- Phase 09 relationship observation public reader 缺失或运行不可用时，关系派生信号必须显式记录为 `source_unavailable`，不得伪装成“零关系”。Phase 07/08 的独立证据链仍可工作；聊天始终不是线索来源。

## Falsifiable Requirements

### REQ-CLUE-01 — Script-built cross-chapter candidates and bounded LLM judgment

脚本从已冻结的 Phase 07 chapter → scene → evidence 层级及 Phase 08 时间事件构造跨章节候选和证据包。LLM 只输出 strict schema 的语义候选/判定，不拥有召回、阈值、冲突、状态、版本、写库、预算、工具或发布权限。

Acceptance:

- 同一输入 lineage 重放产生相同候选 ID、证据包 hash 和排序。
- 向量相似度、BM25、邻近、人物共现、关系观察和会话引用只能增加召回信号；单独存在时生成零个 active/reinforced/paid_off 状态。
- 任一模型输出引用包外 evidence ID、错误 owner/novel/chapter、错误 offset/hash、非法 enum 或 extra field 时，该输出不能产生可见状态。
- 没有可验证早期 cue 的候选不能进入 active；没有新的后续证据不能进入 reinforced；没有独立后期 payoff 证据不能进入 paid_off。

### REQ-CLUE-02 — Append-only five-state lifecycle

每条逻辑线索的状态只由追加式生命周期事件表达。允许的机器或人工转换为：

- candidate → active
- candidate → dismissed
- active → reinforced
- active → dismissed
- reinforced → reinforced（仅在新增且不同的强化证据时追加事件）
- reinforced → paid_off
- reinforced → dismissed

paid_off 和 dismissed 为终态；禁止更新、删除或改写既有生命周期事件来伪造当前状态。

Acceptance:

- 数据库拒绝 active → candidate、paid_off → reinforced、dismissed → active 等非法转换。
- 重放生命周期日志得到的当前状态与 API 返回状态一致。
- active、reinforced、paid_off 每个状态事件都有至少一个有效 evidence ref；paid_off 同时绑定早期 cue 与更晚 payoff，且 payoff 的章节/offset 严格晚于 cue。
- 连续强化只能引用此前生命周期未使用的新证据；重复证据不会产生新事件。

### REQ-CLUE-03 — Evidence-only links to people, relationship observations and timeline events

线索可关联人物、Phase 08 时间事件和未来 Phase 09 版本化关系观察。关联是证据化引用，不是被关联对象的写入或事实提升。

Acceptance:

- 每个 link 只允许一种 target kind，并保存 owner、novel、source version、target reference、supporting evidence refs 和 validation status。
- 人物或时间事件 link 必须通过当前数据库 owner/novel/version 检查；未来关系观察通过只读 source protocol 检查。
- Phase 09 source 不可用时，关系观察 link 保持 unresolved/不可发布，不创建占位关系表或图边。
- 聊天消息、LLM 回答、向量相似度和未证据化关系观察永远不能作为 lifecycle evidence。

### REQ-CLUE-04 — Server-side spoiler protection

线索 API 在任何摘要、计数、筛选项、证据、关联和回收链计算之前，先按持久化阅读进度裁剪可见集合。全书披露只复用 Phase 08 的 per-novel timeline_full_book 明确开关。

Acceptance:

- 默认查询不会返回阅读进度之后的 cue、reinforcement、payoff、标题、计数、人物筛选值、link 或状态暗示。
- 无有效阅读进度时只允许第一章；无章节时返回空集合。
- request_full_book=true 但 Phase 08 开关未持久化时仍按默认 cutoff 返回。
- 一条 paid_off 线索在用户尚未读到 payoff 时不得以 paid_off、回收链长度或未来证据数量泄露结局，只显示截止点可推导的状态。

### REQ-CLUE-05 — Protected human overrides and comparable reanalysis

用户可以确认、驳回、注释和调整关联。人工动作写入追加式 override/audit；重分析创建新候选版本和比较结果，不覆盖或静默重映射人工决定。

Acceptance:

- confirm 将 candidate 追加为 active；reject 将非终态追加为 dismissed；annotation 不改变状态；link adjustment 追加 superseding override。
- 同字段再次人工操作保留 supersedes 链，不更新旧行。
- 重分析产生新的 version ID、manifest checksum 和 machine diff；旧版本仍可查询。
- 只有唯一稳定 evidence identity 映射才能自动 relink override；零个或多个匹配时标记 needs_relink。
- 任一新机器版本都不能删除、降级或覆盖人工确认、驳回、注释或关联调整。

### REQ-CLUE-06 — Analysis-workspace clue surface

前端继续使用全局 /analysis 工作台和现有小说选择器。线索视图包含线索时间带、状态/人物筛选、证据面板、回收链、版本对比和人工动作；不新增摘要中间件菜单或独立顶级导航。

Acceptance:

- 用户在 /analysis 内切换“时间线/线索与伏笔”，不会触发新的顶级路由。
- 线索时间带按叙事章节与 source offset 稳定排序，paid_off 链明确连接早期 cue、强化和后期 payoff。
- 键盘可操作列表与可视时间带展示同一可见线索集合。
- 证据面板支持跳转原文章节，并提供确认、驳回、注释和关联调整。
- 1440×900 与 390×844 浏览器验证无横向页面溢出、未来内容泄露或不可访问控制。

### REQ-CLUE-07 — Frozen, adversarial, operational and release qualification

发布资格由冻结小说 fixture、假阳性/剧透/跨版本对抗、成本/延迟、真实 API、双 viewport 浏览器和 fail-closed release gate 共同决定。

Acceptance:

- 冻结 fixture 至少覆盖 24 个标注样本：真实伏笔、未回收 motif、重复物件、预告/强化/回收、假因果、别名、跨章、无 payoff 和冲突版本。
- adversarial 集中 critical false active/paid_off、包外证据、spoiler leak、override overwrite、跨 owner link 均为 0。
- paid_off precision ≥ 0.90，active/reinforced macro F1 ≥ 0.85；关键无证据接受为 0。
- 每次 qualification 绑定 source/hierarchy/timeline/prompt/schema/model/config/fixture/policy hashes，并报告调用、tokens、费用与 p50/p95 latency。
- API qualification p95 ≤ 500 ms（本地冻结数据）；模型 stage p95 ≤ 60 s 且总费用不超过冻结 policy。
- live 依赖阻塞或 metrics 缺失时 quality_comparable=false，release gate 必须失败。
- release CLI 自行执行固定命令、校验输出 digest 并独立读取 PostgreSQL authority；调用方不能注入成功结果。

## Scope

### In scope

- fiction-only clue/foreshadow contracts
- deterministic cross-chapter recall and evidence packages
- strict LLM semantic judgment
- schema/evidence/threshold/conflict gates
- immutable analysis versions, exact cache, budget, restartable worker and active pointer
- append-only lifecycle and protected overrides
- evidence-only links to character, timeline event and optional relationship observation
- spoiler-safe owner-scoped API
- analysis-workspace clue UI
- frozen/adversarial/API/browser/live qualification and release gate

### Out of scope

- history corpora, history prompts or history fixtures
- relationship graph implementation or mutation
- chat/session implementation or mutation
- writing, continuation or fanfiction generation
- summary/theme/pace/plot middleware menus
- semantic cache, similarity-as-fact, chat-as-fact
- modifying Phase 09/10 planning artifacts

## Requirement-to-Evidence Matrix

| Requirement | Primary observable proof |
|---|---|
| REQ-CLUE-01 | deterministic candidate/package replay + schema/evidence gate tests |
| REQ-CLUE-02 | PostgreSQL lifecycle transition/replay tests |
| REQ-CLUE-03 | typed link scope tests + null Phase 09/10 adapter tests |
| REQ-CLUE-04 | API spoiler/property tests and browser default/full-book flow |
| REQ-CLUE-05 | override supersession/relink/version comparison tests |
| REQ-CLUE-06 | component tests + desktop/mobile real browser journey |
| REQ-CLUE-07 | signed qualification report + executable release gate |

---

*Phase: 11-clue-and-foreshadow-tracking*
