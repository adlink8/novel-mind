---
gsd_state_version: 1.0
milestone: v1.1
milestone_name: 工程与治理基线收口
status: roadmap_planned
last_updated: 2026-07-26T00:00:00.000Z
last_activity: 2026-07-26
progress:
  total_phases: 35
  completed_phases: 21
  total_plans: 123
  completed_plans: 84
  percent: 68
last_shipped_milestone: v0.8
last_shipped_verdict: achieved_candidate_scope
---

# Project State

## Current Position

Milestone: **v1.1 工程与治理基线收口**（Phase 21–25 + 25.1）  
Phase 21: **COMPLETE**（2026-07-26 追认与文档一致性，见 `phases/21-settings-routing-usage-and-debtfix/`）  
Phase 22: **NEAR-COMPLETE** — PR #13 五类根因修复合入（`9f01680`），master CI 全绿（run 30204817945）；分支保护 `ci-gate` required + enforce_admins 已设；**余 3 个 nightly 全绿观察（≈2026-07-29 核销）**  
Phase 25.1: **新立项（2026-07-26）** — 分析页对话工作台；设计契约（同底座/同剧透边界/不同锚点）已写入 ROADMAP  
Next: `/gsd-plan-phase 23`（Layer Registry ADR）；25.1 可在 23/24 期间并行做 UI 侧  
Last activity: 2026-07-26

> 工作流变化：分支保护生效后，**所有进 master 的变更（含文档）必须走 PR 且 ci-gate 绿**，管理员亦不可绕过。

## 历史位置（v1.0，2026-07-17）

Phase: **20-structure-workspace-multilayer-presentation**  
Status: **PHASE COMPLETE (P0)** + **2026-07-17 ordered follow-on executed**

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

## Deferred / next（2026-07-26 已全部归入 Phase 21–34）

- Resume NM build to arc/global（no promote）→ **Phase 26**
- Clue worker gate 已修（`2cf8562`）；title builder（short_title）→ **Phase 25**；payoff 生产重跑 → **Phase 27**
- v0.3 RAG eval residual + Phase 10 Playwright residual → **Phase 28**
- master CI 全红恢复 + 分支保护生效 → **Phase 22**
- phase21 追认与文档漂移 → **Phase 21**
