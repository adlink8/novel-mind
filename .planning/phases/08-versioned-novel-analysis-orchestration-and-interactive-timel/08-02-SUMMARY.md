---
phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
plan: 02
subsystem: timeline-ai
tags: [pydantic, litellm, structured-output, evidence, exact-cache, timeline]
requires:
  - phase: 08-01
    provides: durable analysis jobs, model-call audit authority, immutable versions, and budget reservations
  - phase: 07-semantic-hierarchical-chunking
    provides: frozen scene/evidence packages and hierarchy lineage
provides:
  - Strict four-precision timeline extraction schemas with separate narrative and story-time signals
  - Timeline-only structured model gateway with capability checks and one explicitly budgeted repair
  - Evidence-bound chapter extraction, exact cache identity, call-skipped audit, and provisional publication
affects: [08-03, 08-04, timeline-reconciliation, timeline-worker]
tech-stack:
  added: []
  patterns: [local structured-output revalidation, exact lineage cache, script-owned evidence publication gate]
key-files:
  created: [backend/app/services/timeline/model_gateway.py, backend/app/services/timeline/evidence.py, backend/app/services/timeline/extraction.py, backend/prompts/timeline_chapter_extract.v1.txt]
  modified: [backend/app/schemas/timeline.py, backend/app/schemas/__init__.py, backend/app/services/timeline/__init__.py]
key-decisions:
  - "Narrative chapter/index remains independent from exact, relative, fuzzy, or unknown story-time shape."
  - "A schema or business-gate failure permits one same-deployment repair with a separate reservation and audit attempt; provider fallback is forbidden."
  - "Only locally validated, evidence-scoped output enters the exact cache or provisional chapter publication."
patterns-established:
  - "Exact cache identity includes every frozen source, hierarchy, evidence, prompt, schema, deployment, decoding, and config component."
  - "Cache hits create call-skipped audits pointing to the original successful attempt and artifact checksum."
requirements-completed: [REQ-TIME-04, REQ-TIME-05, REQ-TIME-09]
duration: 18min
completed: 2026-07-13
---

# Phase 08 Plan 02: Strict Timeline Extraction Summary

**Strict Pydantic timeline output, same-deployment one-repair gateway, and evidence-bound exact-cache extraction with automatic provisional publication.**

## Performance

- **Duration:** 18 min
- **Started:** 2026-07-13T03:30:00Z
- **Completed:** 2026-07-13T03:48:00Z
- **Tasks:** 3
- **Files modified:** 11

## Accomplishments

- Locked event, participant, evidence, story-time, and cross-event constraint output against extra fields, time smuggling, missing anchors, and invalid offsets.
- Added a dedicated timeline model gateway that checks structured-output capability before budget/network use, disables hidden retries/streaming, revalidates locally, and allows one audited repair without model fallback.
- Built bounded chapter evidence validation and an exact cache whose hits preserve original attempt/checksum lineage while valid events publish automatically as provisional candidates.

## Task Commits

1. **Task 1 RED: strict schema contracts** - `d0fb0f1`
2. **Task 1 GREEN: timeline extraction schemas** - `9f17ec2`
3. **Task 2 RED: model gateway contracts** - `92ee4d7`
4. **Task 2 GREEN: strict timeline model gateway** - `c3b26e2`
5. **Task 3 RED: evidence/cache extraction contracts** - `6043a48`
6. **Task 3 GREEN: evidence-bound chapter extraction** - `895a006`

## Files Created/Modified

- `backend/app/schemas/timeline.py` - Strict extraction output and four story-time precision shapes.
- `backend/app/services/timeline/model_gateway.py` - Structured capability, reservation, local validation, repair, usage, and cost semantics.
- `backend/app/services/timeline/evidence.py` - Frozen evidence package hashing and scope/offset/hash gates.
- `backend/app/services/timeline/extraction.py` - Exact cache, audit lineage, and provisional publication coordinator.
- `backend/prompts/timeline_chapter_extract.v1.txt` - Prompt-injection-resistant chapter extraction contract.
- `backend/tests/unit/timeline/` and `backend/tests/adversarial/test_timeline_evidence.py` - Schema, gateway, cache, publication, and forged-evidence coverage.

## Decisions Made

- Kept the gateway dedicated to `app.services.timeline`; user-modified `ai_service.py` and `analysis_service.py` were not touched.
- Treated provider responses as untrusted until both Pydantic parsing and package-owned evidence validation pass.
- Stored only complete successful output in cache; malformed/refused/unknown outcomes cannot publish or populate cache.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Added the repository-required primary test marker to adversarial evidence tests**
- **Found during:** Task 3 verification
- **Issue:** The test classification gate rejected adversarial tests without one of the repository's primary markers.
- **Fix:** Classified the adversarial evidence suite as unit tests while retaining its adversarial path and behavior.
- **Files modified:** `backend/tests/adversarial/test_timeline_evidence.py`
- **Verification:** Plan target suite passed 23 tests.
- **Committed in:** `895a006`

---

**Total deviations:** 1 auto-fixed (1 blocking issue). **Impact:** Test metadata only; implementation scope and behavior were unchanged.

## Issues Encountered

- Context7 was unavailable locally; LiteLLM's official documentation was checked as fallback for unified completion/streaming behavior. The transport remains injected and fully contract-tested without live provider access.
- Existing pytest timeout configuration warnings remain pre-existing and outside 08-02 ownership.

## Known Stubs

None. Empty collections in `InMemoryExtractionStore` are intentional deterministic test-adapter state, not user-facing placeholders.

## Self-Check: PASSED

- All 11 declared or required-export files exist.
- All six `08-02` RED/GREEN task commits exist.
- Plan verification suite: 23 passed.
- Python compile check passed.
- No unrelated dirty file was staged or modified by this plan.

## User Setup Required

None - no live provider credentials or external configuration were required.

## Next Phase Readiness

- 08-03 can consume validated provisional chapter candidates, exact cache lineage, gateway attempts, and story-time constraints for cross-chapter reconciliation.
- No later Phase 08 plan was executed.

---
*Phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel*
*Completed: 2026-07-13*
