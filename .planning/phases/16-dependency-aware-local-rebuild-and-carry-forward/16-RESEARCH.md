# Phase 16 Research: Dependency-aware Local Rebuild and Carry-forward

**Date:** 2026-07-16  
**Scope:** V08-REUSE-01..04 only  
**Confidence:** High for authority boundaries and conservative propagation; medium for final stage-cost upper-bound constants, which must be frozen and reported as estimates rather than observed savings.

## Recommendation

Implement Phase 16 as a deterministic planning-and-copy sidecar over explicit Phase 13 candidates and Phase 14 builder authority. Persist one immutable rebuild plan with normalized per-asset decisions, then create a new explicit candidate version, carry only proven-clean semantic authority, and submit only dirty stages to the existing Phase 14 worker. Do not add a second provider gateway or worker lifecycle. The oracle, carry-forward and report layers must have no provider capability.

Use three serial waves. First add the dependency/change authority and calculate conservative dirty closure. Second perform exact semantic carry-forward and integrate the frozen stage mask with Phase 14. Third independently recompute reuse economics and run fixed adversarial/PostgreSQL/no-pointer verification. Phase 15 remains a read-only regression consumer and is not an input to reuse decisions.

## Verified Planning Baseline

### Phase 13 candidate authority

- Phase 13 owns immutable explicit versions, nodes, typed claims, edges, direct Phase 07 source links, one manifest seal and append-only structural reports under `narrative_memory_*` tables.
- Every claim must have direct leaf closure, scope and frozen source/hierarchy lineage. Candidate rows are append-only and cannot be rewritten for a new source snapshot.
- Phase 13 has no active pointer or implicit current-version resolver. Carry-forward must therefore create/reuse an explicit target version and insert new scoped rows; it must never alias parent DB row IDs or copy the parent seal.
- Phase 13 is currently paused mid 13-02. The APIs named in this plan are intended contracts only until Phase 13 finishes and verifies.

### Phase 14 builder authority

- Planned Phase 14 control plane owns explicit-version run, stage/checkpoint, call attempt, budget ledger/reservation and build report authority.
- Its fixed order is Chapter State → Arc/Volume → Global. Boundary plan is deterministic/frozen; a failed chapter blocks only its parent and Global.
- Provider calls require Phase 12 eligibility, committed budget reservation, frozen exact-cache key, fixed deployment, cancellation checks and strict Phase 13 persistence.
- Phase 16 should extend the run request with a frozen rebuild-plan identity/stage mask, not bypass the Phase 14 worker. Carry decisions remain authoritative in Phase 16 rebuild items (`decision='carried'`) with source and target checksums; they do not create a new Phase 14 stage status or any stage/call/reservation row.

### Phase 15 retrieval boundary

- Planned Phase 15 reads only sealed, structurally valid explicit candidate versions through cutoff-first loaders and fresh leaf re-slicing.
- Retrieval route, trace, cache and citations are consumer observations, not source dependencies. Reuse must not ingest query text, retrieval scores, fallback reasons or citations as candidate authority.
- After rebuild, Phase 15 is useful only as a regression: explicit target candidate manifest/citations must remain valid and Reader Chat remains uncut-over.

## Dependency Graph Model

Derive a canonical graph from database rows under one parent candidate and one target hierarchy:

```text
target source/chapter/evidence identity
  -> chapter_state node + claims + direct links
     -> frozen story_arc/volume node + claims + edges
        -> global_story node + claims + edges

optional timeline/relationship/clue fact actually consumed
  -> the exact claim/node whose source link records that optional ref

boundary plan checksum
  -> every story_arc/volume
  -> global_story
```

Each vertex needs a stable semantic key, kind, chapter range, authoritative content checksum set, direct evidence fingerprints, optional-source fingerprints, schema/model/policy compatibility fingerprint and old/new scope. Each edge has a closed dependency kind and reason. Database IDs and insertion order are excluded from canonical graph identity.

Build the graph losslessly: load all parent version rows before filtering, fail on foreign scope/duplicates/unknown kinds, verify the parent seal/report, and revalidate every source link. Graph construction is read-only and provider-free.

## Change Oracle

Compare parent and target at progressively higher levels.

### Source and chapter identity

- Match chapters by stable database chapter identity only when owner/novel agree; chapter number/order is an independently compared attribute.
- `edited`: same chapter identity, authoritative content/hash or target evidence fingerprint changed.
- `inserted` / `deleted`: chapter identity appears only in target/parent.
- `reordered`: same chapter identity but narrative order/number changed.
- `evidence_remapped`: chapter content may match but target leaf partition/offset/hash mapping changed.
- Never use title similarity, text similarity, embedding or length as proof.

### Candidate compatibility

A Chapter State is clean only if its chapter identity/order, complete direct leaf evidence fingerprints, schema version, strict claim semantics, visibility, optional-source dependencies and compatible policy/model lineage can be proven unchanged. Model revision differences do not automatically dirty content if the frozen reuse policy explicitly permits semantic carry-forward, but that compatibility decision must be closed, versioned and included in the plan hash.

An Arc/Volume is clean only when:

- its node kind/range and frozen boundary plan are identical;
- every child Chapter State is clean;
- every direct evidence and optional-source dependency is clean;
- cross-chapter continuation/dependency metadata is complete and unchanged.

Global is clean only when every required Arc/Volume is clean and the complete middle-level graph, conflicts/open loops and boundary checksum remain compatible. Any dirty parent makes Global dirty.

## Conservative Propagation Rules

Use a closed reason lattice and stable topological propagation:

- Simple chapter edit with proven stable boundaries/dependencies: changed evidence → that Chapter State → containing Arc/Volume → Global.
- Insert/delete/reorder: dirty from the earliest affected narrative position. If the next stable explicit volume boundary and child mapping are provable, stop Chapter State suffix propagation there but dirty every changed/overlapping parent and Global. Otherwise extend to the book suffix and Global.
- Arc boundary change: dirty the union of old/new overlapping ranges, all affected parents, downstream suffix where child membership/order may change, and Global.
- Missing cross-chapter dependency metadata, ambiguous chapter mapping, evidence split/merge without exact target mapping, optional-source lineage uncertainty or policy incompatibility: fail closed and expand to the containing range/suffix and Global.
- Global is carried only for a true no-change graph. It is never carried when any lower authoritative dependency is dirty or uncertain.

The plan must distinguish `dirty`, `carried`, `stale_blocked` and `not_applicable`, with one or more stable reason codes and an explainable propagation path per vertex.

## Carry-forward Semantics

Carry-forward is a validated copy, not a row alias and not a cache hit:

1. Require an immutable frozen rebuild plan whose parent/target version, source/hierarchy, boundary and policy checksums match current DB authority.
2. Lock the unsealed explicit target version; reject any target content conflict.
3. Load a clean parent node and all claims/edges/direct links losslessly.
4. Revalidate strict Phase 13 DTOs and semantic checksums.
5. Resolve each parent evidence fingerprint to exactly one target Phase 07 evidence leaf with same stable chapter identity, offsets/content hash and authoritative re-slice. Ambiguous/missing mappings change the decision to stale/dirty before any copy.
6. Insert target nodes/claims/edges/source links through Phase 13 explicit-version authority. Preserve semantic node/claim checksum identity while recomputing target scope/link/edge/manifest components where version/build lineage changes.
7. Persist a normalized carry result referencing parent semantic keys/checksums and target keys/checksums. Exact retry is idempotent; conflict fails closed.

Do not copy parent manifest, seal, validation report, Phase 14 checkpoints, call attempts, budget rows or retrieval caches. The target gets a fresh DB-derived manifest only after carried and rebuilt rows are complete.

## Phase 14 Dirty-stage Integration

Translate the immutable plan into a stage mask:

- Rebuild items with `decision='carried'` contain source/target semantic checksums and resolve clean dependencies without creating Phase 14 stage, reservation, call or cache-write rows.
- `dirty` stages use normal Phase 14 packages and gateway. Oracle cannot authorize a call; Phase 14 rechecks target Phase 12 eligibility and all budgets.
- `stale_blocked` prevents sealing and cannot silently become carried.
- Parent stages run only after every child is resolved either by a validated carry-forward rebuild item or a completed Phase 14 dirty stage under the target version.

The builder must prove no clean stage reaches package generation/provider reservation and no dirty stage is skipped because a parent cache happened to exist under incompatible lineage.

## Durable Rebuild Authority

Add an isolated `backend/app/models/narrative_memory_rebuild.py` and migration with three append-only/candidate-only tables:

- `narrative_memory_rebuild_plans`: owner/novel, parent/target version, old/new source/hierarchy/boundary/policy checksums, plan checksum, created timestamp.
- `narrative_memory_rebuild_items`: plan-scoped semantic asset key/kind/range, decision, direct reasons, propagated-from keys, old/new checksums and dependency checksum.
- `narrative_memory_reuse_reports`: plan/target manifest, rebuilt/carried/stale counts/ranges, actual calls/tokens/cost, deterministic full-rebuild upper bound, avoided upper bound, cache reuse and report checksum.

All three reject UPDATE/DELETE. They create no run selector, active key, pointer, promotion or consumer binding. Mutable execution remains Phase 14-owned.

## Reuse Economics

Use separately labeled quantities:

- **Observed actual:** settled Phase 14 call count, input/output tokens and cost for dirty stages; exact-cache hits and carry-forward rebuild items counted separately.
- **Full-rebuild call upper bound:** number of all Chapter/Arc/Global stages that would require generation absent carry/cache, derived from the same target boundary plan.
- **Token/cost upper bound:** deterministic sum of the same target packages' frozen reservation envelopes and price snapshot, not historical average and not provider billing.
- **Avoided upper bound:** full-rebuild upper bound minus observed/reserved dirty-stage work, floored at zero and reported with formula inputs.
- **Carry reuse:** semantic nodes/claims/links copied without calls.
- **Cache reuse:** dirty stages that avoided transport through exact validated Phase 14 cache.

Tests should also run a controlled full-rebuild fixture with the same transport/pricing so reported formulas can be checked against observed calls, while production reports retain honest `upper_bound` labels.

## Planned File Boundary

```text
backend/app/models/narrative_memory_rebuild.py
backend/migrations/versions/16_narrative_memory_rebuild_authority.py
backend/app/services/narrative_memory/
  rebuild_contracts.py
  dependency_graph.py
  change_oracle.py
  carry_forward.py
  rebuild_executor.py
  reuse_report.py
backend/scripts/run_narrative_memory_rebuild.py
backend/tests/unit/narrative_memory/test_dependency_graph.py
backend/tests/unit/narrative_memory/test_change_oracle.py
backend/tests/unit/narrative_memory/test_carry_forward.py
backend/tests/unit/narrative_memory/test_reuse_report.py
backend/tests/integration/narrative_memory/test_rebuild_authority_pg.py
backend/tests/integration/narrative_memory/test_carry_forward_pg.py
backend/tests/integration/narrative_memory/test_local_rebuild_pg.py
backend/tests/integration/narrative_memory/test_reuse_report_pg.py
backend/tests/adversarial/test_narrative_memory_rebuild_safety.py
backend/tests/ci/test_narrative_memory_rebuild_contract.py
```

## Verification Strategy

### Pure and property-style fixtures

- edit/insert/delete/reorder/evidence-remap/boundary/optional-source cases with stable graph/closure hashes and reason ordering;
- no-change graph carries every node including Global; one edit dirties exact lower/parent/global closure;
- uncertainty cases never reduce the dirty set and are monotonic under added unknowns;
- insertion order and DB IDs do not affect semantic decisions.

### PostgreSQL and concurrency

- migration round-trip and append-only/scope constraints;
- parent/target cross-scope, unsealed parent, target conflict, stale plan and ambiguous leaf mapping all fail closed;
- carry rows preserve semantic checksum identity and target direct leaf closure;
- concurrent carry/retry is idempotent and cannot seal incomplete targets;
- oracle/carry/report paths produce zero provider attempts, reservations, embedding/index writes and pointer changes.

### Integrated dirty execution

- Phase 14 receives only frozen dirty stages; carry-forward rebuild items have zero Phase 14 stage/gateway/reservation records;
- single chapter edit runs exactly chapter + containing parent + Global under stable boundaries;
- insert/delete/reorder/boundary uncertainty expands conservatively as declared;
- target manifest recomputation succeeds only after all carry/rebuild decisions are resolved;
- Phase 15 explicit target retrieval still revalidates citations and remains default-off/no Reader Chat cutover.

## Risks and Cautions

1. **Global source snapshot changes globally.** Reuse cannot require the old and new book-level snapshot hashes to be equal; it must prove local evidence equivalence and record the new target lineage while preserving semantic content checksums.
2. **Build/node IDs may change.** Carry-forward needs an exact evidence fingerprint mapping, not ID equality or fuzzy text matching.
3. **Checksum language must be precise.** Preserve semantic content checksums; recompute scope/link/manifest checksums that legitimately bind the target version/build.
4. **Append-only means no repair in place.** Any conflicting target row or stale plan blocks the target/new version; never mutate the parent.
5. **Cost savings can be overstated.** Separate observed values from deterministic upper bounds and expose formulas/price snapshots.
6. **Do not make the oracle a hidden provider caller.** Static capability scans and call-attempt before/after observers must prove zero provider/embedding access.
7. **Do not use retrieval telemetry as dependency truth.** Phase 15 remains a consumer regression only.

## Planning Conclusion

Phase 16 can safely reduce work only when equivalence is proven at the direct evidence and typed semantic levels. The correct fallback for uncertainty is more rebuilding, never optimistic reuse. The output is a sealed explicit target candidate plus an auditable reuse report; it remains outside production selection and cannot make a Phase 17 quality verdict.
