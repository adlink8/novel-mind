# Phase 10: Reader Selection AI and Multi-Session Conversations — Context

**Gathered:** 2026-07-13  
**Status:** ready for planning  
**Scope:** fiction only

<domain>
## Phase Boundary

交付阅读器选区到多会话 AI 对话的完整垂直能力。聊天是证据受限的解释界面，不是新的小说事实来源，也不是时间线、人物关系或线索的写入入口。
</domain>

<spec_lock>
## Locked Requirements

`10-SPEC.md` 中 REQ-CHAT-01..07 的 pass/fail 条件全部锁定。规划与执行不得把服务端 owner/spoiler/evidence 门缩减为前端行为，不得把持久 job/预算/审计缩减为同步调用或内存状态。
</spec_lock>

<decisions>
## Locked Decisions

- **D-01 — Reader entry:** 阅读页选中文本后显示轻量入口；打开可收起小窗，不遮挡正文，桌面和手机均可用。
- **D-02 — Conversation lifecycle:** 每本小说有多个持久 owner-scoped 会话，支持创建、重命名、切换、归档、恢复归档和删除。
- **D-03 — Immutable user-message snapshot:** 每条用户消息固化 selection 的 chapter/source offsets/evidence refs、阅读进度快照和 visible-context manifest；重试不得替换这些快照。
- **D-04 — Server spoiler authority:** 防剧透默认在服务端执行；只有同 owner/novel 已持久化的既有明确全书开关可扩展上下文。
- **D-05 — Evidence-only answer:** 模型只能依据选区和当前可见检索证据；回答强制引用，证据不足时明确不确定，禁止补全或虚构。
- **D-06 — No domain writes:** LLM 不能写 timeline、人物、关系或线索事实。结构化修改建议仅预留 candidate contract，必须标记需显式确认；本阶段不实现确认或写入。
- **D-07 — Existing AI stack:** 使用现有模型层、预算、取消和持久 job 模式；不引入 LangChain/LangGraph/agent tools，不假设 OpenAI 或其他 provider 保存 remote conversation state。
- **D-08 — PostgreSQL authority:** 持久对话与上下文以 PostgreSQL 为事实源；消息、call lineage、usage/cost、重试和取消必须可审计。
- **D-09 — Validation breadth:** 计划必须包含迁移、API contract、unit/integration/adversarial/browser 验证、预算、隐私和 owner 边界。
- **D-10 — Phase 09 read-only dependency:** Phase 09 未开始实现，但其公共依赖固定为 versioned/evidence-bound/spoiler-filtered relationship observations；Phase 10 不等待规划、不修改 Phase 09 文件，只经只读 consumer contract 使用。
- **D-11 — Phase 11 fact boundary:** Phase 11 只读取已确认的 timeline/relationship/clue domain structures；聊天消息、回答、suggestion candidates 和 context manifests 永不作为事实输入。
- **D-12 — Product scope:** 仅 fiction；不恢复历史文本，不提前实现线索追踪或人物关系图 UI。

## the agent's Discretion

- 会话删除采用事务级硬删除：先标记/取消活动 generation jobs，再级联删除消息、manifest、citation、attempt 与预算数据；删除后正文和 prompt lineage 不再可查询。审计要求适用于资源存在期间，不凌驾于用户删除和隐私边界。
- 会话“切换”由列表 + 读取目标会话完成，不增加跨设备全局 current-conversation 指针；可持久化 `last_opened_at` 仅用于排序。
- AI 回答使用 strict `answer_blocks[]`（每块非空 evidence refs）而非自由文本后置猜测引用；对话历史只作非事实性 framing。
- 轮询持久 job 状态，不增加 SSE/WebSocket；现有 30 秒 API timeout 不承载完整模型调用。
- numeric budget defaults 配置化；conversation 和 novel 两级 ledger 均在调用前按 worst-case 原子预留。
</decisions>

<canonical_refs>
## Canonical References

- `.planning/REQUIREMENTS.md` — REQ-CHAT-01..07
- `.planning/ROADMAP.md` — Phase 10 goal/dependencies
- `.planning/phases/08-versioned-novel-analysis-orchestration-and-interactive-timel/08-{SPEC,CONTEXT,AI-SPEC,VERIFICATION}.md`
- `.planning/phases/04-llm/04-{CONTEXT,RESEARCH,AI-SPEC,VERIFICATION}.md`
- `frontend/src/app/novels/[id]/page.tsx` — reader ownership of chapter/progress state
- `frontend/src/components/reader/reader-content.tsx` — current pagination and text rendering
- `backend/app/api/auth.py`, `backend/app/api/novels.py` — authentication and owner-scoped novel routes
- `backend/app/services/ai_service.py`, `backend/app/services/ai_router.py` — current AI entry/routing
- `backend/app/services/timeline/{query,evidence,model_gateway,jobs,budget}.py` — spoiler/evidence/job/call patterns
- `backend/app/services/knowledge/evidence.py` — bounded evidence package pattern
</canonical_refs>

<existing_contracts>
## Existing Contracts to Reuse

- `require_owned_novel` and `require_user`: inaccessible novel-scoped resources return 404.
- `Novel.reading_progress.chapter_id`: default reading cutoff source.
- `Novel.reading_progress.timeline_full_book`: only persisted full-book disclosure authority.
- `ChunkHierarchyNode.source_start/source_end/content_hash`: source-span evidence authority.
- Timeline query: visible-set-first filtering, first-chapter fallback, no edge/aggregate leakage.
- Timeline gateway: frozen deployment, pre-call reservation, one explicit repair, no transparent provider fallback, late outcome audit.
- Phase 04 evidence package: only supplied evidence IDs are citeable; recall signals are not facts.
</existing_contracts>

<integration_contracts>
## Phase 09 and Phase 11 Boundaries

Phase 10 defines a consumer-facing `RelationshipObservationReader` protocol with inputs `owner_id`, `novel_id`, `max_chapter_number`, `active_version_id` and bounded query terms. Its output must contain immutable observation ID, version lineage, effective narrative interval, evidence refs and already spoiler-filtered excerpts. The production adapter is enabled only when Phase 09 exposes that contract; Phase 10 never imports Phase 09 ORM tables or edits Phase 09 files. Context assembly still rechecks owner, novel, version and cutoff.

Phase 11 may reference confirmed domain rows created by dedicated timeline/relationship/clue pipelines. It must not import reader-chat models/services, scan chat messages, use assistant citations as evidence, or convert suggestion candidates into facts. A later explicit-confirmation workflow would need its own phase and audit contract.
</integration_contracts>

<deferred>
## Deferred Ideas / Explicit Non-Goals

- Applying suggestion candidates to any domain table
- Phase 09 relationship graph visualization or editing
- Phase 11 clue discovery/lifecycle/UI
- Historical corpus support
- Remote provider thread/conversation IDs as authority
- Agent tool execution, web browsing, filesystem access or autonomous actions from chat
</deferred>

