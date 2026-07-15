# Phase 10: Reader Selection AI and Multi-Session Conversations — Specification

**Created:** 2026-07-13  
**Domain:** fiction only  
**Requirements:** REQ-CHAT-01..07  
**Status:** locked for planning

## Goal

读者可在阅读页选择原文，通过不遮挡正文的可收起小窗，在同一本小说的多个持久会话中进行 owner-scoped、证据受限、默认防剧透且可审计的 AI 对话。

## Baseline

- 阅读页当前按约 3,500 字分页并以纯文本节点渲染正文，没有选区到章节绝对 offset 的契约，也没有聊天入口。
- `Novel.reading_progress` 是服务端剧透边界；Phase 08 已将 `timeline_full_book` 作为唯一明确的 per-novel 全书披露开关。
- Phase 07/08 已提供 chapter/source offsets、evidence refs、版本 lineage、服务端可见集裁剪和持久模型调用/预算/取消模式。
- Phase 04 已验证“LLM 只做语义、脚本拥有证据包/门控/写库”的边界。
- 当前没有 conversation/message/context manifest/chat job 表或 API；不得假设 provider 保存远程会话状态。
- Phase 09 尚未实现。Phase 10 只依赖其已锁定的只读公共输出：versioned、evidence-bound、spoiler-filtered relationship observations。

## Falsifiable Requirements

### REQ-CHAT-01 — Immutable selection and visible-context evidence

每条用户消息必须在同一事务中固化：`chapter_id`、以当前 `Chapter.content` 为基准的 `[source_start, source_end)` Unicode code-point offsets、选中文本与 SHA-256、解析出的 evidence refs、阅读进度快照、全书开关快照和不可变 visible-context manifest。

**Pass:** 服务端重新切片正文与选中文本逐字符一致；所有 manifest evidence 同 owner/novel、未超过可见章节 cutoff、绑定 source/hierarchy/version lineage；刷新后可重放相同 selection 与 manifest。  
**Fail:** 只保存浏览器选中文本、只保存 DOM offset、offset/hash 不匹配仍入库、重试时用新的阅读进度或新的上下文替换原 manifest。

### REQ-CHAT-02 — Multiple durable owner-scoped conversations

每本小说支持创建、重命名、切换、归档、恢复归档和删除多个会话；消息有稳定单调序号和幂等 client message key。

**Pass:** 两个并发发送得到唯一顺序；刷新/重启后会话、消息、引用和当前选择可重放；跨 owner 的 novel/conversation/message/job ID 一律返回 404；删除先取消活动 job，再事务级删除会话内容。  
**Fail:** 每书只有单会话、会话只在浏览器内、跨会话消息串线、归档会话仍可发送、删除留下可查询正文或调用数据。

### REQ-CHAT-03 — Server-enforced spoiler boundary

默认上下文严格按持久阅读进度裁剪；无有效进度时沿用 Phase 08 的第一章 cutoff；只有既有 `timeline_full_book=true` 的同 owner/novel 明确开关可扩展到全书。浏览器参数不能单独扩大范围。

**Pass:** future chapter text、event、relationship observation、evidence ID、计数、标题、错误和 citation 均不进入 manifest、prompt 或响应；显式全书开关关闭后新消息立即恢复 cutoff。  
**Fail:** 先检索全书再在前端过滤、接受 `full_book=true` 请求参数而不核对持久偏好、通过引用或统计泄露未读章节。

### REQ-CHAT-04 — Answer-only AI boundary

模型仅可产生 evidence-bound answer blocks、澄清问题、不确定性声明和结构化 suggestion candidates。它不得写 timeline、relationship 或 clue 事实；本阶段没有 accept/apply/write endpoint。任何 suggestion 均固定 `requires_explicit_confirmation=true`，仅作为聊天数据保存。

**Pass:** 模型 transport 没有 DB/tool/filesystem 能力；worker 只能写 chat tables；代码扫描证明 chat 模块不调用 timeline/relationship/clue mutation service；候选接口无确认或应用路由。  
**Fail:** 模型输出直接更新领域表、聊天消息被投影为事实、存在隐式 auto-apply、Phase 11 从聊天读取事实。

### REQ-CHAT-05 — Durable auditable generation jobs and budgets

每次生成使用 PostgreSQL 持久 job、调用 attempt、双 scope（conversation + novel）预算 ledger/reservation、冻结 deployment/prompt/schema/context lineage、usage/cost、状态原因、cancel/retry 记录和响应 hash。

**Pass:** 重启从持久状态恢复；调用前原子预留两级预算；未知价格/超预算在网络调用前 fail closed；取消后不发布迟到响应；重试复用原消息与 manifest；调用 lineage 可审计。  
**Fail:** HTTP 请求等待完整模型调用、内存计数预算、provider 失败自动切换模型、取消后仍写 assistant message、依赖失败伪装成功。

### REQ-CHAT-06 — Reader selection entry and collapsible window

阅读页选区出现轻量入口，打开后为可收起聊天小窗；桌面不覆盖正文主列，390px 移动端可选择、提问、切换会话、取消、查看引用并收起后继续阅读。

**Pass:** 选区入口与 selection 绑定；桌面正文宽度/滚动保持可用；移动小窗最大高度受限且可折叠；加载、空态、预算暂停、依赖失败、取消、归档和删除均有明确状态；citation 可跳转章节与 source offset。  
**Fail:** 全屏替换阅读器、固定层遮住正文且不可收起、仅鼠标可用、引用无法定位、消息发送时必须保持选区 DOM 存活。

### REQ-CHAT-07 — Automated release evidence

发布门必须同时覆盖 unit、PostgreSQL integration、API contract、adversarial、frontend component 和真实 desktop/mobile browser 链路。

**Pass:** 自动证明 Unicode/分页 offsets、owner IDOR、spoiler side channels、prompt injection、伪造 refs、并发顺序、幂等发送、预算竞态、取消迟到响应、重启恢复、强制引用、无证据不确定性、会话 CRUD 和移动选区流程；真实浏览器只控制 provider transport。  
**Fail:** 仅 mock route 的浏览器测试、仅 happy path、没有 PostgreSQL 并发验证、未检查聊天不能成为领域事实。

## In Scope

- PostgreSQL conversation/message/selection/context manifest/citation/job/call/budget contracts与迁移
- owner-scoped 会话和消息 API
- DOM selection 到章节绝对 code-point offset 的确定性映射和服务端复验
- 只读 evidence retrieval：selection、Phase 07 hierarchy、已确认 knowledge/timeline、Phase 09 relationship observations
- 服务端剧透裁剪、不可变 manifest、strict cited answer schema
- 持久 job、预算、取消、重试、失败恢复和审计
- 阅读页轻量入口、可收起小窗、多会话管理、引用跳转、desktop/mobile UX
- fiction-only fixtures 与完整自动化发布门

## Out of Scope

- 历史文本或历史 ontology
- Phase 09 人物关系图 UI 或关系计算
- Phase 11 线索/伏笔发现、状态机或 UI
- 从聊天直接或间接写 timeline、人物关系、线索或其他分析事实
- provider remote conversation/thread state
- LangChain、LangGraph、agent tools 或新编排框架
- 恢复已移除的历史文本支持

## Acceptance Checklist

- [ ] REQ-CHAT-01..07 每条均有自动化命令和负向断言。
- [ ] 所有消息和调用均 owner/novel/conversation scoped；跨 owner 统一 404。
- [ ] 模型输入只包含当前选区、当前 manifest 允许的 evidence 和非事实性对话 framing。
- [ ] 每个 answer block 至少一个有效 manifest citation；无 evidence 时没有事实性 answer block。
- [ ] 既有 `timeline_full_book` 是唯一全书扩展授权。
- [ ] Phase 09 文件未被 Phase 10 修改；关系观察只经只读 consumer contract 使用。
- [ ] Phase 11 只读取已确认 domain structures，不读取聊天作为事实源。
- [ ] 真实 desktop 与 390px browser 测试通过，正文始终可继续阅读。

