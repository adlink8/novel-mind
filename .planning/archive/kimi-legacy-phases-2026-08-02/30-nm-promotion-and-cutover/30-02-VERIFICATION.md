# Phase 30-02 Verification: Fail-Closed A/B Decision

## Status

**VERIFIED AS BLOCKED — candidate-only; no release A/B verdict and no consumer mutation.**

## Preflight evidence

| Gate | Result | Evidence |
|---|---|---|
| Novel 91 signed quality input | PASS | `backend/evals/results/phase28/novel91-live-rag-quality.json`: `live_quality_passed`, 100/100 reviewed, `quality_comparable=true` |
| Judge calibration/lineage | PASS | `novel91-live-calibration-report.json`: status `passed`, consistency `1.0`, critical false accept `0` |
| Common NM/NU/raw source state | BLOCKED | Formal DB: 1 NM candidate version, 0 `narrative_units`, 0 `narrative_index_builds` for novel 91 |
| Qualification report input | BLOCKED | `narrative_memory_qualification_runs=0`, `narrative_memory_qualification_reports=0` |
| Owner-scoped consumer evidence | BLOCKED | `/analysis?novel=91` against project frontend 3001/backend 8010 returns auth gate `401` without owner-2 session |
| Pointer safety | PASS | `narrative_active_pointers=0`; no promotion/cutover command executed |

## Decision

The Phase 30-02 decision is `blocked_comparable_inputs_missing`. The implementation does not
convert the existing raw RAG quality report into an NM-vs-Narrative-Unit-vs-raw A/B result, and
does not infer owner/spoiler/citation behavior from unit tests. A future run must supply one
signed common fixture, all three candidate paths, cost/source lineage, and owner-scoped browser
evidence before a qualified or regression verdict can be issued.

## Verification commands

- Read-only PostgreSQL counts for the tables above — confirmed on the formal project backend.
- Playwright CLI against `http://127.0.0.1:3001/analysis?novel=91` with the project backend at
  `http://127.0.0.1:8010` — login gate rendered; `/api/auth/me` returned `401 Unauthorized`.
- No pointer, version, ownership, consumer, or remote repository data was mutated by this audit.
