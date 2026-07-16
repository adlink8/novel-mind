---
gsd_state_version: 1.0
milestone: v0.8
milestone_name: 分层叙事记忆与层级 RAG
status: executing
last_updated: 2026-07-16T16:00:00.000Z
last_activity: 2026-07-16
progress:
  total_phases: 16
  completed_phases: 14
  total_plans: 71
  completed_plans: 70
  percent: 88
stopped_at: null
authorized_scope: phases 13-18 (user 2026-07-16: 查看13-18阶段任务 编排GSD子代理执行)
---

# Project State

## Project Reference

See `.planning/PROJECT.md` and `IMPLEMENTATION-STATUS.md`.  
系统结构以 `docs/architecture/` 为准。

**Core value:** 先建立可信、安全、可迁移的实现基线，再扩展 RAG。

## Current Position

Phase: 16 (Dependency-aware Local Rebuild and Carry-forward)
Plan: 3 of 3
Status: COMPLETE — 16-VERIFICATION `status: passed`; Phase 17 unblocked (not started)
Last activity: 2026-07-16

## Authorization (2026-07-16)

- User instruction: `查看13-18阶段任务 编排GSD子代理执行`
- Scope: Phase 13 remainder + Phase 14–18 full plans
- Dependency order: 13 complete+verify → 14 → 15 → 16 → 17; **Phase 18 parallel** (frontend-only, independent of RAG chain)
- Plan gates that previously required `批准执行 Phase N` are satisfied for this orchestration run
- Commit discipline: stage only plan-declared files; do not commit unrelated dirty WIP

## Execution Inventory

- Phase 13: **COMPLETE + VERIFIED** (`13-VERIFICATION.md` status: passed). 13-01/02/03 SUMMARYs present. Candidate-only authority, strict contracts, provenance seal, no-pointer proofs.
- Phase 14: **COMPLETE + VERIFIED** (`14-VERIFICATION.md` status: passed). 14-01..04 SUMMARYs present. Builder control plane Alembic `14membuild01`; Chapter→Arc→Global candidate worker; optional sources; CLI dry-run; 37 tests passed.
- Phase 15: **COMPLETE + VERIFIED** (`15-VERIFICATION.md` status: passed). 15-01..03 SUMMARYs present. Deterministic router; cutoff-first visible loaders; multi-level descent + leaf re-slice; default-off offline experiment; 59 targeted tests passed.
- Phase 16: **COMPLETE + VERIFIED** (`16-VERIFICATION.md` status: passed). 16-01/02/03 SUMMARYs present. Rebuild authority Alembic `16memrebuild01`; provider-free oracle/carry/report; dirty-only Phase 14 stages; fixed CLI; 62 targeted tests passed.
- Phase 17: 3/3 plans planned; **unblocked** by Phase 16 verification; candidate-only verdict
- Phase 18: COMPLETE — 18-01 `f6280cf`, 18-02 `2035b54`, 18-03 `743a524`; dual-viewport motion qualified (parallel track)

## Auto Routing

Wave A Phase 13–16: **done**. Phase 18 complete in parallel.
Next serial RAG chain: Phase 17 after verification.
Phase 16 emits reuse mechanics only (no Phase 17 quality/promotion verdicts); candidate-only (no pointer).

## Phase 11 Execution Metrics

- 11-01: 55min, 3 tasks, 10 files, 28 targeted tests passed (16 unit + 12 PostgreSQL integration, 0 skip); alembic head `11cluetrack01`.
- 11-02: 45min, 3 tasks, 12 files, 38 targeted tests passed (6 candidates + 8 source protocols + 8 llm + 12 gates + 4 adversarial, 0 skip).
- 11-03: 95min, 3 tasks, 14 files, 15 targeted tests passed (7 unit + 8 integration, 0 skip); feat commit `464f65c`.
- 11-04: 45min, 3 tasks, 9 files, 28 plan-targeted + 121 full frontend Vitest + production build; feat commit `0d61b1b`.
- 11-05: 95min, 3 tasks, 9 files, offline/unit 16 + adversarial 7 + PG integration 7 + release contract 9 + Playwright 2 (desktop+390) passed on 5433; feat commit `e50df64`.

## Phase 11 Decisions

- Migration down_revision is live single head `12readerchat01` (not the outdated plan text `10analysistime01`).
- Clue uses fully clue-owned run/version tables so timeline `analysis_runs` active_key uniqueness is not shared.
- Lifecycle current state is always derived via `replay_lifecycle`; no mutable authoritative current_status column.
- Lifecycle events, overrides and pointer journals are physically append-only via PostgreSQL triggers.
- paid_off requires distinct cue+payoff narrative coordinates with strict later order (app validator + DB trigger).
- Chat text / similarity scores are never lifecycle evidence; link contracts reject those fields.
- Relationship observation links may be `source_unavailable` without inventing graph rows.
- Phase 09 null/outage sources emit `source_unavailable` (distinct from healthy `empty`); never zero-signal success substitute.
- Clue LLM repair is caller-controlled (`repair=True` only); provider_retries=0; judge has no DB/lifecycle writes.
- GateService returns pure GateDecision; similarity/motif/vector alone cannot accept active/paid_off.
- Lifecycle evidence rows attach only to lifecycle_event_id (not machine_clue_id) to avoid unique collisions rolling back machine clues.
- Spoiler full-book reuses Phase 08 `timeline_full_book` only; no clue-specific preference endpoint.
- Override supersession is INSERT-only latest-wins; reanalysis relinks on exactly one evidence identity match.
- Frontend clue client lives in `frontend/src/lib/clue-api.ts` (does not overwrite dirty `api.ts`).
- UI matches live 11-03 envelope fields (`available_states` / `available_character_ids` / `counts.by_state`).
- ClueWorkspace owns independent clue run start/status; /analysis adds 线索与伏笔 tab without top-level /clues route.
- Reject and adjust_link require explicit confirmation; actions refresh from server authority only.
- Release contract tests live at `tests/ci/test_clue_release_gate.py` (Phase 08/10 pattern).
- Qualification CLI owns fixed commands/digests and fresh PostgreSQL observer; self-hashes alone cannot release.

## Phase 10 Execution Metrics

- 10-01: 45min, 3 tasks, 8 files, 28 targeted unit+PostgreSQL migration tests passed (0 skip); alembic head `12readerchat01`.
- 10-02: 25min, 3 tasks, 7 files, 9 targeted PostgreSQL API/IDOR tests passed (0 skip); OpenAPI conversation paths + ruff clean.
- 10-03: 55min, 3 tasks, 8 files, 35 targeted tests passed (20 unit + 4 PG context + 9 conversation API/IDOR + 2 HEAD spoilers); ruff clean; Phase 09 files untouched.
- 10-04: 70min, 3 tasks, 12 files, 93 full reader-chat suite passed (32 plan-targeted: 5 budget + 8 gateway + 9 generation PG + 10 adversarial); ruff clean; forbidden capability scan empty.
- 10-05: 95min, 3 tasks, 13 files, backend 93 + frontend 19 unit + lint 0 err + build + mocked e2e 4 + release gate 10 passed; real e2e blocked (Postgres 5432 offline).

## Phase 10 Decisions

- Migration down_revision is Phase 09 single head `11relobserve01`.
- Chat budgets use separate reader_* ledger tables with conversation and novel scopes.
- Suggestions always require_explicit_confirmation=true; no apply/confirm domain write contract.
- Hard-delete cascades private chat content; novel-scoped chat ledger survives conversation delete.
- reader_* tables never FK into timeline/relationship/clue fact tables.
- Conversation API injects ContextBuilder; production default is ProductionContextBuilder (10-03); DeterministicContextBuilder retained for tests.
- Archived conversations are readable but reject new messages with 409.
- Child resources always scoped owner+novel+conversation; inaccessible IDs return 404 with no ownership leak.
- Row lock on reader_conversations.next_sequence; client_message_id unique idempotency with savepoint recovery.
- resolve_chapter_cutoff is the shared public spoiler cutoff; full-book only via persisted timeline_full_book.
- Phase 09 observations enter chat only via RelationshipObservationReader; runtime outages are source_unavailable, never invented edges.
- Retry rehydrates frozen manifest checksum; never rebuild under newer reading progress.
- Dual budget lock order: novel ledger then conversation ledger; unknown pricing or either ceiling → zero provider calls.
- Worker freezes deployment/prompt/schema hashes on process; one schema/citation repair only; post-cancel responses settle then discard.
- Exact recovery stores validated envelope on attempt.usage; cache_hit audit on re-publish without provider call.
- Chat worker has no LangChain/LangGraph/tools/remote conversation IDs and no domain mutation imports.
- Selection: page UTF-16 base + Array.from code-point conversion; server re-slice remains authority.
- Desktop reserves chat column (no permanent text cover); mobile max-h 45vh collapsible chip.
- Conversation truth only in PostgreSQL; localStorage is panel presentation only.
- Background job dispatch is fail-soft; e2e completes jobs via controlled transport helper.

## Phase 09 Execution Metrics

- 09-01: 28min, 3 tasks, 6 files, 13 targeted persistence tests passed (0 skip); alembic head `11relobserve01`.
- 09-02: 45min, 3 tasks, 10 files, 17 targeted pipeline tests passed (13 unit + 4 PostgreSQL integration, 0 skip).
- 09-03: 55min, 3 tasks, 8 files, 10 targeted graph API/projection PostgreSQL tests passed (0 skip); OpenAPI graph path registered.
- 09-04: 45min, 3 tasks, 13 files, frontend 85 Vitest + lint (0 errors) + Next production build passed; cytoscape@3.34.0 pinned.
- 09-05: 95min, 3 tasks, 9 files, backend 60 + frontend 85 + Playwright 4 + release qualified; scope_clean true.

## Phase 09 Decisions

- Phase 09 edge types are only ally/enemy/family/mentor/romantic; causes/precedes/same_entity are not graph edges.
- Accepted observations and protective overrides are physically append-only via PostgreSQL triggers; supersession is always INSERT.
- Legacy character_relations is left untouched and never used as Phase 09 truth.
- AUTO_ACCEPT_THRESHOLD = 0.85; REVIEW_THRESHOLD = 0.65; policy_hash freezes gate order and thresholds.
- same_entity/causes/precedes never produce RelationshipObservation; same_entity is identity-review metadata only.
- RelationshipObservationWorker is the sole accepted-observation writer; LLM cannot choose owner/version/status.
- Graph cutoff reuses only Phase 08 timeline_full_book; missing progress defaults to chapter one.
- Latest-wins overrides use highest id per logical key/field without mutating prior override rows.
- Hard cap responses empty nodes/edges with filters_required while preserving spoiler-safe counts.
- Phase 10/11 get documented read-only service functions only; no chat/clue tables or routes in Phase 09.
- Cytoscape.js is the relationship renderer (exact 3.34.0); ECharts timeline remains; workspaces share version/full-book/through_chapter only.
- Graph client filter params match OpenAPI singular character_id/relation_type; filters_required never mounts Cytoscape.
- Phase 09 release uses independent PostgreSQL observations + internally executed command digests; verdict is passed or blocked_release.
- Character-filtered graph queries prefilter endpoints in SQL so 10k observation seeds meet p95<=300ms.

## Phase 08 Execution Metrics

- 08-02: 18min, 3 tasks, 11 files, 23 targeted tests passed.
- 08-03: 9min, 3 tasks, 7 files, 9 targeted tests passed including real PostgreSQL lifecycle coverage.
- 08-04: 11min, 3 tasks, 7 files, backend 5 tests and frontend 12 contract tests passed.
- 08-05: 11min, 3 tasks, 7 files, frontend 66 unit tests plus desktop/mobile Playwright passed.
- 08-06: 9min, 3 tasks, 6 files, backend 56 + controlled live 7 + CI 5 + frontend 66 tests and build passed.
- 08-07: 26min, 3 tasks, 9 files, 63 timeline tests passed including real PostgreSQL concurrency coverage.
- 08-08: 24min, 3 tasks, 9 files, backend 65 + frontend 68 + real desktop/mobile browser 2 + release gate 7 tests passed.
- 08-09: 16min, 3 tasks, 10 files, backend 74 + frontend 68 + release gate 8 tests and production build passed.
- 08-10: 7min, 2 tasks, 4 files, PostgreSQL qualification 5 + release/workflow/CI-gate 47 tests passed.

## Phase 08 Decisions

- Timeline extraction keeps narrative position separate from four strict story-time precision shapes.
- Timeline model calls allow one same-deployment repair with an independent budget reservation and no fallback.
- Only evidence-valid complete output is cached and provisionally published.
- Contradictory chronology remains explicitly unranked instead of receiving fabricated story order.
- Promotion and rollback require recomputed graph manifests and row-locked expected-revision CAS.
- Missing reading progress exposes only the first chapter; full-book access requires an explicit persisted per-novel preference.
- Active and running candidate responses keep independent progress, events, counts, aggregates, previews, and edges.
- Canvas and keyboard companion list expose the same visible event set; active and candidate views remain source-isolated.
- Full-book disclosure requires confirmation before persisting the per-novel preference.
- Blocked or unavailable live timeline dependencies produce `metrics=null` and cannot satisfy the release gate.
- Phase 08 qualification is fiction-only and proves deferred relationship graph, reader AI, clue tracking, and history products are absent.
- Production timeline work is driven by durable background workers over the active Phase 07 hierarchy and resumes from completed chapter/stage checkpoints.
- Gateway budget reservations, call attempts, outcomes, and exact cache recovery are PostgreSQL-backed and auditable across process restarts.
- Production extraction and reconciliation use fixed no-fallback deployments; unsupported capability, unknown pricing, or budget rejection pauses before a provider call.
- Narrative timeline projection orders by chapter, source offset/index, then event ID; participant controls derive only from the selected version.
- Phase 08 release qualification requires signed PostgreSQL production-worker artifacts and measured visible-query output.
- Timeline browser E2E uses real Next.js, FastAPI, PostgreSQL, and timeline APIs with only the provider transport controlled.
- Running workers poll durable cancellation between every production stage and stop before later calls or promotion.
- Reconciliation exact-cache identity binds version lineage plus hashes of the actual prompt and output schema.
- Timeline `source_start` is required end-to-end and derives from persisted evidence offsets.
- Release qualification requires independent DB authority observations and successful command-output digests; self-hashes prove integrity only.
- The executable release CLI owns fixed argv/cwd commands and the PostgreSQL session factory; command digests are recomputed from internally captured combined output bytes.
- Release verdicts expose command, exit code, and digest without exposing captured test or service output.

## Phase 06 (v0.5) — COMPLETE

REQ-AUTO-01..11 已交付（含 06-08 QualityRun 持久化、06-09 BaselineCandidate prepare/commit 与跨 chunker 报告）。

## Phase 07 — COMPLETE (logic + tests; PG wiring residual)

包路径：`backend/app/services/chunking/`  
测试：`tests/unit|integration/chunking` + adversarial + legacy → **88 passed**

## Next Action

1. Execute Phase 17 (frozen single-book qualification) after reading 17 plans; keep candidate-only.
2. Never promote narrative-memory candidates; Phase 16 is reuse mechanics only.
3. Do not touch unrelated dirty WIP outside plan-declared files.

## Session

- Resumed: Phase 13–18 GSD subagent orchestration authorized 2026-07-16
- Resume file: `.planning/HANDOFF.json`
