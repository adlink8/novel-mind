# Phase 15 Research: Adaptive Hierarchical Retrieval and Leaf Evidence Safety

**Date:** 2026-07-16  
**Scope:** V08-RETR-01..05 only  
**Confidence:** High for repository authority/safety patterns; medium for final ranking weights, which must remain a frozen Phase 15 policy rather than an implicit tuning surface.

## Recommendation

Implement Phase 15 as a read-only sidecar under `app.services.narrative_memory`. Consume one explicit, structurally valid Phase 13 candidate version populated by Phase 14, but never resolve a production/current pointer. Split the implementation into three seams: a pure deterministic router, a scoped PostgreSQL traversal/leaf resolver, and a default-off offline experiment/audit wrapper. Keep final evidence authority entirely in Phase 07 `ChunkHierarchyNode(level="evidence")` plus authoritative `Chapter.content`.

The central safety rule is **visibility before observability**: apply owner/novel/version/snapshot/cutoff filters before candidate materialization, ranking, counts, fallback classification, cache identity or trace generation. A post-retrieval redaction layer is insufficient because future titles, candidate counts, scores, source availability and timing can leak even when excerpts are removed.

## Verified repository baseline

### Candidate memory authority

- `backend/app/models/narrative_memory.py` defines scoped immutable versions, typed nodes/claims/edges, direct source links, one manifest and append-only structural reports. There is no narrative-memory active pointer.
- `backend/app/services/narrative_memory/contracts.py` supplies strict node/claim/source contracts and canonical checksums; Phase 15 should add retrieval DTOs in a separate module rather than weakening authority contracts.
- Phase 13 plans require every authoritative claim to have a direct Phase 07 evidence leaf and every version read to be explicit. Phase 15 must preserve both invariants and cannot treat graph ancestry alone as proof.
- Phase 14 is the only intended producer of Chapter State → Arc/Volume → Global candidates. Phase 15 is a consumer; it must reject incomplete/failed/unsealed runs rather than complete or repair them.

### Phase 14 builder handoff

- Phase 14's control plane is `backend/app/models/narrative_memory_builder.py` plus `builder_repository.py`, `builder_worker.py`, `arc_planner.py`, `global_builder.py` and `builder_report.py`. It persists owner/novel/version-scoped run, stage/checkpoint, model-call attempt and budget authority while candidate content remains in Phase 13 tables.
- A Phase 15 read is eligible only when the explicit Phase 14 run/stages are complete, the worker artifact checksum equals the Phase 13 database-recomputed manifest, and the Phase 13 seal/structural report exists. A partial/paused/failed run is a fallback input condition, never an invitation for retrieval to resume the builder.
- Phase 14 has no pointer and excludes Reader Chat. Phase 15 must preserve that boundary and read the final candidate by explicit version only.

### Raw hierarchy and citation authority

- Phase 07 hierarchy rows already carry build/node/chapter identity, level, offsets, content and content hash. `backend/tests/integration/test_hierarchical_retrieval.py` only proves an in-memory evidence→scene expansion and raw fallback; it does not establish owner/version/cutoff/manifest safety for narrative memory.
- Phase 12 `audit_pg.py` and Phase 13 provenance design establish the correct final proof: reload the scoped build/leaf and `Chapter`, verify Unicode code-point bounds, re-slice `Chapter.content[start:end]`, and recompute content hash. Stored snippets or summaries are never final authority.
- `NarrativeMemorySourceLink` repeats candidate scope, hierarchy build, evidence node, chapter, offsets, content hash and source snapshot. Phase 15 should join and revalidate all of them; a valid-looking leaf ID alone is insufficient.

### Existing spoiler-safe consumer patterns

- `backend/app/services/reader_chat/context.py` freezes persisted reading progress into a context manifest and excludes future evidence before packing.
- `backend/app/services/reader_chat/retrieval.py` demonstrates visible-set-first hierarchy filtering and bounded source packing, but it resolves active production pointers and belongs to Reader Chat. Phase 15 may mirror its ordering discipline but must not import, call or replace it.
- Timeline/relationship/clue query services already demonstrate owner/novel/version/cutoff filtering. Their summaries and status metadata remain optional domain products and are not necessary to Phase 15 retrieval.

## Proposed contracts

Use frozen strict DTOs in `backend/app/services/narrative_memory/retrieval_contracts.py`:

- `RetrievalScope`: owner_id, novel_id, explicit version_id, source snapshot, hierarchy build/checksum, persisted cutoff snapshot/hash, full-book authorization (false by default), policy version/hash.
- `RetrievalQuestion`: normalized question, optional selected chapter/offsets, optional expected bucket only for fixtures; raw query text is hashed for manifests and never logged.
- `RouteDecision`: `local|arc|global|mixed`, ordered start levels and closed reason codes. No free-text rationale, candidate title or model output.
- `VisibleCandidate`: version-scoped node/claim identity, kind/range/visibility and deterministic score inputs. Only already-visible fields may exist in this DTO.
- `TraversalStep`: generic level, visible candidate key, parent/child relation, visible candidate count, omitted-after-budget count and safe outcome. Do not serialize hidden counts or absent-because-future distinctions.
- `LeafCitation`: chapter/evidence IDs, exact offsets/hash, re-sliced excerpt and frozen build/snapshot lineage. Construction is private to the server-side resolver.
- `RetrievalManifest`: request/policy/cutoff hashes, explicit version/manifest/build lineage, route decision, traversal, fallback reason, final leaf citations and deterministic checksum.

## Deterministic routing

The router should use only normalized query text, explicit selection coordinates and frozen policy tables. It must not inspect candidate titles, summaries, node counts or future data to decide the route.

Recommended precedence:

1. `local`: a valid chapter/selection anchor plus local reference/definition/entity-state intent.
2. `global`: explicit whole-book/theme/overall-trajectory language and an authorized full-book cutoff; otherwise downgrade to mixed or the highest visible scope.
3. `arc`: causal/transition/cross-chapter/arc-range intent with no global request.
4. `mixed`: multiple signals, ambiguous scope, comparison requiring local and upper organization, or safe default.

The output includes stable reason codes such as `selection_anchor`, `local_fact_intent`, `cross_chapter_intent`, `whole_book_intent`, `multiple_scope_signals`, and `safe_default`. Tokenization, normalization, pattern order and tie-breaking belong to a versioned policy whose hash is stored in the retrieval manifest. Routing never calls the provider.

## Visible-set-first candidate loading

Every loader takes the full `RetrievalScope` and uses one lossless scoped query. Required predicates include:

- `version.owner_id`, `novel_id`, explicit `version_id`;
- candidate `source_snapshot_hash`, hierarchy build/checksum and sealed manifest;
- node/claim/edge/link repeated scope and correct candidate version;
- `claim.visible_from_chapter <= cutoff`; `node.chapter_end <= cutoff` for any upper node (an arc crossing the cutoff is not partially exposed); and leaf/chapter numbers within the visible prefix. Global is visible only when the persisted authorization/cutoff covers its complete range;
- structurally successful Phase 13/14 report/run evidence.

Counts, rank features and source statuses are calculated only after these predicates. Cache identity must include owner, novel, explicit version, candidate manifest checksum, source snapshot, hierarchy checksum, cutoff snapshot hash, route policy hash, normalized query hash and budgets. Cache lookup must revalidate all fields; cache output exposes no raw key.

## Descent and fallback algorithm

The traversal is a bounded deterministic DAG walk:

```text
route start levels
  global -> arc/volume -> chapter_state -> claim -> exact source link
  arc    -> arc/volume -> chapter_state -> claim -> exact source link
  local  -> chapter_state -> claim -> exact source link
  mixed  -> bounded union of visible local + upper candidates, then dedupe
                                             |
                                             v
                                  Phase 07 evidence leaf
                                             |
                                             v
                               Chapter.content re-slice/hash
```

- Rank only candidates already admitted to the visible set. Use frozen deterministic features and stable tie-break keys; no provider or embedding is required for this phase.
- If the chosen upper level is absent/partial or yields no valid visible child, collapse to the next visible level using the same scope/cutoff.
- If memory descent yields no valid leaf, query Phase 07 evidence leaves directly under the same frozen build and cutoff (`raw_fallback`). Never widen owner, novel, version, snapshot or cutoff to avoid an empty result.
- Deduplicate final evidence by `(build_id, evidence_node_id, chapter_id, start, end, hash)`. Upper claims may organize/rank leaves but are not returned as citations.
- Record only closed fallback codes such as `upper_absent`, `upper_partial`, `no_visible_child`, `invalid_leaf`, `budget_exhausted`, and `raw_fallback`; do not reveal that a hidden future node exists.

## Citation revalidation

For every proposed leaf, a fresh resolver must:

1. Reload the exact candidate version, seal/report, source link, Phase 07 build/evidence leaf and Chapter under the same scope.
2. Require evidence level, matching build/novel/chapter/snapshot and exact link offsets/hash.
3. Check Python Unicode code-point bounds against `Chapter.content`.
4. Compute `excerpt = Chapter.content[start:end]`, verify equality with frozen evidence content, and recompute the canonical content hash.
5. Construct the citation only after every check succeeds. Invalid leaves are dropped with a safe generic reason; if the policy requires minimum evidence and none survives, the experiment is blocked.

Neither an upper claim nor a stored source-link excerpt can bypass these steps.

## Offline experiment and no-cutover boundary

Add a fixed CLI such as `backend/scripts/run_hierarchical_retrieval_experiment.py`, guarded by a default-false setting and requiring owner, novel, explicit candidate version, frozen question fixture and persisted cutoff. It writes or prints a deterministic experiment manifest/report only; it does not create an HTTP route, background provider job, pointer or production cache alias.

Prove no cutover in two ways:

- Static capability scan: Phase 15 modules do not import Reader Chat conversations/context/worker/gateway, provider gateways, promotion or pointer setters.
- Behavioral observer: snapshot Reader Chat OpenAPI routes and run the existing context/API fixture before and after an offline experiment; request/output/manifest and pointer tables remain byte-equivalent.

## Plan split

### 15-01 — Deterministic router and visible candidate sets

Define strict retrieval contracts, frozen routing policy and local/arc/global/mixed decisions. Implement explicit-version PostgreSQL visible loaders with cutoff-first queries and isolated cache identity. Prove deterministic routing and that future/cross-scope rows cannot affect route, counts, scores, statuses or cache.

### 15-02 — Multi-level descent and revalidated leaf citations

Implement bounded hierarchy descent, mixed union/dedupe, collapsed lower-level recovery and Phase 07 raw fallback. Resolve every final citation through fresh Chapter re-slicing/hash validation and freeze the traversal/citation manifest.

### 15-03 — Audit, adversarial safety and default-off experiment

Add the fixed offline CLI/runner, safe traces and PostgreSQL fresh-observer tests. Cover IDOR/version/cache isolation, future metadata non-interference, broken citations and Reader Chat no-cutover/default-off behavior.

## Verification strategy

- Unit: route matrix, normalization/tie-break determinism, manifest/canonical hashes, descent/fallback state machine and safe trace serialization.
- PostgreSQL: owner/novel/version/snapshot/build/cutoff filters, visible counts/ranking, edge/claim/link traversal, Unicode re-slicing, stale/tampered leaf rejection and isolated cache keys.
- Adversarial: add arbitrary future nodes/titles/scores/source statuses and require byte-identical visible result/trace; attempt cross-tenant/version cache replay; corrupt every citation lineage component.
- Regression: Phase 07 hierarchy, Phase 13 candidate/provenance, Phase 14 builder and Reader Chat context/API tests.
- Capability: no provider calls, active pointer resolution, promotion, new production route or Reader Chat import.

## Risks and cautions

1. **Counts and statuses leak as easily as text.** Compute all metadata after cutoff; never expose hidden/filtered counts.
2. **A candidate cache can become an IDOR.** Include all scope, version, manifest and cutoff hashes in the key and revalidate hits.
3. **Route quality must not weaken safety.** Misrouting falls back downward; it never widens the cutoff or uses an unverified upper summary.
4. **Stored snippets can be stale.** Always rebuild citations from current authoritative Chapter text under the frozen snapshot proof.
5. **Do not import Reader Chat for convenience.** Shared concepts should be locally strict contracts or a lower-level neutral helper only if already present; Phase 15 remains non-production.
6. **Do not claim qualification.** Phase 15 proves mechanics and safety; Phase 17 compares quality/cost and decides only `qualified_candidate` or `blocked`.

## Planning conclusion

Phase 15 can be implemented without reanalyzing books and without changing any product consumer. The correct boundary is an explicit candidate-version, cutoff-first, read-only traversal whose upper layers organize visible candidates but whose final authority is always a freshly revalidated Phase 07 leaf slice.
