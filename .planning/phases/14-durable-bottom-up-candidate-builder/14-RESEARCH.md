# Phase 14 Research: Durable Bottom-up Candidate Builder

**Date:** 2026-07-16  
**Scope:** V08-BUILD-01..05 only  
**Confidence:** High for repository patterns, authority boundaries and execution semantics; medium for final operational limits, which must be frozen in the implementation policy rather than inferred at runtime.

## Recommendation

Implement Phase 14 as a separate `narrative_memory_builder` control plane feeding the explicit Phase 13 candidate authority. Reuse the proven durable patterns from timeline and clue—leases, stage checkpoints, model-call attempts, row-locked budget reservations, fixed deployments, persistent exact cache, post-call cancellation checks—but do not share their tables or promotion lifecycle. Phase 14 must never call timeline promotion, clue promotion, Reader Chat generation or any implicit current-version resolver.

Use four serial delivery waves. First establish the durable control plane and Chapter State worker. Second freeze contiguous arc/volume boundaries and aggregate only verified chapters. Third build Global from verified middle-level nodes and finish through Phase 13 database-derived manifest/seal/validation. Fourth close optional-source, failure-isolation, crash/concurrency, no-chat and no-pointer gaps with a fixed single-book dry-run entry point. This ordering makes every later stage consume persisted validated authority rather than transient model output.

## Verified Repository Baseline

### Phase 12 eligibility authority

- `12-VERIFICATION.md` is `passed`: 4/4 requirements and 10/10 truths.
- `EligibilityReport.provider_calls_allowed` is derived from required hierarchy results and rejects caller override.
- Exact hierarchy requires immutable/non-candidate active Phase 07 build, complete snapshot/manifest/tree/offset/hash/coverage and no foreign-scope rows.
- Optional timeline/relationship/clue inventories distinguish healthy empty from unavailable and validate version lineage. Clue pointer target must be exactly `validated`.
- Real API/CLI fresh-observer tests already prove the audit itself performs no provider call, repair, promotion or pointer mutation.

Phase 14 should persist the exact eligibility policy/report checksum on its run and compare it with the immutable `NarrativeMemoryVersion`. It must call the existing guard rather than reproduce a weaker Boolean.

### Phase 13 candidate authority

- Phase 13 Plan 13-01 delivered Alembic head `13memoryauth01` and seven append-only candidate tables: version, node, claim, edge, source-link, manifest and validation report.
- Candidate content is scoped by owner/novel/version with composite foreign keys; direct evidence uses the Phase 07 `(build_id, node_id)` identity plus database guards.
- Manifest seal prevents late content inserts. Candidate authority has no mutable status, active key, pointer, promotion or execution table.
- Version lineage freezes source snapshot, hierarchy build/checksum, eligibility report, prompt/schema/model/decoding/config/policy and optional-source lineage.
- Plans 13-02 and 13-03 define strict six-variant claims, explicit-version persistence, direct claim-to-leaf closure, deterministic DB manifest and structural validation, but Phase 13 is currently paused mid 13-02 Task 3. Phase 14 may be planned now but cannot execute until those contracts are actually complete and independently verified.

### Existing durable worker precedents

- `app.models.analysis` provides timeline-owned `AnalysisRun`, `AnalysisChapterStage`, `ModelCallAttempt`, `AnalysisBudgetLedger` and `AnalysisBudgetReservation`. Timeline production worker demonstrates lease/heartbeat, per-stage idempotency, persistent exact cache, cancellation polling and fixed deployment lineage.
- `app.services.timeline.worker` is not safe to import wholesale because it validates and promotes a timeline version and dispatches downstream analysis. Phase 14 should reuse concepts, not its promotion behavior or tables.
- `app.services.timeline.model_gateway` and `budget` demonstrate pre-call reservation and auditable call outcomes. Reader Chat and clue have analogous isolated ledgers. The narrative-memory builder therefore needs its own ledger so domain budgets and active-run uniqueness cannot collide.
- Timeline/clue exact caches key validated model output by exact frozen inputs and record cache hits as new audit events. Phase 14 should use the same semantic rule: a cache hit avoids a provider call but does not avoid lineage, validation or stage audit.
- Existing workers poll cancellation before calls and before publication. Phase 14 must add a post-provider, pre-authority-write check so a response arriving after cancellation is settled and discarded.

## Proposed Durable Authority

Create an additive migration and `backend/app/models/narrative_memory_builder.py` with builder-specific tables. All rows are owner/novel/version scoped where practical, and every run points to one explicit immutable `NarrativeMemoryVersion`.

### `narrative_memory_build_runs`

- Identity: `id`, owner, novel, version, immutable eligibility report checksum and policy version.
- Durable state: closed status (`pending`, `running`, `partial`, `paused_budget`, `paused_dependency`, `cancelled`, `completed`, `failed`), lease ID/expiry, heartbeat, cancel flag, progress and status reason.
- One live run per explicit version; no `active_key` by novel and no current-version lookup.
- Frozen run policy contains stage order, bounded repair count, chapter concurrency, deterministic arc policy and maximum budgets. No provider secret or free-form prompt authority is stored.

### `narrative_memory_build_stages`

- Unique `(run_id, stage_key)` with closed stage kind: chapter state, arc/volume plan, arc/volume aggregate, global aggregate, manifest/validation.
- Scope/range fields, dependency keys, attempt count, status, package/cache/artifact checksum, checkpoint and stable reason code.
- Completed stage identity is immutable; retries may claim only incomplete/failed stages. A conflicting artifact must fail rather than overwrite.
- Dependency rows or a canonical dependency list make chapter→arc→global blocking explicit and reportable.

### `narrative_memory_model_call_attempts`

- One immutable audit row per actual attempt or cache-hit event, bound to run/stage/reservation.
- Request/cache key, fixed deployment lineage, input/output hashes, status, token usage, cost, latency, provider request ID and error code.
- Only strictly validated successful output can be a cache source. Cache identity includes the actual source package checksum and every frozen prompt/schema/model/decoding/config/policy field.

### Budget ledger and reservations

- One ledger per run, with maximum and reserved/settled calls, input/output tokens and USD.
- Reservation is row-locked and committed before transport. Unknown pricing, unsupported capability or any ceiling breach creates a paused reason and zero attempt/transport call.
- Settlement is idempotent; abandoned reservations are reconciled from call audit without silently refunding an uncertain provider attempt.

### Append-only build report

- Final or paused report includes deterministic stage counts/statuses, blocked dependency closure, calls, tokens, cost, cache hits, source states, manifest checksum and worker artifact checksum.
- It is an execution observation, not a Phase 17 quality verdict and not a production selector.

## Bottom-up Package Contracts

### Chapter State package

- Load one chapter's Phase 07 evidence leaves under the frozen build and revalidate scope, order, offsets and hashes.
- Attach optional signals only through frozen adapters and include explicit `non_empty`, `healthy_empty`, `unavailable` or `lineage_mismatch` status.
- Provider output is the Phase 13 strict candidate package subset: one Chapter State node, strict typed claims and direct source links. Script rebinds all scope, version, chapter, evidence IDs, offsets and hashes before persistence.
- A single bounded same-deployment schema repair is allowed only when budget was separately reserved; semantic/evidence failures remain failures.

### Arc/Volume plan and aggregate package

- Boundary planning is deterministic and has no provider call. Prefer explicit frozen volume boundaries only if they are ordered, continuous, non-overlapping and exactly cover the eligible chapter range.
- Otherwise use a versioned deterministic arc policy (for example bounded consecutive chapter windows with stable keys). The exact window rule belongs in `policy_hash`.
- An arc/volume can run only when every included Chapter State stage is completed and its stored candidate rows revalidate. Failed chapters block their containing parent only.
- Provider input contains validated typed child claims plus their direct Phase 07 leaf links, not display summaries alone. Output claims must keep direct leaf closure after canonical merge/deduplication.

### Global package

- Exactly one Global Story stage exists per version.
- It runs only after all planned arcs/volumes required for full coverage are complete and structurally valid. Any blocked/missing parent leaves Global blocked without a call.
- Global claims, conflicts and open loops use the Phase 13 closed claim union. The model cannot invent a child ID, source link or visibility boundary.
- Completion delegates manifest recomputation, seal and structural report to Phase 13 `manifests.py`/`provenance.py`; a worker checksum mismatch blocks completion.

## Optional Sources and Chat Exclusion

Build three narrow read-only adapters under `optional_sources.py`:

- Timeline: read only the exact frozen version from the Phase 12/13 lineage, include evidence-valid events, and preserve their Phase 07 leaf references.
- Relationship: read accepted Phase 09 observations only; `source_unavailable` is not an empty graph. Every observation must have evidence compatible with the frozen snapshot.
- Clue: read the exact `validated` active-pointer target and lifecycle/evidence authority; candidate/superseded/stale targets are unavailable.

Adapters return typed signal DTOs plus a source-state record. They never return arbitrary prose as authority. A static forbidden-capability test must scan the entire builder package for `reader_chat`, conversation/message tables, chat citations, similarity-derived facts, pointer setters and promotion functions. Runtime package snapshots should also prove that chat-shaped keys are rejected by strict DTOs.

## Failure, Cancellation and Resume Semantics

1. Claim a run/stage with a lease and re-read immutable version/eligibility lineage.
2. Resolve dependencies and recompute the input package/cache key.
3. If a completed stage exists, verify its artifact and candidate rows; return without a call.
4. Check cancellation, reserve budget transactionally, then check cancellation again before transport.
5. Record/settle the attempt. If cancelled after transport, discard output before candidate writes.
6. Strictly parse, validate, script-rebind evidence and persist via Phase 13 explicit-version authority.
7. Atomically mark the stage completed with artifact checksum/checkpoint. A crash before this boundary resumes idempotently; a conflicting artifact fails closed.
8. Continue independent siblings. Propagate a stable blocked-dependency status only to ancestors containing the failed child.

This produces partial but auditable candidates without sealing them prematurely. Resume clears only retryable run/stage state; it never deletes completed authority or rewrites a completed stage.

## Planned File Boundary

```text
backend/app/models/narrative_memory_builder.py
backend/migrations/versions/14_narrative_memory_builder_control.py
backend/app/services/narrative_memory/
  builder_contracts.py
  builder_repository.py
  builder_budget.py
  builder_gateway.py
  builder_packages.py
  builder_worker.py
  arc_planner.py
  global_builder.py
  optional_sources.py
  builder_report.py
backend/scripts/run_narrative_memory_build.py
backend/tests/unit/narrative_memory/test_builder_*.py
backend/tests/integration/narrative_memory/test_builder_*_pg.py
```

The CLI is an operator-controlled candidate dry-run entry point requiring owner, novel and explicit version. It must not accept a promote/current/default flag.

## Verification Strategy

### Unit

- Strict package round-trips and rejection of extra/coerced/chat-shaped fields.
- Exact-cache key sensitivity to source/package/prompt/schema/model revision/decoding/config/policy and optional-source lineage/status.
- Deterministic volume preference and fallback arc planning with exact coverage, stable keys and invalid-boundary failures.
- Dependency closure and failure isolation: only containing parent/global blocked.
- Report arithmetic for calls/tokens/cost/cache/source states.

### PostgreSQL integration

- Migration round-trip, composite scope constraints, lease races, checkpoint idempotency and append-only call/report audit.
- Concurrent budget reservations never exceed any ceiling; unknown price performs zero transport calls.
- Crash at pre-call, post-call/pre-write and post-write/pre-checkpoint boundaries resumes without duplicate authority or calls beyond defined uncertainty handling.
- Cancellation before and after provider returns; output after cancellation is settled but not persisted.
- Same validated cache input skips transport and reproduces byte-identical candidate rows; any frozen input change misses cache.
- Chapter failure preserves completed siblings and unaffected arcs byte-identically; resume executes only the failed stage and its blocked ancestors.

### Boundary and fresh observer

- Optional sources cover non-empty, healthy-empty, unavailable, stale and cross-scope cases.
- Static/dynamic tests prove Reader Chat and pointer/promotion capabilities are absent.
- Before/after fresh observer snapshots existing chunk/timeline/clue/narrative-unit pointers and journals.
- Final DB manifest recomputation equals worker artifact; no narrative-memory pointer table or implicit selector exists.

## Risks and Planning Cautions

1. **Phase 13 is unfinished.** Execute nothing until its strict contracts, explicit-version persistence, provenance and manifest services are verified. Phase 14 plans name intended interfaces but must adapt to the verified Phase 13 API rather than editing around it.
2. **Append-only candidate rows complicate retries.** Idempotency must be exact; conflicting retry output creates a failed stage/new version, never UPDATE/DELETE of prior authority.
3. **Do not reuse timeline promotion worker.** Its final path promotes a timeline pointer and dispatches relationships/clues, which violates v0.8 candidate-only scope.
4. **Cache hits still need proof.** Revalidate cached DTOs, evidence and package lineage, and record an audit event before stage completion.
5. **Healthy empty differs from unavailable.** Preserve source status even when no optional facts are included; do not substitute zero-signal success for an outage.
6. **Parent summaries cannot inherit evidence implicitly.** Every parent claim still needs direct leaf links after aggregation.
7. **A mutable arc boundary breaks cache and resume.** Freeze the selected boundary plan and checksum before any parent calls.
8. **No quality claim in Phase 14.** Structural completion is not Phase 17 qualification and cannot be named `qualified_candidate` by the worker report.

## Planning Conclusion

Phase 14 is a durable population pipeline for the isolated Phase 13 candidate envelope. It does not require all existing books to be reanalyzed and does not affect production consumers. The decisive contract is: Phase 12 authorizes provider eligibility, Phase 13 owns immutable candidate facts and evidence, Phase 14 owns only resumable construction and auditable cost, and completion stops at a sealed explicit candidate with all production pointers unchanged.
