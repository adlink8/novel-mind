# Phase 12 Research: Read-only Asset Audit and Eligibility

**Date:** 2026-07-15
**Scope:** V08-AUDIT-01..04 only

## Recommendation

Implement Phase 12 as a new `app.services.narrative_memory.audit` sidecar. Keep the core evaluator pure and dependency-injected: adapters return immutable inventory records, while the evaluator assigns one eligibility status and stable reason codes. PostgreSQL adapters are added only after the contracts are proven. Do not add tables, migrations, model gateways, repair paths, or active-pointer helpers in this phase.

## Existing assets to reuse

- `app.models.chunk_build.ChunkBuild`, `ChunkActivePointer`, and `ChunkHierarchyNode` are the Phase 07 PostgreSQL authority for the required hierarchy.
- `app.services.chunking.pg_store.get_active_build_id`, `get_build`, and `load_hierarchy_trees` already expose read paths. The existing loader silently skips a chapter without a chapter node, so an audit adapter must load/count raw rows or otherwise preserve malformed-state evidence rather than treating it as absent.
- `app.services.chunking.hierarchy.validate_hierarchy_invariants` contains useful structural rules, but the persisted audit must additionally check build/novel scope, parent/child symmetry, source offsets, content hashes, coverage, manifest identity, and source snapshot identity against authoritative rows.
- Timeline authority is `TimelineActivePointer` plus timeline event/evidence rows; `app.services.timeline.query.resolve_version_id` is an owner/novel-aware read pattern.
- Relationship authority is append-only `RelationshipBuildRun` and accepted `RelationshipObservation`/`RelationshipEvidenceLink`; `RelationshipQueryService.resolve_version` and `list_accepted_observation_refs` demonstrate owner/version/cutoff-aware reads. There is no Phase 09 active-pointer table, so status must be derived from its real build/run/version contract rather than invented.
- Clue authority is `ClueAnalysisVersion`, `ClueActivePointer`, lifecycle/evidence rows; its source adapters distinguish `source_unavailable` from a healthy empty result.
- Existing tests under `tests/unit/chunking`, `tests/integration/chunking`, `tests/integration/timeline`, `tests/integration/relationships`, and `tests/integration/clues` provide fixtures and invariant examples.

## Confirmed gaps

1. There is no cross-asset, owner-scoped eligibility report or common status/reason-code contract.
2. Existing Phase 07 read helpers reconstruct valid-looking trees and can discard malformed persisted chapters; audit requires lossless inventory and explicit anomalies.
3. `ChunkActivePointer` is novel-scoped, not owner-scoped. Ownership must be proved by joining the target `Novel` and then constraining every loaded build/node to that novel; never accept a caller-supplied build alone.
4. Phase 07 stores source snapshot and manifest hashes on `ChunkBuild`, but persisted node rows do not carry the snapshot. Exact reuse therefore requires recomputation and cross-checking against authoritative chapters/chunks; inability to prove exactness must not become `reusable_exact`.
5. Timeline, relationship, and clue have different version authorities. A shared adapter protocol is appropriate; a shared fabricated version model is not.
6. No current guard exposes a simple `required_assets_reusable` predicate for Phase 14 to check before provider invocation.

## Proposed file boundary

```text
backend/app/services/narrative_memory/
  __init__.py
  audit_contracts.py     # enums, reason codes, inventory/report schemas, guard
  audit.py               # pure deterministic evaluator
  audit_sources.py       # read-only protocols and in-memory fixtures first
  audit_pg.py            # Phase 12-02/03 PostgreSQL adapters

backend/tests/unit/narrative_memory/
  test_audit_contracts.py
  test_audit.py

backend/tests/integration/narrative_memory/
  test_audit_pg.py
  test_audit_no_side_effects.py
```

Do not place this under `chunking/`: hierarchy is one source, while the report spans timeline, relationships, and clues and is the entry gate for later narrative-memory phases.

## Contract shape

- `AssetKind`: `hierarchy`, `timeline`, `relationship`, `clue`.
- `EligibilityStatus`: exactly `reusable_exact`, `rebuild_required`, `blocked`, `optional_unavailable`.
- `AssetRequirement`: `required` or `optional`.
- `ReasonCode`: closed string enum. Begin with source missing/unavailable, owner/novel mismatch, active version missing, snapshot mismatch, manifest mismatch, malformed hierarchy, invalid offset/hash, incomplete coverage, and optional lineage mismatch.
- `AssetInventory`: owner/novel, kind, version/build identity, source snapshot/manifest identities when applicable, counts and adapter-observed availability.
- `AssetEligibility`: inventory identity, requirement, single status, sorted unique reason codes, deterministic rebuild chapter/range hints.
- `EligibilityReport`: schema/policy version, owner/novel, ordered asset results, and a derived `provider_calls_allowed` boolean that is true only when every required source is `reusable_exact`.

Pydantic models should use strict enums and `extra="forbid"`. Canonical ordering is required so reports and later manifests are stable.

## Status policy

- Required hierarchy absent or not provably scoped: `blocked`.
- Required hierarchy present but stale/recomputable mismatch with a determinable affected range: `rebuild_required`.
- Required hierarchy fully proved: `reusable_exact`.
- Optional source absent, reader unavailable, or incompatible lineage: `optional_unavailable`.
- Optional source can be `rebuild_required` only when a real existing asset is stale and its rebuild scope is known; optional absence never blocks hierarchy-only eligibility.
- Never collapse unavailable into an empty healthy result.

## Non-negotiable safety invariants

1. Phase 12 modules must not import model gateways, provider clients, promotion modules, pointer setters, repair/reconcile writers, or worker dispatch.
2. PostgreSQL execution is SELECT-only. A negative integration test snapshots active pointer/version rows and relevant counts/checksums before and after audit.
3. The provider guard is evaluated from the report, not from caller assertions. Any unknown status or missing required asset yields false.
4. Audit never normalizes, deletes, backfills, or rewrites malformed rows.
5. Owner and novel scope is established before accepting build/version IDs; inaccessible and cross-owner targets fail closed without leaking asset metadata.

## Plan split

### 12-01 — Contracts and pure evaluator

Create the package, strict schemas, deterministic status policy, source protocols, in-memory adapter, and guard. Unit tests cover all four statuses, optional-unavailable semantics, canonical ordering, unknown/malformed inputs, and provider guard fail-closed behavior.

### 12-02 — PostgreSQL hierarchy/domain inspection

Add lossless Phase 07 inventory and exact invariant checks, plus read-only adapters for Phase 08/09/11 availability and lineage. Test malformed persisted states and minimal rebuild scope.

### 12-03 — Operator entry and side-effect proof

Add owner-scoped CLI/API using existing dependency/auth patterns, stable serialized report, PostgreSQL before/after observer, forbidden-import/call tests, and fixed commands.

## Verification

- Unit: exact enum surface, deterministic report serialization, status precedence, optional semantics, guard false before required exact reuse.
- Integration: owner isolation; missing pointer/build; build/novel mismatch; malformed tree; offset/hash/coverage/manifest mismatch; optional source unavailable; no writes.
- Static capability scan: no imports from gateway/provider/promotion/pointer setters or ORM write operations in the audit package.
- Regression: targeted Phase 07 hierarchy, Phase 08 timeline query, Phase 09 relationship query, and Phase 11 source-protocol tests.

## Planning risks

- Do not claim exact reuse if the current schema cannot prove a property. Return a reasoned block/rebuild state and close the proof gap in 12-02.
- Do not assume all domains share active pointers; model each current authority honestly.
- Avoid making report content itself a new mutable authority. It is a derived, reproducible artifact over existing rows.
