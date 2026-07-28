---
phase: 11-clue-and-foreshadow-tracking
plan: "02"
subsystem: pipeline
tags: [clue, foreshadow, candidate-recall, evidence-package, llm-judge, gates, phase09-protocol]

requires:
  - phase: 11-clue-and-foreshadow-tracking
    provides: clue schemas (ClueSemanticJudgment, lifecycle validators) and PostgreSQL authority from 11-01
  - phase: 07-semantic-hierarchical-chunking
    provides: hierarchy evidence nodes, build_id/checksum, source offsets
  - phase: 09-dynamic-character-relationship-graph
    provides: optional read-only public reader surface (bound only when present)
provides:
  - Deterministic ClueCandidateRecallService with stable candidate IDs and package hashes
  - Bounded ClueEvidencePackage (cue + later windows, trim, omitted IDs)
  - VersionedRelationshipObservationSource with explicit source_unavailable vs empty
  - Selection/citation accept + free-form chat reject (chat never fact source)
  - Strict ClueLLMJudgeService (no DB/tools/writes; caller-controlled repair only)
  - ClueGateService ordered gates with zero side effects; adversarial critical false=0
affects:
  - 11-03 durable worker and spoiler API
  - 11-04 analysis workspace UI
  - 11-05 qualification and release gate

tech-stack:
  added: []
  patterns:
    - "Recall signals (vector/lexical/adjacency/entity/timeline/relationship) never accept state alone"
    - "Phase 09 outage → source_unavailable status distinct from healthy empty"
    - "LLM repair is explicit caller flag; provider_retries=0; no hidden retries"
    - "Gates are pure GateDecision for later lifecycle persistence"

key-files:
  created:
    - backend/app/services/clues/__init__.py
    - backend/app/services/clues/sources.py
    - backend/app/services/clues/candidates.py
    - backend/app/services/clues/evidence.py
    - backend/app/services/clues/llm_judge.py
    - backend/app/services/clues/gates.py
    - backend/prompts/clue_semantic_judge.v1.txt
    - backend/tests/unit/clues/test_candidates.py
    - backend/tests/unit/clues/test_llm_judgment.py
    - backend/tests/unit/clues/test_evidence_gates.py
    - backend/tests/integration/clues/test_source_protocols.py
    - backend/tests/adversarial/test_clue_false_positives.py
  modified: []

key-decisions:
  - "Null/unbound Phase 09 reader records source_unavailable, never empty-success zero-signal substitute"
  - "Judge repair only via judge_package(repair=True); no auto-repair inside primary call"
  - "No AsyncSession lifecycle writes in services package; persistence deferred to 11-03"
  - "Primary selection/citation refs accepted as locators only; free-form chat hard-rejected"

patterns-established:
  - "ClueEvidencePackage.to_llm_payload freezes allowed IDs/classifications and marks novel text untrusted"
  - "Gate order: scope→schema→evidence→offset/hash→temporal→transition→threshold/conflict→human-protection"
  - "stable_candidate_id from sorted cue/later/reason_codes via SHA-256"

requirements-completed: [REQ-CLUE-01, REQ-CLUE-02, REQ-CLUE-03, REQ-CLUE-07]

duration: 45min
completed: 2026-07-15
---

# Phase 11 Plan 02: Cross-Chapter Candidate Recall, Evidence Packages, and LLM Gates Summary

**Deterministic cross-chapter clue recall with bounded cue/later packages, Phase 09 `source_unavailable` protocol, strict no-write semantic judge, and side-effect-free lifecycle gates (critical false active/paid_off = 0).**

## Performance

- **Duration:** 45 min
- **Started:** 2026-07-15T01:30:00Z
- **Completed:** 2026-07-15T02:15:00Z
- **Tasks:** 3
- **Files modified:** 12

## Accomplishments

- `ClueCandidateRecallService` builds reproducible candidate IDs, package hashes, and ordering from hierarchy nodes + optional timeline/vector/relationship signals.
- `ClueEvidencePackage` enforces cue (≤3) / later (≤8, ≤4 chapters) bounds with deterministic trim and omitted-ID recording.
- Phase 09 protocol: null/outage → `source_unavailable`; healthy zero rows → `empty`; free-form chat rejected; selection/citation only.
- `ClueLLMJudgeService` returns DTO/audit only; tools/stream/extra fields fail; provider retries frozen at 0; repair is caller-controlled.
- `ClueGateService` enforces paid_off from reinforced + cue+later order, motif/order/entity conflicts, human-protected dismissal, relation-ref blocks.

## Task Commits

1. **Tasks 1–3: candidates/sources/evidence + judge + gates/adversarial** - `c61bf87` (feat)

**Plan metadata:** (this SUMMARY + STATE/ROADMAP docs commit follows)

## Files Created/Modified

- `backend/app/services/clues/sources.py` — relationship + citation protocols
- `backend/app/services/clues/candidates.py` — `ClueCandidateRecallService`
- `backend/app/services/clues/evidence.py` — `ClueEvidencePackage` / units
- `backend/app/services/clues/llm_judge.py` — strict async judge adapter
- `backend/app/services/clues/gates.py` — pure gate pipeline
- `backend/app/services/clues/__init__.py` — package exports
- `backend/prompts/clue_semantic_judge.v1.txt` — versioned fiction-only prompt
- unit/integration/adversarial tests listed in frontmatter

## Decisions Made

- Outage vs empty are distinct statuses so later workers/APIs cannot treat unavailable Phase 09 as “no relationships.”
- No import of `reader_chat` or relationship workers into clue services; optional bind via callable only.
- Gate decisions carry stable machine-readable failure codes for 11-03 persistence.

## Deviations from Plan

None - plan executed as written (service package only; no durable worker yet).

## Issues Encountered

None

## Commands and Test Results

```text
cd backend
.\.venv\Scripts\python.exe -m pytest tests/unit/clues/test_candidates.py tests/integration/clues/test_source_protocols.py tests/unit/clues/test_llm_judgment.py tests/unit/clues/test_evidence_gates.py tests/adversarial/test_clue_false_positives.py -q -x
# 38 passed
```

Service scan: no `session.add` / `db.add` / `ClueLifecycleEvent(` write constructors in `app/services/clues/`.

## Verification Mapping

| Must-have | Evidence |
|---|---|
| Deterministic candidate IDs / package hashes | `test_same_inputs_produce_stable_candidate_ids_hashes_and_order` |
| LLM no write / no hidden retries | `test_zero_hidden_retries_and_explicit_repair_is_caller_controlled`, source guards |
| paid_off needs cue+later+from reinforced | `test_paid_off_requires_cue_later_and_from_reinforced` |
| Phase 09 absence explicit | `test_null_source_records_source_unavailable_not_empty`, unavailable vs empty |
| Phase 10 chat not fact source | `test_selection_citation_accepted_freeform_chat_rejected` |
| Critical false active/paid_off = 0 | `test_adversarial_false_active_and_paid_off_count_is_zero` |

## Next

- Execute `11-03-PLAN.md` (durable worker, versioning, budgets, overrides, owner/spoiler API).
- Consume `GateDecision` for append-only lifecycle persistence; keep chat out of evidence.
- Preserve `source_unavailable` end-to-end when Phase 09 reader is down.

---
*Phase: 11-clue-and-foreshadow-tracking*
*Completed: 2026-07-15*
