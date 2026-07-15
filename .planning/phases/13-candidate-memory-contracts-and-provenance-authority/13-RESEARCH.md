# Phase 13 Research: Candidate Memory Contracts and Provenance Authority

**Date:** 2026-07-15  
**Scope:** V08-MEM-01..05 only  
**Confidence:** High for repository facts and authority boundaries; medium for the final typed-claim vocabulary, which should remain deliberately small in 13-02.

## Recommendation

Implement Phase 13 as an additive PostgreSQL sidecar under `app.services.narrative_memory`, with its own `narrative_memory_*` tables and no production pointer table or promotion service. Reuse the proven Phase 08/09/11 patterns—owner/novel/version lineage, strict Pydantic validation, canonical manifests, composite scope foreign keys, PostgreSQL append-only triggers—but do not reuse their version/run tables or pointer lifecycle. Per the approved `13-CONTEXT.md`, Phase 13 contains candidate facts and validation only; worker run/stage/checkpoint authority is deferred to Phase 14.

The minimum reliable authority needs an explicit claim table in addition to the roadmap's version/run/stage/node/edge/source-link/report rows. A claim hidden inside node JSON cannot receive a database-enforced source link, cannot be independently checksummed, and cannot prove V08-MEM-03. Free text may be retained only as non-authoritative display metadata; every fact, state, delta, uncertainty and visibility boundary must live in a closed typed claim payload.

Use append-only sealing rather than a mutable publication status. The version row freezes identity and lineage. Graph rows are inserted under that version. An immutable manifest row seals the exact sorted database contents. Validation reports are append-only observations of a sealed manifest. No row in this package is an active production selection.

## Verified repository baseline

### Current database and migration authority

- The live single Alembic head is **`11cluetrack01`**, verified with `python -m alembic heads`. Phase 13's migration must use `down_revision = "11cluetrack01"`; plan text or historical phase numbers are not sufficient evidence.
- `ChunkBuild`, `ChunkActivePointer` and `ChunkHierarchyNode` are the Phase 07 PostgreSQL authority. Hierarchy levels are exactly `chapter`, `scene`, and `evidence`. Phase 13 must not add memory levels to `ChunkHierarchyNode.level`.
- A hierarchy leaf is identified by `(build_id, node_id)`, and persisted rows carry `novel_id`, `chapter_id`, chapter number, parent, offsets, content and content hash. `ChunkBuild` carries source snapshot hash, hierarchy manifest checksum and chunker lineage.
- `Chapter.content` is the authoritative raw text. Current Phase 12 code already demonstrates server-side re-slicing and checks `content_hash(node.content)`, offset bounds, and `Chapter.content[start:end] == evidence.content`.
- Phase 07 validation already checks one chapter root, scene/evidence parent kinds, parent walks for cycles and non-overlapping evidence offsets. Phase 13 needs analogous cross-chapter range and memory-DAG validation without changing Phase 07 rows.

### Reusable lifecycle patterns

- Phase 08 `AnalysisVersion` records source snapshot, hierarchy build/checksum, prompt/schema/model/decoding/config lineage and manifest data. `AnalysisRun` and chapter stages provide durable mutable execution state. These are timeline-owned and must not be shared because their active-run uniqueness and publication semantics are domain-specific.
- Phase 09 relationship accepted observations and protective overrides are physically append-only through PostgreSQL `BEFORE UPDATE OR DELETE` triggers. It also shows normalized evidence links with chapter offsets and content hashes.
- Phase 11 deliberately uses clue-owned version/run tables rather than timeline's `AnalysisRun/AnalysisVersion`, then recomputes a canonical manifest before pointer movement. Its lifecycle events, overrides and pointer journal are append-only. This validates the same isolation decision for narrative memory, but Phase 13 must stop before the pointer layer.
- Existing manifest implementations serialize sorted components with canonical JSON (`sort_keys=True`, compact separators) and SHA-256. Phase 13 should centralize one narrative-memory canonicalizer rather than import a promotion module.
- `knowledge_unit.py` already demonstrates composite `(owner_id, novel_id, id)` unique keys and scoped foreign keys. This is the strongest existing pattern for preventing a valid ID from being attached to the wrong tenant or novel.

### Phase 12 dependency condition

Phase 12 establishes the correct read-only eligibility seam and a derived `provider_calls_allowed` guard. Its final `12-VERIFICATION.md` is `passed` with 4/4 requirements and 10/10 plan truths verified. Gap-closure commit `d6f1f93` requires the clue active-pointer target to be exactly `validated`; adversarial `candidate` and `superseded` cases pass. The Phase 12 prerequisite gate is therefore satisfied. Phase 13 must consume its eligibility report and must not recreate or weaken Phase 12's policy.

## Minimal PostgreSQL authority

All authority tables should repeat `owner_id`, `novel_id`, and `version_id` and constrain them with composite foreign keys. Redundant scope is intentional: it makes cross-tenant/cross-novel joins invalid at insertion time rather than relying on every future query to remember filters.

### 1. `narrative_memory_versions`

Immutable identity and frozen build inputs:

- `id`, `owner_id`, `novel_id`, `version_key`; unique `(owner_id, novel_id, version_key)` and `(owner_id, novel_id, id)`.
- `source_snapshot_hash`, `hierarchy_build_id`, `hierarchy_checksum`.
- `eligibility_policy_version` and `eligibility_report_checksum` from Phase 12.
- `prompt_hash`, `schema_hash`, `model_lineage`, `decoding_hash`, `config_hash`, and optional-source lineage/checksums for timeline, relationship and clue.
- `parent_version_id` is optional and scoped to the same owner/novel. It is lineage only, never an active fallback.
- No `active_key`, `is_active`, `published`, pointer revision, promotion status or consumer binding.

The version row should be protected from `UPDATE` and `DELETE` immediately. Do not store a later-mutated `manifest_checksum` on it; the separate seal row below preserves the manifest while keeping version lineage physically immutable.

### 2. `narrative_memory_nodes`

One row per memory node:

- Closed `node_kind`: `chapter_state`, `story_arc`, `volume`, `global_story`.
- `chapter_start` and `chapter_end` are inclusive narrative chapter numbers; both positive and `end >= start`.
- `chapter_state` requires `start = end`. `story_arc` and `volume` must be contiguous ranges by construction and validation. `global_story` must equal the frozen source's minimum/maximum chapter range.
- `node_key`, `schema_version`, `content_checksum`, model lineage checksum, and optional non-authoritative `display_label`.
- Unique `(owner_id, novel_id, version_id, node_key)` and scoped `(owner_id, novel_id, version_id, id)`.

Do not persist a fact-bearing summary blob on the node. If a display summary is eventually needed, it must be explicitly marked non-authoritative, excluded from claim APIs, and its checksum included in the manifest so it cannot drift.

### 3. `narrative_memory_claims`

One immutable authoritative claim/delta per row:

- Scoped FK to its node and version.
- Closed `claim_kind`, `schema_version`, discriminated `typed_payload` JSONB, `uncertainty`, `confidence`, `visible_from_chapter`, and `claim_checksum`.
- Unique `(owner_id, novel_id, version_id, claim_key)` and checksum index.
- `confidence` constrained to `[0,1]`; visibility positive and within the owning node/source range.
- The JSONB is accepted only after strict Pydantic validation. PostgreSQL constraints protect common scalar invariants; the manifest and validator protect the closed type-specific contract.

The initial closed union should be small:

- `entity_state`: typed entity reference, state dimension, prior/current typed value and change kind.
- `event_fact`: actors/entities, event kind and narrative position/range.
- `relationship_delta`: typed endpoints, relationship kind and transition.
- `clue_delta`: logical clue reference and typed transition, without importing clue prose as authority.
- `world_state_delta` and `open_loop_delta`: typed subject/scope and transition.

Each variant must use `ConfigDict(extra="forbid", strict=True, frozen=True)` and a discriminator. Unknown fields, coercion, unknown enum values and untyped arbitrary dictionaries fail closed. `statement`/`summary` text is presentation only and cannot replace the typed fields.

### 4. `narrative_memory_edges`

Edges express containment/derivation between nodes in the same scoped version:

- Composite FKs for both endpoints; `source_node_id <> target_node_id`.
- A closed edge type, initially `contains` and, only if needed by 13-02, `derives_from`.
- `edge_checksum` and `model_lineage_checksum`; the latter must resolve to the full frozen lineage on the scoped version.
- Unique `(version scope, source_node_id, target_node_id, edge_type)`.
- Parent range must contain child range. Allowed containment transitions are only `global_story`→`story_arc|volume` and `story_arc|volume`→`chapter_state`. Global cannot bypass the verified middle level and connect directly to chapter state; avoid an unconstrained generic graph.
- A deferred PostgreSQL constraint trigger should reject cycles using a recursive CTE. Application validation is still required for useful error reports, but an application-only DAG guarantee is insufficient for the success criterion.

### 5. `narrative_memory_source_links`

Every claim must have one or more exact leaf links:

- Scoped FK to `claim_id`, plus `hierarchy_build_id`, `evidence_node_id`, `chapter_id`, `chapter_number`, `source_start`, `source_end`, `content_hash`, `source_snapshot_hash`, and source/model lineage checksums.
- FK `(hierarchy_build_id, evidence_node_id)` to the existing unique Phase 07 leaf identity.
- `source_start >= 0` and `source_end > source_start`; unique claim/evidence/range identity.
- `source_kind` may distinguish `hierarchy`, `timeline`, `relationship`, and `clue`, but every kind must still carry the final Phase 07 leaf locator. Optional domain IDs are enrichment lineage, not substitutes for raw evidence.
- Every link carries its own checksum and `model_lineage_checksum`; the scoped version stores the complete canonical lineage object. Nodes, claims and edges follow the same checksum-to-version rule so V08-MEM-04 does not depend on an implicit join convention.

Because existing Phase 07 tables do not expose owner in their unique leaf key, an insertion/validation trigger must additionally prove: version hierarchy build equals the link build; build novel equals scoped novel; node is `evidence`; node chapter equals link chapter; and the version/build source snapshot hashes agree. Do not alter Phase 07 schema merely to make this FK wider.

### 6. `narrative_memory_manifests` and `narrative_memory_validation_reports`

- Exactly one seal per version, with manifest schema version, sorted component counts/hashes, final checksum and `sealed_at`. The seal is insert-only.
- Manifest components must include version lineage, nodes, claims, edges and source links. Ordering keys are explicit and stable; database rows, not worker output, are the input.
- Validation reports are append-only and bind `version_id`, `manifest_checksum`, validator/policy version, verdict (`qualified_candidate` or `blocked`), sorted reason codes and observed counts.
- Inserting a seal activates a PostgreSQL guard that rejects further inserts for that version. All graph/claim/link/manifest/report authority rows reject updates/deletes; a failed or incomplete version is retained for audit and a new version is created for retry.

## Provenance closure and manifest gate

Validation order should be deterministic and fail closed:

1. Resolve owner/novel/version and verify the frozen Phase 12 eligibility report checksum. Required hierarchy must be `reusable_exact`; optional assets remain optional and cannot weaken hierarchy closure.
2. Verify version lineage against `ChunkBuild`: novel, immutable committed build state, source snapshot hash, hierarchy build ID and hierarchy checksum.
3. Load all scoped nodes, claims, edges and source links losslessly. Reject foreign-scope rows, unknown kinds/schema versions, duplicate logical keys and unsealed partial reads.
4. Validate node chapter ranges, allowed level transitions, full chapter-state coverage expected by the candidate, middle-level continuity/coverage, containment and DAG acyclicity. Sibling arc/volume ranges under a global node must have no overlap or unexplained gap. A global node may connect only to already validated arc/volume nodes.
5. Strictly re-validate each claim payload. Reject extra fields, coercions, summary-only facts, invalid uncertainty/confidence/visibility and source references outside the frozen package.
6. Require at least one source link for every claim. For every link, load the Phase 07 evidence node and authoritative `Chapter`; verify owner through the novel, novel/build/chapter identity, evidence level, offsets, `Chapter.content[start:end]`, evidence content/hash, link hash and source snapshot.
7. For timeline/relationship/clue enrichment, validate the optional domain row and its lineage, then continue to the same Phase 07 leaf. A domain row with no exact leaf closure is unusable.
8. Recompute the canonical manifest from sorted database rows and compare it with the immutable seal. A self-reported worker checksum never qualifies the version.
9. Insert an append-only validation report. Neither success nor failure changes a production pointer.

The direct claim→leaf requirement is intentional. Node edges explain hierarchy, but they must not be the only evidence mapping for an upper claim; otherwise a broad parent summary could inherit unrelated child evidence and appear grounded.

## No-production-pointer boundary

The repository already has production selectors named `chunk_active_pointers`, `timeline_active_pointers`, `clue_active_pointers`, `narrative_active_pointers`, and `active_baselines`. Phase 13 must create none of the following:

- `narrative_memory_active_pointers` or any equivalent current/production selector;
- promotion, rollback, CAS revision or pointer journal APIs;
- imports from timeline/clue promotion modules or existing pointer setters;
- Reader Chat consumers, context sources, citations, text or similarity-derived facts;
- changes to existing timeline, relationship, clue, chunk or narrative-unit consumers.

Candidate creation and validation endpoints may return an explicit `version_id` only. Reads must require that ID; there is no implicit "current memory" resolution. Static forbidden-import tests and database before/after snapshots should prove the boundary rather than relying on naming conventions.

## Constraints and indexes to require in 13-01

- Composite unique scope keys on versions, nodes and claims, followed by composite FKs on every child/endpoint.
- `RESTRICT` deletion from immutable candidate authority and Phase 07 evidence; do not cascade-delete proof rows when source metadata changes.
- Check constraints for closed statuses/kinds, positive chapter numbers, ordered ranges/offsets, confidence bounds, distinct edge endpoints and 64-character checksum fields.
- Indexes: version scope/key; node version/kind/range; edge version/source/target; claim version/node/kind/visibility; source-link claim and `(hierarchy_build_id,evidence_node_id)`; manifest/report version/checksum.
- Append-only triggers for version, node, claim, edge, source-link, manifest and report rows.
- Seal guards preventing late inserts into content tables; deferred cycle/range validation for edge batches.

## Proposed implementation boundary

```text
backend/app/models/narrative_memory.py
backend/app/services/narrative_memory/
  contracts.py              # strict discriminated unions and canonical DTOs
  authority.py              # scoped candidate writes; no provider/pointer imports
  provenance.py             # DAG/range/leaf re-slice validation
  manifests.py              # sorted DB snapshot and SHA-256 seal
backend/migrations/versions/13_narrative_memory_authority.py
backend/tests/unit/narrative_memory/
  test_contracts.py
  test_manifests.py
  test_provenance.py
backend/tests/integration/narrative_memory/
  test_candidate_authority_pg.py
  test_provenance_pg.py
  test_no_pointer_side_effects.py
```

Keep Phase 12 audit files intact. Phase 13 consumes their report contract and adds candidate authority beside it.

## Plan split

### 13-01 — Narrative-memory PostgreSQL authority

Create the independent version/node/claim/edge/source-link/manifest/report models and additive migration from `11cluetrack01`. Add composite scope constraints, indexes, append-only/seal/cycle guards and migration upgrade/downgrade tests. Although the roadmap's initial 13-01 shorthand mentions run/stage, the later approved Phase 13 context explicitly defers worker/checkpoint authority to Phase 14; do not create empty control-plane tables here.

### 13-02 — Strict typed contracts

Implement the smallest closed claim/state-delta union for Chapter State, contiguous Story Arc/Volume and Global Story Model. Add strict parse/serialization, canonical hashes, uncertainty/confidence/visibility rules and negative tests for extra fields, coercion, unknown authoritative fields, free-text-only claims and package-external references.

### 13-03 — Provenance closure and manifest gates

Implement lossless scoped loading, node/range/DAG checks, claim-to-leaf closure, authoritative chapter re-slicing, optional-source lineage adapters, DB manifest recomputation, sealing and append-only validation reports. Add the no-pointer capability scan and fresh-session before/after observer.

## Verification strategy

### Unit and contract tests

- Round-trip every claim variant with strict mode; reject extra keys, numeric/string coercion, unknown enums and arbitrary JSON facts.
- Prove canonical serialization is insertion-order independent and that any lineage/node/claim/edge/link field change changes the manifest checksum.
- Cover range containment, allowed kind transitions, cycles, missing claim evidence, broad chapter-only refs, uncertainty/visibility bounds and same-snapshot closure.

### PostgreSQL integration tests

- Upgrade from and downgrade to the verified `11cluetrack01` head; assert one head after migration.
- Reject cross-owner, cross-novel and cross-version node/claim/edge/link attachments at the database boundary.
- Reject updates/deletes to immutable rows, inserts after seal, cycles, invalid ranges and non-evidence hierarchy nodes.
- Re-slice Unicode chapter content using Python/PostgreSQL-stored offsets exactly as the existing Phase 07/12 contract defines; test out-of-range, stale content and wrong hash/snapshot.
- Seed rows in different insertion orders and prove database-recomputed manifests are identical; mutate a pre-seal component and prove checksum mismatch blocks validation.
- Prove every claim has leaf closure and every source link belongs to the frozen build/snapshot.

### Pointer and capability proof

In a fresh observer session, snapshot complete rows/revisions/checksums for `chunk_active_pointers`, `timeline_active_pointers`, `clue_active_pointers`, `narrative_active_pointers`, and `active_baselines` before and after candidate creation, sealing and validation. Assert byte-equivalent state and no new pointer-like table. Static scans must reject promotion/pointer setters, provider gateways and Reader Chat imports in Phase 13 authority/validation modules.

### Regression set

Run targeted Phase 07 hierarchy, Phase 08 timeline manifest/query, Phase 09 relationship evidence, Phase 11 clue source/manifest and Phase 12 audit tests. Phase 13 must remain a sidecar and cannot change their existing selection behavior.

## Risks and planning cautions

1. **Preserve the now-verified Phase 12 prerequisite.** Commit `d6f1f93` closed the clue active-status false-exact path and final verification passed. Phase 13 must retain the exact `validated` clue-target rule and must not silently exclude or weaken optional-source lineage checks.
2. **JSONB can become an authority escape hatch.** Only discriminated, strictly validated payloads are allowed. Unknown fields and free-text-only facts must block sealing.
3. **Application-only tenant checks are insufficient.** Repeat scope columns and use composite FKs; use PostgreSQL triggers where the existing Phase 07 key cannot express owner/snapshot closure.
4. **A mutable `status` on a supposedly immutable version creates ambiguity.** Keep qualification on append-only reports and final content identity on an immutable manifest seal; future Phase 14 execution status belongs to its worker control plane.
5. **Graph closure is not evidence closure.** Require direct claim-to-leaf links even when parent nodes derive from child nodes.
6. **Offsets are source coordinates, not summary coordinates.** Always re-slice `Chapter.content`; never accept stored snippets, embeddings, similarity scores or model citations as final proof.
7. **Do not name this authority `narrative_*` generically.** Existing `narrative_units`, `narrative_index_builds` and `narrative_active_pointers` are a different publication authority. Use the `narrative_memory_*` prefix consistently.
8. **Failed partial candidates need auditability.** Append-only content means retry creates or resumes missing idempotent rows under the same unsealed version; conflicting rewrites create a new version rather than deleting evidence.

## Planning conclusion

Phase 13 does not require reanalyzing existing assets. It establishes the candidate envelope in which later analysis can run. The decisive architecture is: Phase 07 raw hierarchy remains unchanged; Phase 12 supplies eligibility; Phase 13 owns an isolated immutable memory candidate with explicit typed claims and exact leaf links; Phase 14 may populate it; no v0.8 component can select it as production authority.
