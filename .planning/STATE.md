---
gsd_state_version: 1.0
milestone: v1.0
milestone_name: 结构工作台与多层呈现
status: phase_complete
last_updated: 2026-07-17T10:00:00.000Z
last_activity: 2026-07-17
progress:
  total_phases: 20
  completed_phases: 20
  total_plans: 82
  completed_plans: 82
  percent: 100
last_shipped_milestone: v0.8
last_shipped_verdict: achieved_candidate_scope
---

# Project State

## Current Position

Phase: **20-structure-workspace-multilayer-presentation**  
Status: **PHASE COMPLETE (P0)** + **2026-07-17 ordered follow-on executed**  
Last activity: 2026-07-17 (docs update)

## Authorization boundary (still)

- **Does not** authorize narrative-memory promotion / active pointer
- **Does not** authorize Reader Chat cutover to NM

## Phase 20 P0 (done)

1. NM read-only structure API — `a141bac`
2. Structure Workspace shell — `a3131a9`
3. Scope-bound facets + claims drill — `f0e1e0c`
4. Docs + verification — `132f9b9` / `20-VERIFICATION.md`

## Follow-on ordered steps (2026-07-17)

| # | Item | Status |
|---|------|--------|
| 1 | Hierarchy rebuild novel 91 | **done** — `cb_9f9aee6bf1cb427b`, audit EXIT=0 (`e322c45`) |
| 2 | NM candidate build | **partial** — version 1, ~12 chapter_state; resume ops (`20-NM-BUILD-PARTIAL.md`) |
| 3 | Rel transition honesty UI | **done** — `b986dea` |
| 4 | Timeline server chapter range | **done** — `31be1f6` |
| 5 | Clue live re-judge | **done ops** — v24, payoff still 0 (`20-CLUE-LIVE-REJUDGE.md`) |
| 6 | API UAT smoke | **done** — `20-UAT-API.md` |

Reports: `20-ORDERED-EXEC-REPORT.md`, `20-WAVE-NEXT-REPORT.md`

## Cross-cutting architecture/data governance audit

- **Authoritative issue inventory:** [`ARCHITECTURE-LAYERING-DATA-GOVERNANCE-AUDIT-2026-07-17.md`](./ARCHITECTURE-LAYERING-DATA-GOVERNANCE-AUDIT-2026-07-17.md)
- **Expected target gap:** [`TARGET-GAP-ANALYSIS-2026-07-17.md`](./TARGET-GAP-ANALYSIS-2026-07-17.md) — separately evaluates foundation readiness, v0.8 candidate scope, real-book data readiness, stable understanding product, and the full understanding/creation vision.
- Scope: layer naming, SSOT/projection authority, lineage/rebuild/promotion boundaries, API dual tracks, documentation drift, and current engineering baseline.
- Verdict: **gaps_found**. This audit records issues only and does **not** authorize Narrative Memory promotion, pointer changes, data deletion, or workspace cleanup.

## Sample novel 91 (facts)

- Hierarchy: reusable_exact  
- Timeline: ~1933 events  
- Relationships: ~41 accepted (mostly establish)  
- Clues: active v24, 32 clues, 0 payoff  
- NM: candidate version present, incomplete chapter_state  

## Deferred / next

- Resume NM build to arc/global (hours; no promote)
- Commit/stabilize NM CLI transport WIP if still dirty
- Clue worker gate + title builder for real payoff
- Restart BE after timeline range deploy
- v0.3 RAG eval residual; Phase 10 Playwright residual
