---
phase: 17-frozen-single-book-qualification-and-candidate-verdict
status: researched
requirements: [V08-QUAL-01, V08-QUAL-02, V08-QUAL-03, V08-QUAL-04, V08-QUAL-05]
execution_authorized: false
---

# Phase 17 Research: Frozen Single-book Qualification and Candidate Verdict

## Recommendation

Implement Phase 17 as a candidate-only qualification sidecar under `app.services.narrative_memory`, not as an extension of production eval endpoints or Reader Chat. Reuse the existing Phase 06 frozen-fixture, calibrated Judge, complete-metric and baseline-comparison patterns, but bind them to Phase 12–16 narrative-memory authority and strengthen the final gate with a fresh PostgreSQL observer. The evaluator should be pure until Plan 17-03 persists an append-only run/report and emits one fixed-command verdict.

The phase has three separable responsibilities:

1. freeze what will be measured before outputs exist;
2. run a paired same-source candidate/baseline comparison and compute complete metrics;
3. independently re-derive authority and issue a candidate-only verdict with no promotion capability.

## Verified Repository Baseline

### Phase 12 eligibility

- `audit_contracts.py`, `audit.py` and `audit_pg.py` expose read-only `reusable_exact|rebuild_required|blocked|optional_unavailable` decisions and a provider-call guard.
- Phase 12 independent verification passed and proved no data repair, provider call or active-pointer change.
- Phase 17 must consume the exact report/checksum and reject a stale or non-exact hierarchy before qualification calls.

### Phase 13 candidate authority

- `narrative_memory_versions/nodes/claims/edges/source_links/manifests/validation_reports` define an append-only explicit-version sidecar with no active pointer.
- Strict contracts and canonical hashes bind typed claims to Phase 07 evidence. Structural validation reports already use `qualified_candidate|blocked` vocabulary, but Phase 17 must not confuse structural validity with comparative quality qualification; the Phase 17 report is a separately typed/evidenced audit.
- Current handoff states Phase 13 is paused at Plan 13-02 Task 3 with unverified WIP. Therefore all Phase 17 plans require completed 13-02/13-03 verification and cannot treat present files as implemented dependencies.

### Phase 14 builder handoff

- Planned builder authority freezes run/stage/call/budget/report lineage and produces a complete or explicitly partial candidate with a database-derived manifest.
- Stage reports include calls, tokens, costs, cache hits and optional-source statuses. Phase 17 reads those facts but cannot resume or repair a run.
- Only complete, sealed and structurally revalidated candidates are eligible for quality comparison.

### Phase 15 retrieval handoff

- Planned offline experiment uses explicit candidate version, deterministic local/arc/global/mixed routing, cutoff-first candidate loading, legal descent, raw fallback and fresh leaf re-slice.
- Public trace is spoiler-sanitized and Reader Chat remains byte-equivalent/no-cutover. Phase 17 should call this internal experiment seam, not register a new production route.
- A baseline adapter can invoke the same visible Phase 07 leaf loader/rerank/citation validator while deliberately bypassing narrative-memory upper levels.

### Phase 16 reuse handoff

- Roadmap requires a deterministic dirty closure for edit/insert/delete/reorder/boundary fixtures, checksum-identical carry-forward, conservative expansion and a reuse economics report.
- Phase 17 must ingest Phase 16's database-verifiable `reuse_report.py` authority containing rebuilt/carried/stale counts and ranges, observed actual calls/tokens/cost, deterministic full-rebuild upper bounds, avoided upper bounds, dirty scope, carry reuse and exact-cache reuse. Avoided values remain estimates/upper bounds rather than claimed observed savings. Missing/stale/non-recomputable reuse inputs make reuse metrics unavailable and therefore block a verdict whose policy requires them.

### Existing evaluation primitives

- `rag_fixture.py` and `rag_quality.py` already model frozen fixtures, Generator/Judge isolation, calibration, deterministic faithfulness recount, p95 metrics, baseline comparison and fail-closed policy evaluation.
- `rag_quality.py` also owns baseline promotion functions. Phase 17 may reuse pure scoring concepts/helpers only; it must not import API, baseline prepare/commit, active baseline or promotion paths.
- Existing domain qualification scripts show fixed operator command patterns, but Phase 17 needs stricter stdout/verdict and PostgreSQL observer authority.

## Frozen Bundle Design

### Fixture identity

The qualification fixture is immutable canonical JSON with:

- fixture schema/version, author/reviewer IDs or review record, `frozen_at` and fixture checksum;
- owner, novel, source snapshot, Phase 07 hierarchy build/checksum and explicit candidate version/manifest;
- question cases sorted by stable case key;
- case bucket, query, persisted reading cutoff, expected answerability, allowed route(s), required/optional gold leaf identities and graded relevance;
- spoiler forbidden evidence/metadata set derived from the frozen cutoff, without serializing forbidden titles/content into public reports;
- no-answer rationale and evidence exclusion scope;
- fixture-construction provenance explicitly excluding candidate retrieval, answer, score and report artifacts.

The freezer validates all gold leaves by fresh source re-slice before sealing. A candidate metric/result object is an illegal fixture input. Freeze must precede qualification-run creation; attempts to replace the fixture hash after a run begins fail closed.

### Policy identity

The immutable policy binds:

- required buckets and minimum case counts;
- retrieval k/budget/rerank, route/fallback and citation rules;
- absolute thresholds by bucket and aggregate, relative non-regression limits against baseline, and zero-tolerance safety gates;
- Generator and calibrated Judge deployments/revisions/prompts/schemas/decoding;
- max calls/tokens/cost, known price snapshot, timeout/retry/cache policy;
- metric schema/version and verdict rules;
- required report disclaimer and no-promotion/no-cutover capability policy.

Policy hash is computed before results. Empty required buckets, unknown price, missing calibrated lineage or ambiguous threshold direction is a policy error that becomes `blocked`, not an evaluator default.

## Paired Comparison Protocol

For each case create one frozen `PairedCaseEnvelope`. Candidate and baseline share:

- source snapshot and Phase 07 build;
- exact query and reading cutoff;
- visible leaf universe and final citation validator;
- retrieval top-k/leaf expansion/rerank limits;
- answer prompt/schema, Generator and Judge lineage;
- timeout, retry, token and dollar ceilings;
- pricing snapshot and metric version.

Only `strategy` differs:

- `hierarchical_candidate`: Phase 15 deterministic router → visible candidate levels → descent/collapsed/raw fallback → leaf validator.
- `leaf_raw_baseline`: same cutoff-first Phase 07 leaf universe → same leaf budget/rerank → same leaf validator, without reading candidate node/claim/summary content.

Use distinct exact-cache namespaces bound to strategy while recording cold/cache-hit status, so one side cannot silently receive the other's generated answer. The paired runner can alternate a predeclared order by case key to reduce order bias, but order is frozen before results. Any source/cutoff/budget/generator/Judge divergence marks the pair non-comparable and blocks the run.

## Metric Contract

Every metric includes numerator, denominator, value, unit, status and contributing case IDs. Required values cannot be absent, NaN/Infinity or synthesized as zero.

### Retrieval and routing

- gold leaf recall@k;
- reciprocal rank and nDCG@k using frozen graded leaf relevance;
- route hit by allowed starting layer;
- fallback used/rate and fallback reason distribution;
- visible citation validation/rejection counts;
- no-answer abstention correctness and false-answer rate;
- spoiler forbidden-leaf/metadata leakage count (must be zero).

### Answer quality

- deterministic claim-to-citation support/critical unsupported count;
- calibrated Judge faithfulness and relevance, with Generator/Judge lineage isolation;
- answerability/no-answer compliance;
- per-bucket and aggregate means plus the policy's lower-bound/statistical form where applicable.

Judge scores never override deterministic unsupported/spoiler/citation failures.

### Performance and economics

- retrieval, generation, Judge and end-to-end latency p50/p95;
- calls/tokens/cost by strategy and role, total and per case;
- cache hit/miss counts with strategy-isolated identity;
- Phase 16 rebuilt/carried/stale counts, observed actual calls/tokens/cost, avoided calls/tokens/cost upper bounds and full-rebuild upper bound, preserving Phase 16's estimate labels and frozen formula inputs;
- hierarchical-vs-baseline deltas and policy ratios.

Unknown price, incomplete call usage or a non-recomputable reuse denominator blocks the report instead of disappearing from aggregates.

## Verdict Order

Evaluate in fixed fail-closed order:

1. execution prerequisite and frozen fixture/policy integrity;
2. owner/novel/version/snapshot/build scope;
3. Phase 13 graph/manifest/claim→leaf closure;
4. Phase 14 complete build/report/call ledger;
5. Phase 15 cutoff/citation/spoiler and paired comparability;
6. Phase 16 reuse report recomputation;
7. required bucket/sample/metric completeness;
8. zero-tolerance spoiler/citation/unsupported/no-pointer gates;
9. absolute per-bucket/aggregate quality thresholds;
10. relative baseline, p95 latency and cost/budget thresholds;
11. fresh production pointer before/after equality.

Any failure yields `blocked` with stable sorted reason codes. Only all gates passing yields `qualified_candidate`. There is no warning-success or partial-qualified state.

## PostgreSQL Qualification Authority

Use an additive, append-only sidecar rather than overloading Phase 13 structural reports. The minimal authority should contain:

- `narrative_memory_qualification_runs`: owner/novel/candidate, fixture/policy/source/hierarchy/manifest/build/retrieval/reuse lineage, generator/Judge/pricing/budget hashes, status timestamps and before-pointer digest;
- `narrative_memory_qualification_case_results`: strategy/case identity, retrieval/answer/call/metric artifact checksums and sanitized reason codes;
- `narrative_memory_qualification_reports`: complete metric payload checksum, independent verifier checksum, after-pointer digest, command payload digest and verdict.

All rows are scoped, append-only and explicit-version. No table/column is named or usable as active/current/production selector. A report can be inserted only after a fresh verifier succeeds or records blocking reasons. Qualification audit writes are excluded from the production pointer digest but included in the command/report lineage.

## Fresh Observer Protocol

Open a separate SQLAlchemy session after the runner has closed its write transaction. Recompute from raw authority:

- exact owner/novel/candidate/source/hierarchy/manifest identities;
- Phase 13 ranges, DAG, typed claim source closure and Unicode Chapter re-slice/hash;
- Phase 14 terminal stage set, budget/call settlement and build report checksum;
- every Phase 15 retrieval manifest, cutoff, visible trace and final citation;
- Phase 16 dirty closure/carry-forward byte identity/reuse economics;
- fixture/policy and complete metric checksum;
- complete production selector/revision/journal snapshot before and after.

The pointer snapshot must cover at least Phase 07 chunk, Phase 08 timeline, Phase 11 clue and Phase 06 active baseline authorities, plus schema-discovered production selector tables. It also asserts no narrative-memory active pointer or promotion table/function/route exists. Relationship has no active pointer, but its accepted authority remains read-only and any mutable revision/journal present in the live schema is included.

## Fixed Command Contract

Add one operator CLI requiring explicit `--owner-id`, `--novel-id`, `--version-id`, `--fixture`, `--policy` and budget acknowledgement. It performs preflight, snapshots pointers, runs the paired evaluation, invokes the fresh verifier, persists the append-only report, then prints one canonical JSON document.

- success: `verdict=qualified_candidate`, exit 0;
- every other condition: `verdict=blocked`, nonzero exit (use a stable documented code such as 2 for qualification block and 1 for command/config failure, while still emitting `blocked` when a report can be formed).

The output digest is computed over the canonical payload excluding its own digest field, then stored and emitted. Logs go to stderr and never include spoiler text, credentials, raw provider payloads or hidden metadata. There is no API endpoint, worker auto-trigger, promotion import or consumer hook.

## Verification Strategy

### Unit and frozen artifacts

- fixture/policy strict parsing, canonical ordering/hash, bucket coverage and candidate-output exclusion;
- paired envelope equality except strategy;
- complete metric math including empty/NaN/unknown-price failures;
- deterministic verdict order and reason codes;
- Generator/Judge isolation and Judge-non-authority.

### PostgreSQL integration

- append-only scoped run/case/report round trip and cross-owner/version rejection;
- fresh source re-slice, graph/manifest/build/retrieval/reuse recomputation;
- pointer before/after byte equality and no selector/promotion schema;
- fixture/policy/result tampering, stale snapshot, partial build, invalid cutoff/citation and missing metric all block.

### Comparative and adversarial

- all five buckets with deterministic fake transports for regular CI;
- hierarchical improvement, equality and regression cases against the same leaf baseline;
- no-answer false answer, spoiler metadata leak, future leaf, summary-only citation, Judge false positive, cache cross-strategy and unknown price;
- fixed command exact stdout/digest/exit status and repeatability;
- optional separately authorized live-provider single-book run with hard cost ceiling, never as the only correctness evidence.

## Risks and Planning Cautions

- **Fixture leakage:** questions created after viewing results invalidate the evaluation. Enforce a pre-run immutable hash and provenance exclusion.
- **Unfair baseline:** giving candidate more leaf/token budget makes quality deltas meaningless. Compare a canonical paired envelope field-by-field.
- **Metric completeness illusion:** missing buckets or prices can look like zero failures. Represent missing/invalid explicitly and block.
- **Judge overreach:** a favorable LLM score can mask citation/spoiler failure. Run deterministic gates first and make them non-overridable.
- **Self-attested verifier:** reading runner JSON is not independent. Reopen PostgreSQL and recompute raw rows/content.
- **Pointer coverage drift:** hard-coded tables can miss future selectors. Combine an explicit allowlist with schema discovery and fail on unknown pointer-like authority.
- **Verdict ambiguity:** structural Phase 13 report and quality Phase 17 report share vocabulary. Give Phase 17 a separate typed schema and explicit `qualification_kind=single_book_candidate`.
- **Overclaiming:** a single-book result is not production or project completion. Require scope disclaimer in schema, CLI and tests.

## Plan Split

### 17-01 — Frozen single-book fixture and policy

Strict immutable bucketed fixture/policy contracts, pre-result freeze, gold leaf validation and paired envelope comparability.

### 17-02 — Comparative evaluation and complete metrics

Same-source hierarchical/leaf runners, isolated answer/Judge path, full per-case/bucket/aggregate metrics and pure fail-closed threshold evaluation.

### 17-03 — Independent PostgreSQL authority and fixed verdict

Append-only qualification persistence, fresh Phase 12–16 verifier, pointer-diff proof, fixed CLI and candidate-only/no-promotion qualification.

## Planning Conclusion

Phase 17 can produce a defensible single-book candidate verdict only by keeping fixture freeze, paired measurement and independent authority separate. No plan should expose a product endpoint or promotion seam. Missing Phase 13–16 verification is an execution blocker, not a reason to weaken the qualification design.
