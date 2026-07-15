# Phase 10 Research: Reader Selection AI and Multi-Session Conversations

**Research date:** 2026-07-13  
**Discovery level:** Level 0/1, local contract verification  
**Confidence:** HIGH for repository patterns; MEDIUM for Phase 09 adapter naming because implementation does not yet exist

## Executive Conclusion

不需要新框架或依赖。最小正确架构是：阅读器确定性生成 selection coordinates，FastAPI 在 owner/novel/chapter 边界重新验证正文与 offsets，PostgreSQL 原子固化 user message + selection + progress snapshot + visible context manifest，再由持久 generation job 使用现有 AI 层和 Phase 08 的预算/取消/调用审计模式生成 strict cited blocks。服务端先裁剪可见集合再检索、组包和调用模型；聊天永远不是领域事实源。

## Local Evidence

| Area | Current evidence | Planning implication |
|---|---|---|
| Reader | `reader-content.tsx` 将章节切成约 3,500 JS code-unit 页面，正文是单个 `whitespace-pre-wrap` text node | 选区工具必须记录 page base，并把 DOM UTF-16 index 转为 Python-compatible Unicode code-point offset |
| Progress | reader 同步 `Novel.reading_progress`; timeline query 无进度时只开放第一章 | chat 复用同一 cutoff，不创建第二套 spoiler preference |
| Full book | `timeline_full_book` 已持久化到 `Novel.reading_progress` | chat 请求参数不能扩大可见范围，只读取该明确开关 |
| Owner scope | novel routes use `require_owned_novel`; Phase 08 APIs use owner+novel filters and 404 | conversation/message/job 的每次 lookup 必须从 owned novel 向下 scope |
| Evidence | hierarchy/timeline evidence 保留 `source_start/source_end/content_hash` | selection 与 retrieval refs 可做 offset/hash/scope fail-closed validation |
| AI | `ai_service.chat`, `ai_router.route_task`; Phase 08 adds frozen deployment, persistent reservation and attempts | 增加 `reader_chat` balanced task type；job 创建后冻结 deployment，失败不透明切换 provider |
| Persistence | Phase 08 has `AnalysisRun`, call attempts, budget reservations, cancel polling | 建 chat-specific tables，复用状态机/事务模式，不把 chat FK 塞入 timeline run tables |
| Frontend API | centralized `frontend/src/lib/api.ts`, Axios credentials and 30s timeout | send endpoint 返回 202/job；UI 轮询，不在 HTTP 内等待生成完成 |
| Phase 09 | implementation absent during planning; public output shape locked by user | 现在定义只读 consumer boundary；Phase 10 执行时必须绑定已完成的 Phase 09 public reader，运行期暂时不可用可记录为 evidence-source outage，但不得用空实现冒充集成或伪造关系数据 |

## Standard Stack

- FastAPI async router + SQLAlchemy 2 async + Pydantic v2 strict schemas
- PostgreSQL 16 + Alembic as sole conversation/context authority
- Existing LiteLLM/Vertex-compatible `ai_service` and `ai_router`
- Existing Next.js App Router, React, Tailwind, Lucide and Axios
- pytest/pytest-asyncio, Vitest/Testing Library and Playwright desktop/mobile
- No package installs; no LangChain, LangGraph, agent SDK, remote thread API or new state store

## Architecture Pattern

```text
DOM Selection
  -> {chapter_id, selected_text, code-point [start,end), chapter_hash}
  -> POST user message (owned novel + conversation)
  -> server re-slices Chapter.content and resolves active hierarchy evidence
  -> compute persisted spoiler cutoff from reading_progress/timeline_full_book
  -> visible-set-first retrieval (selection + hierarchy + confirmed timeline/knowledge + Phase09 observations)
  -> atomic UserMessage + Selection + ContextManifest + EvidenceRefs + GenerationJob
  -> worker: lock job -> reserve conversation+novel budgets -> frozen model call
  -> strict AnswerEnvelope local validation -> citations must belong to manifest
  -> atomic AssistantMessage + Citation rows + job terminal state
  -> reader polls and renders cited blocks; citation jumps to chapter/source offset
```

## Offset Contract

### Canonical definition

- `source_start` inclusive and `source_end` exclusive.
- Units are Unicode code points in the exact persisted `Chapter.content`, matching Python slicing.
- `selection_text = Chapter.content[source_start:source_end]` must be byte-for-byte equal after UTF-8 encoding; no trimming, whitespace collapse or Unicode normalization occurs.
- Persist `chapter_content_hash`, `selection_text_hash`, and active hierarchy build lineage.

### Browser conversion

The current page splitter uses JavaScript UTF-16 indices. Refactor it to return `{text, sourceStartUtf16}`. Walk text nodes under the reader article to convert `Range.startOffset/endOffset` to page-local UTF-16 offsets, add page base, then convert each prefix using `Array.from(prefix).length` to code-point indices. Send selected text and hashes as stale-selection evidence, never as server authority.

### Server validation

Load chapter through owned novel, compute SHA-256 of exact content, check bounds/non-empty/max selection length, slice by code point, compare exact selected text/hash, and reject stale/malformed selections with stable 409/422 error codes. Resolve every overlapping hierarchy evidence node and persist immutable refs. A client cannot supply authoritative evidence IDs.

## Persistence Contract

Suggested normalized tables in one migration:

| Table | Purpose / key invariants |
|---|---|
| `reader_conversations` | owner+novel scope, title, active/archived, `next_sequence`, timestamps |
| `reader_messages` | conversation sequence, user/assistant role, body, reply linkage, unique client idempotency key |
| `reader_message_selections` | one per user message; chapter, offsets, exact text/hash, chapter/hierarchy lineage |
| `reader_context_manifests` | one immutable manifest per user message; progress/full-book/cutoff/version/prompt inputs/checksum |
| `reader_context_evidence_refs` | allowlisted evidence keys, source type/id, chapter/offset/hash/version, bounded excerpt |
| `reader_message_citations` | assistant message/block -> manifest evidence ref |
| `reader_generation_jobs` | durable state, lease, cancel/retry, frozen deployment/prompt/schema/context lineage |
| `reader_model_call_attempts` | reservation/request/response hashes, provider request ID, usage/cost/latency/error |
| `reader_budget_ledgers` / `reader_budget_reservations` | row-lockable conversation and novel scopes; worst-case reserve + settle |

Use PostgreSQL constraints for role/sequence/status/scope, partial unique indexes for one nonterminal job per user message, and ordered row locking for dual ledger reservation. No chat table becomes a parent or source FK for timeline/relationship/clue domain facts.

## API Contract

Prefix: `/api/novels/{novel_id}/conversations`.

- `GET /` list active/archived conversations with last message metadata only.
- `POST /` create conversation.
- `PATCH /{conversation_id}` rename or active/archive transition.
- `DELETE /{conversation_id}` cancel nonterminal jobs and hard-delete the conversation graph transactionally.
- `GET /{conversation_id}/messages?after_sequence=` replay messages, citations, selection summaries and job state.
- `POST /{conversation_id}/messages` validate/commit user message + immutable manifest + queued job; return 202.
- `GET /{conversation_id}/jobs/{job_id}` inspect status/usage/error category.
- `POST /{conversation_id}/jobs/{job_id}/cancel` set durable cancel request.
- `POST /{conversation_id}/jobs/{job_id}/retry` resume an eligible terminal/paused job with the original manifest.

Every endpoint starts from `require_owned_novel`, scopes child IDs by owner+novel+conversation, and returns 404 for inaccessible IDs.

## Visible Context Assembly

1. Resolve cutoff from persisted reading progress; no progress means first chapter.
2. Honor full book only when the existing `timeline_full_book` value is true.
3. Validate selection and include it as mandatory evidence.
4. Retrieve only rows whose owner/novel/version/chapter fit the frozen visibility snapshot.
5. Prefer exact overlapping Phase 07 evidence, then bounded hybrid retrieval from visible chapters, confirmed active timeline events, accepted knowledge units/judgments, and Phase 09 relationship observations through the read-only protocol.
6. Store bounded excerpts and stable evidence IDs in the manifest before any model call.
7. Include previous dialogue only as bounded, untrusted conversational framing; it is never added to allowed evidence IDs.

Retrieval similarity, previous assistant text and Phase 09 candidate/unconfirmed rows are not facts. Missing optional sources reduce evidence; they do not permit invented answers.

## AI Output and Citation Enforcement

Use a strict envelope:

- `answer_blocks[]`: `{text, evidence_refs[min=1]}`
- `clarifying_question`: nullable string
- `uncertainty`: nullable structured reason
- `suggestion_candidates[]`: evidence-bound proposal, `requires_explicit_confirmation=true`

Local validation rejects refs outside the manifest, empty citations, extra fields, domain-write instructions and suggestions without evidence. If usable evidence is absent, `answer_blocks` must be empty and `uncertainty` or a clarifying question is required. UI renders blocks and citations directly; it does not infer citations from prose.

## Durable Job, Retry, Cancel and Budget

- HTTP commits a queued job and returns; worker obtains a lease and polls `cancel_requested` before retrieval, before provider call, after provider return and before assistant-message commit.
- Freeze model provider/id/revision, prompt/schema/context hashes and budget policy on job creation. Do not use provider remote conversation IDs.
- Reserve both conversation and novel ledgers in deterministic lock order before network access. Unknown pricing or either scope exceeding calls/tokens/cost pauses without a call.
- A transport timeout/outcome-unknown remains charged/reserved until resolved. No blind retry and no provider fallback.
- One local schema/citation repair may occur only with a separate reservation. Retry endpoint reuses the original user message and manifest.
- If cancellation arrives during a provider call, settle actual usage, hash/audit the response, discard it, and do not create an assistant message.

## Privacy and Security

- Treat novel text, selection, user question and retrieved evidence as private owner data.
- Do not emit prompt, selection, evidence excerpt or model raw output into logs/release reports; log IDs, hashes, counts, status and redacted error codes.
- API list responses exclude message bodies and context excerpts.
- Hard delete cascades private conversation data after canceling active jobs.
- Prompt text is untrusted data inside delimiters; model has no tools or mutation functions.
- Visible-set-first applies to evidence, citations, counts, previews, errors and relation/timeline metadata.

## Phase 09 / Phase 11 Contract

The `RelationshipObservationReader` is a consumer protocol, not a Phase 09 implementation. It accepts owner, novel, active version and cutoff and returns only versioned, evidence-bound, spoiler-filtered observations. Phase 10 execution must bind the completed Phase 09 public reader; if that contract is absent, execution stops on the declared phase dependency rather than adding a null adapter or editing Phase 09. Runtime dependency outages are recorded explicitly, while Phase 10 revalidates every item and records observation lineage in its manifest.

Phase 11 is prohibited from using `reader_messages`, citations, manifests, answer blocks or suggestions as source evidence. It may read only confirmed domain structures from their authoritative pipelines. A future suggestion-application workflow requires an explicit confirmation API and separate phase.

## Validation Architecture

- Unit: offset conversion, strict schemas, citation validation, cutoff policy, manifest canonicalization, budget state machine.
- PostgreSQL integration: migration, owner isolation, concurrent sequences/idempotency, hard delete, dual-ledger reservation, restart/lease, cancel-late-result.
- API contract: complete conversation lifecycle and stable 404/409/422/202 behavior.
- Adversarial: forged offsets/refs, future-chapter side channels, prompt injection, cross-owner IDs, stale chapter hash, uncited claims, fake suggestions.
- Frontend: component states, panel geometry, selection persistence, citation navigation.
- Browser: real Next.js + FastAPI + PostgreSQL, controlled provider only; desktop and 390px.

## Package Legitimacy Audit

No package-manager install task exists. The phase reuses installed dependencies, so no package legitimacy checkpoint is required.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| DOM UTF-16 offsets diverge from Python offsets | explicit code-point conversion plus exact server re-slice/hash tests with emoji/CJK/combining marks |
| Spoiler leaks through secondary metadata | compute visible IDs first; derive refs/counts/errors/citations only from visible set |
| Concurrent sends duplicate order/job/cost | conversation row lock, unique `(conversation_id, sequence)`, client idempotency and dual ledger locks |
| Provider response arrives after cancellation | settle/audit then discard before assistant commit |
| Chat becomes a fact backdoor | no domain mutation imports/routes; strict suggestion candidate only; release grep/architecture tests |
| Phase 09 implementation shape changes | protocol adapter boundary and execution precondition; no direct ORM coupling |

## Sources

Local authoritative sources only: repository code and Phase 04/08 planning/verification artifacts listed in `10-CONTEXT.md`. No web lookup was required because no new dependency or external API was selected, and local implemented contracts are the authority.
