# Wave 1.3 — Quality gap map (ordered execution)

**Date:** 2026-07-17  
**Scope:** Relationships honesty + evolution; Clues re-run readiness / lifecycle links / title honesty; Timeline chapter-range server filter  
**Authorization:** No NM promote, no push, no large refactors  
**Status of this pass:** Investigation + plan only. **No code fix landed** (no tiny-safe-with-tests delta found that was smaller than residual ops work).

---

## Executive summary

| Area | Present state | Smallest next fix | Risk | Without promote? |
|------|---------------|-------------------|------|------------------|
| Relationships honesty | Phase 19 API/UI **done** (`edge_kind`, opt-in provisional) | Keep default accepted-only; surface **seed/backfill** as non-pipeline accepted | Low (UI label) / Med (API field) | Yes |
| Relationships evolution | Schema/gates support `change`/`end`; sample + backfill are **establish-only** | Ops: real LLM relationship worker (not backfill); optional transition chip UI | High (LLM/ops) / Low (UI chip) | Yes (ops) |
| Clues readiness | Clamp **in code**; sample runs need **re-run** | Re-run clue worker on target novel; verify titles + counts | Med (ops/LLM cost) | Yes |
| Clues title honesty | Writer path honest; **DB legacy titles** stale | Same re-run (no code) | Low | Yes |
| Clues lifecycle / links | List counts spoiler-filtered; detail links may be looser | Align detail `links` filter with list; show lifecycle length honestly | Low–Med | Yes |
| Timeline chapter range | Structure facet = **client filter** + multi-chapter densify | Optional `chapter_start`/`chapter_end` on timeline GET | Med (contract) | Yes |

**Priority order for execution:** P0 → P1 → P2 (below).

---

## P0 — Unblock product truth on real data (ops + honesty residual)

### P0.1 Clue re-run after clamp (readiness)

**Gap**

- `IMPLEMENTATION-STATUS.md`: candidate build failed on `later windows span more than 4 chapters`; fixed by clamp before package build.
- Fix is **code-complete**; sample novels still need a successful full clue run to produce short titles, plant→payoff, lifecycle, links.

**Evidence (code)**

| Path | Role |
|------|------|
| `backend/app/services/clues/evidence.py` | `clamp_later_units_to_scope`, `MAX_LATER_CHAPTERS` hard fail if unclamped |
| `backend/app/services/clues/candidates.py` | calls clamp before package |
| `backend/tests/unit/clues/test_candidates.py` | clamp unit coverage |
| `backend/app/services/clues/worker.py` | `build_machine_clue_title` (short titles on **new** persist only) |
| `backend/scripts/_rerun_clues_smoke.py` | smoke re-run helper |
| `backend/scripts/run_clue_qualification.py` | qualification + lifecycle_count checks |

**Smallest next fix (ops, not promote)**

1. Pick owner-scoped sample novel (e.g. slime) with hierarchy + timeline lineage intact.
2. Start clue analysis via product API or `backend/scripts/_rerun_clues_smoke.py` / worker path.
3. Acceptance checks after run:
   - Run reaches terminal success (not span-clamp hard fail).
   - List titles are short (`len ≤ 32` style) — not raw cue[:80].
   - At least some `evidence_count > 0`; `payoff_chapter` spoiler-safe under cutoff.
   - Detail `lifecycle` / `payoff_chain` non-empty for paid_off/reinforced where expected.
   - Card `link_count` vs detail `links` length agree under same spoiler cutoff.

**Risk:** Medium (LLM/budget, lineage mismatch).  
**Implementable now without promote:** Yes (ops only).  
**Code change required:** No (unless re-run surfaces a new bug).

---

### P0.2 Relationship provisional vs accepted honesty — residual after Phase 19

**Gap**

- Phase 19 **presentation honesty is implemented**:
  - API: `include_provisional` default false; `edge_kind` ∈ `{accepted_observation, provisional_cooccurrence}`; provisional `relation_type=cooccur`.
  - UI: solid typed vs dashed「共现」; banner; non-assertive evidence.
- Residual product risk: **seed backfill** writes **accepted** KG + observations with `transition="establish"` always. After backfill, graph looks “confirmed” even though edges are ops-seeded, not multi-pass evolution.

**Evidence**

| Path | Role |
|------|------|
| `backend/app/api/relationships.py` | `include_provisional` query |
| `backend/app/services/relationships/query.py` | default accepted-only; provisional when empty or opt-in |
| `backend/app/services/relationships/timeline_kg_backfill.py` | seed path; deterministic `transition: "establish"` |
| `frontend/src/components/relationships/*` | honesty styles (19-03) |
| `backend/tests/unit/relationships/test_graph_honesty.py` | honesty unit suite |

**Smallest next product fix (choose one; ordered by size)**

| Option | Work | Risk | When |
|--------|------|------|------|
| **A (smallest)** | UI-only ops banner when graph edges exist but known seed run / metadata `source=timeline_kg_backfill` (if exposed) | Low | If metadata already on envelope or evidence |
| **B** | Graph edge additive field e.g. `intake_kind: pipeline \| seed_backfill` from observation/judgment metadata | Med | Needs BE + FE + tests |
| **C (preferred ops)** | Prefer empty-accepted + provisional cooccur for progressive UI; **do not** re-run seed backfill for demos | None (policy) | Immediate |

**Recommended smallest next:** **C for demos**, then **B** if seed data remains in prod samples and must be labeled.

**Not P0 code:** inventing change/end without new judgments.

**Implementable now without promote:** Yes (policy C now; B is a small feature).

---

## P1 — Evolution + clue count honesty + optional server range

### P1.1 Relationships: evolution / change / end missing

**Gap**

- Model & gates allow `establish` | `change` | `end` (`ACCEPTABLE_TRANSITIONS` in `gates.py`; package `allowed_transitions`).
- Fold (`_fold_observations`) applies end as chain terminator; graph **drops** `transition == "end"` from visible edges (active-only).
- Sample + backfill: **all establish**; no multi-step chain → no evolution story on graph.

**Root cause (not UI bug)**

1. Seed backfill hardcodes establish and single shot per pair.
2. Production evolution requires multiple accepted observations over chapters (LLM judgment + gates), which sample path has not produced.
3. UI barely surfaces transition chips even when change exists.

**Ordered fixes**

| # | Fix | Paths | Risk | Without promote |
|---|-----|-------|------|-----------------|
| 1 | **Ops:** run full relationship worker with LLM on novel that has multi-chapter character arcs (not only `backfill_relationship_kg_from_timeline.py`) | `services/relationships/worker.py`, `scripts/run_relationship_qualification.py` | High (cost/quality) | Yes |
| 2 | **UI (small):** show transition badge on accepted edges (`establish`/`change`); evidence panel already has valid_from/to chapters | `relationship-graph.tsx`, `relationship-evidence-panel.tsx`, tests | Low | Yes |
| 3 | **Do not** fake change/end in backfill | `timeline_kg_backfill.py` | — | — |
| 4 | Optional later: seed-aware demotion so seed establish never pretends to be pipeline fact (ties to P0.2-B) | query + schema | Med | Yes |

**Smallest next fix for “evolution missing” on product surface:**  
→ **P1.1#2 UI transition badge** if any non-establish data appears after ops;  
→ **P1.1#1 ops LLM run** is the only way to get real change/end data.

**Implementable now without promote:** #2 yes; #1 yes as ops.

---

### P1.2 Clues: lifecycle link counts honesty

**Gap**

- List items: `evidence_count` / `link_count` from **spoiler-visible** evidence/links (`clues/query.py` list path filters links when supporting evidence chapter > cutoff).
- Detail (`clue_detail_panels`): evidence filtered by cutoff; **`links` returned without the same supporting-evidence chapter filter** → card count can disagree with detail panel.
- Lifecycle array is cutoff-filtered; card does **not** show lifecycle step count (only state chip + evidence/link counts).
- `link_count` often 0 until pipeline writes `ClueLink` rows (character/timeline/relationship targets) — after re-run, validate whether links stage is empty by design or failure.

**Files**

| Path | Note |
|------|------|
| `backend/app/services/clues/query.py` | list filter ~L417–429 vs detail ~L652–661 |
| `frontend/src/components/clues/clue-card.tsx` | displays 证据 N / 关联 N |
| `frontend/src/components/clues/clue-band.tsx` | server `payoff_chain` only |
| `backend/app/models/clue.py` | lifecycle + links contracts |

**Smallest next fix**

1. **Code (safe, small):** In `clue_detail_panels`, filter `links` with the same supporting-evidence-hidden rule as list. Add unit test under `backend/tests/unit/clues/`.
2. **Optional UI:** Show `lifecycle.length` on detail header only (never invent client-side); do not invent counts on card.

**Risk:** Low.  
**Implementable now without promote:** Yes (detail link filter is the best “tiny fix” candidate for a follow-up commit).  
**This wave:** not landed (no existing failing test; prefer paired unit test in next slice).

---

### P1.3 Clues: title honesty residual

**Code path OK** (`build_machine_clue_title` + `test_title_honesty.py`).  
**Data residual:** rows persisted before 19-01 keep long titles until re-analysis.

**Fix:** P0.1 re-run. Optional ops one-off title rewrite script is **higher risk** (mutates immutable-ish version facts) — **avoid** unless product explicitly allows version rewrite.

---

### P1.4 Timeline chapter range — minimal server query param design

**Current**

- Structure Workspace comments in `frontend/src/app/analysis/page.tsx`:
  - Client filters `narrative_chapter_number ∈ [chapterStart, chapterEnd]`.
  - Multi-chapter: `densifyTimelineForMultiChapter` (cap 120).
- Server timeline already has **upper** spoiler bound:
  - `build_version_view` → `narrative_chapter_number <= cutoff` (reading progress / full_book).
- **No** inclusive lower/upper structure range params.

**Is server range “easy”?**

**Yes, additive and bounded** — recommended design:

```
GET /api/timeline/{novel_id}
  ?ordering=&person=&causal=&full_book=
  &chapter_start: int | null   # inclusive, >= 1
  &chapter_end: int | null     # inclusive, >= chapter_start
```

Semantics (must keep spoiler authority):

```
effective_end = min(chapter_end or +∞, spoiler_cutoff or +∞)
effective_start = chapter_start or 1
filter: chapter_start_effective <= narrative_chapter_number <= effective_end
```

- If `chapter_end` > spoiler cutoff → clamp silently (or 422 — prefer **clamp** to match existing progressive style).
- If only `chapter_start` set → lower bound only (still respect cutoff).
- Invalid `chapter_start > chapter_end` → **422**.
- `counts.events` / causal edges must recompute on filtered set (same as person filter).
- Candidate running view: still no reading-progress cutoff today; range params still apply for structure facet.

**Touch list**

| Layer | Path |
|-------|------|
| API | `backend/app/api/timeline.py` (`get_timeline`, `get_version`) |
| Query | `backend/app/services/timeline/query.py` (`build_version_view`) |
| FE types | `frontend/src/lib/api.ts` (`TimelineQuery`) |
| FE usage | `frontend/src/app/analysis/page.tsx` — pass structure node range; **keep densify** client-side or move later |
| Tests | timeline unit/integration + `api.contract.test.ts` |

**Risk:** Medium (contract + spoiler interaction + large event payloads still possible if range wide).  
**Implementable now without promote:** Yes, but **not tiny** — full contract + tests; estimate ~0.5–1 day.  
**This wave:** **document only** (per task: implement if easy; prefer plan to avoid untested API surface mid-wave).

**Workaround (current, acceptable for P0 Structure):** client filter + density banner remains correct for presentation; inefficiency only when active timeline is huge and structure selects a thin band.

---

## P2 — Polish / deferred

| ID | Item | Notes | Risk |
|----|------|-------|------|
| P2.1 | Dual-layer accepted+provisional same pair when opted-in | Already styled; optional de-dupe UX | Low |
| P2.2 | Relationship evidence still may carry typed `relation_type` for provisional obs ids | FE ignores via edge_kind; optional BE cleanup | Low |
| P2.3 | Clue span bar schematic only | Not full multi-evidence track | Low |
| P2.4 | Playwright e2e residual (Phase 10/19) | Real browser not re-run in 19/20 orchestration | Med |
| P2.5 | Server-side densify/cap for multi-chapter timeline | Only after P1.4 range params | Med |
| P2.6 | NM sample build for L3/L4 | Ops; not facet quality | Med |
| P2.7 | Superuser cross-owner NM 404 | 20 residual; out of wave | Low |

---

## Recommended execution order (next sessions)

```
1. [P0.1] Re-run clues on sample novel after clamp — verify titles, lifecycle, evidence/link counts
2. [P0.2-C] Demo policy: avoid seed backfill as “truth”; prefer provisional when empty
3. [P1.2] Align clue detail links with list spoiler filter + unit test  ← first small code fix
4. [P1.1#2] Transition badge on relationship edges (if any non-establish appear)
5. [P1.1#1] Optional LLM relationship re-run for change/end data (ops)
6. [P0.2-B] seed intake_kind on graph if seed data must stay
7. [P1.4] Timeline chapter_start/chapter_end server params + FE wiring
8. [P2.*] polish
```

---

## “Implementable now without promote?” matrix

| Item | Now? | Promote needed? | Notes |
|------|------|-----------------|-------|
| Clue re-run ops | Yes | No | Cost/lineage only |
| Policy: no seed as truth | Yes | No | Docs/operator |
| Clue detail link filter | Yes | No | Small BE + test |
| Rel transition badge UI | Yes | No | FE + vitest |
| Rel LLM evolution data | Ops yes | No | Quality variable |
| Seed intake_kind field | Yes | No | Additive API |
| Timeline chapter range API | Yes | No | Contract change; ~0.5–1d |
| Fake evolution in backfill | **No** | — | Honesty regression |
| NM promote | **Forbidden** | — | Authorization |

---

## Phase 19 must-have status (quick audit)

| Must-have | Status |
|-----------|--------|
| V09-TRUTH-01 provisional vs accepted in API | **Done** |
| V09-TRUTH-02 default prefers accepted | **Done** |
| V09-TRUTH-03 clue titles not raw cue[:80] | **Code done; data needs re-run** |
| V09-REL-01 visual honesty | **Done** |
| V09-REL-02 default not flooded | **Done** |
| V09-CLUE-01 plant→payoff cards | **Done** |
| Evolution change/end in sample data | **Missing (data/pipeline)** |

---

## Tiny-safe-fix decision (this wave)

Searched for missing exports / obvious one-line bugs with existing red tests: **none that close the three product gaps without either ops re-run or a deliberate small feature (detail link filter / range params).**

- Detail link spoiler mismatch is real → scheduled as **P1.2**, not silent drive-by without test.
- Timeline server range is **designed** (P1.4), not implemented.
- No commit in this wave.

---

## References

- Phase 19 verification: `.planning/phases/19-analysis-workbench-presentation-and-truth/19-VERIFICATION.md`
- Phase 20 verification residuals: `.planning/phases/20-structure-workspace-multilayer-presentation/20-VERIFICATION.md`
- Status tables: `IMPLEMENTATION-STATUS.md` (Phase 09/11 PARTIAL; evolution & clue production MISSING)
- State deferred: `.planning/STATE.md` — “Server-side timeline chapter range query”

---

## Executed

### P1.2 Clue detail links spoiler alignment (2026-07-17)

- **Change:** Extracted `_link_visible` in `backend/app/services/clues/query.py`; list `link_count` and `clue_detail_panels` `links` both drop links whose supporting evidence chapter is beyond cutoff.
- **Tests:** `tests/unit/clues/test_query_projection.py` — supporting-evidence hide/show, full-book keep-all, list/detail visible-set agreement.
- **Commit message:** `fix(clues): align detail links with list spoiler filtering`
- **NM promote:** No.
