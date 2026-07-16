---
phase: 13-candidate-memory-contracts-and-provenance-authority
plan: 02
subsystem: contracts
tags: [pydantic, canonical-json, eligibility, candidate-authority, fail-closed]
requires:
  - phase: 13-01
    provides: seven-table candidate-only PostgreSQL authority and DB guards
  - phase: 12-read-only-asset-audit-and-eligibility
    provides: EligibilityReport and reusable_exact hierarchy precondition
provides:
  - strict frozen memory/claim/edge/source contracts with closed enums
  - narrative-memory canonical JSON and component SHA-256 helpers
  - explicit-version CandidateAuthority persistence bound to Phase 12 eligibility
affects: [13-03-provenance-manifests, 14-bottom-up-builder]
tech-stack:
  added: []
  patterns: [strict Pydantic DTO, package-local refs, eligibility-bound write seam]
key-files:
  created:
    - backend/app/services/narrative_memory/contracts.py
    - backend/app/services/narrative_memory/authority.py
    - backend/tests/unit/narrative_memory/test_contracts.py
    - backend/tests/integration/narrative_memory/test_contract_authority_pg.py
  modified: []
key-decisions:
  - "Authoritative claims use a closed six-variant discriminated payload; free text is non-authoritative only."
  - "Canonical checksums are computed server-side from validated DTOs; callers cannot supply content checksums."
  - "CandidateAuthority never resolves current/active versions and never imports provider/worker/pointer/chat modules."
patterns-established:
  - "Candidate write seam: create_version(eligibility) then persist_package(explicit version_id)."
  - "Idempotent byte-identical retries before seal; conflicting field values raise CandidateConflictError."
requirements-completed: [V08-MEM-01, V08-MEM-02, V08-MEM-04, V08-MEM-05]
duration: 35min
completed: 2026-07-16
---

# Phase 13 Plan 02: Strict Memory Contracts and Eligibility-Bound Authority Summary

**Closed typed claim language, package-local graph contracts, server-side canonical checksums, and explicit-version persistence that only accepts Phase 12 `reusable_exact` hierarchy lineage—without provider, pointer, or production selection capability.**

## Performance

- **Duration:** ~35 min (Tasks 1–3 across sessions)
- **Started:** 2026-07-15
- **Completed:** 2026-07-16
- **Tasks:** 3
- **Files modified:** 4

## Accomplishments

- Defined frozen strict Pydantic DTOs for version lineage, nodes (chapter_state/story_arc/volume/global_story), six claim variants, contains/derives_from edges, exact source links, and CandidatePackage package-local validation.
- Added one narrative-memory canonical JSON encoder (`sort_keys`, compact separators, Unicode preserved) and SHA-256 helpers for lineage/node/claim/edge/source-link components.
- Implemented `CandidateAuthority` that creates immutable versions from verified EligibilityReport hierarchy lineage and persists validated packages by explicit owner/novel/version_id.
- Proved blocked/rebuild hierarchy reports and wrong-scope reports create no candidate rows; exact retries are idempotent; conflicting retries fail closed.
- Static forbidden-capability scan on `authority.py` rejects provider gateway, worker dispatch, active pointer, promotion, Chroma, Reader Chat, and implicit current-version resolvers.

## Task Commits

1. **Task 1: Define strict memory and provenance contracts** — `a452380` (feat)
2. **Task 2: Canonicalize hashes and enforce package-local references** — `bdc515c` (feat)
3. **Task 3: Persist explicit-version candidates behind Phase 12 eligibility** — `7622889` (feat)

## Files Created/Modified

- `backend/app/services/narrative_memory/contracts.py` — Strict DTOs, closed enums, package validators, canonical checksums.
- `backend/app/services/narrative_memory/authority.py` — Owner-scoped explicit-version create/persist seam.
- `backend/tests/unit/narrative_memory/test_contracts.py` — Round-trip and fail-closed contract matrix.
- `backend/tests/integration/narrative_memory/test_contract_authority_pg.py` — PostgreSQL eligibility-bound persistence and capability scan.

## Claim Union and Canonicalization

### Claim variants (discriminator `claim_kind`)

| Variant | Closed fields (summary) |
| --- | --- |
| `entity_state` | entity_kind, entity_key, dimension, prior/current TypedValue, change |
| `event_fact` | event_kind, actor/object keys, chapter range, outcome |
| `relationship_delta` | endpoints, relationship_kind, prior/current state, change |
| `clue_delta` | clue_key, prior/current ClueState, change |
| `world_state_delta` | subject_key, dimension, prior/current TypedValue, change |
| `open_loop_delta` | loop_key, prior/current OpenLoopState, change |

### Canonicalization rules

- Prefix: `narrative-memory.v1:{component}\n` + canonical JSON
- JSON: `model_dump(mode="json")`, `sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`
- Components: `model-lineage`, `version-spec`, `node`, `claim`, `edge`, `source-link`
- Checksums are always computed from validated DTOs after parse; never accepted from callers

### Fail-closed matrix (unit)

- Extra fields / unknown enums / string-to-number coercion
- Summary-only or free-text authority as claims
- Package-external node/claim/source keys
- Illegal hierarchy transitions and parent range non-containment
- Chapter State non-singleton ranges

## Verification Evidence

- `pytest tests/unit/narrative_memory/test_contracts.py tests/integration/narrative_memory/test_contract_authority_pg.py -q -x` → **20 passed** (16 unit + 4 integration), 0 skip
- `ruff check app/services/narrative_memory/contracts.py app/services/narrative_memory/authority.py tests/unit/narrative_memory/test_contracts.py tests/integration/narrative_memory/test_contract_authority_pg.py` → **All checks passed**

## Decisions Made

- Hierarchy eligibility must be `reusable_exact` with a live immutable non-candidate build; optional timeline/relationship/clue assets are frozen as enrichment lineage only.
- Version identity key is `(owner_id, novel_id, version_key)`; content writes always require the explicit integer `version_id`.
- Idempotent inserts compare authoritative field sets and reject any mismatch before seal; sealed versions reject further content inserts (DB + service).
- Fixed await-in-generator bug: edge/source-link inserts use sequential loops so async insert results are not wrapped as async generators.

## Deviations from Plan

None - plan executed as written. Task 3 finished from authorized WIP.

## Issues Encountered

- Generator expressions containing `await` became async generators and failed `tuple(...)`; replaced with explicit loops.

## User Setup Required

None.

## Next Phase Readiness

- Ready for 13-03 provenance closure, database-row manifests, sealing, structural reports, and no-pointer adversarial proofs.
- Must not add provider calls, active pointers, promotion, Reader Chat, or Phase 14 run/stage tables in 13-03.

## Self-Check: PASSED

- All four implementation/test files exist and are committed.
- Commits `a452380`, `bdc515c`, `7622889` present.
- Targeted unit + PostgreSQL tests and Ruff pass.

---
*Phase: 13-candidate-memory-contracts-and-provenance-authority*
*Completed: 2026-07-16*
