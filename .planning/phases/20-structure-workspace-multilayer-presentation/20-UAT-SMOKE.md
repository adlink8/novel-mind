# Phase 20 UAT / Smoke — Structure Workspace

**Date:** 2026-07-17  
**Wave:** 1.1  
**Auditor mode:** read-mostly + local smoke (no NM promote, no git push/commit)  
**Verdict:** **Product surface ready for browser UAT once BE/FE are up**; code + unit + DB evidence support P0 checklist. Browser E2E **not** run this session.

## Environment

| Service | Expected | Observed 2026-07-17 |
|---------|----------|---------------------|
| PostgreSQL | `127.0.0.1:5432` (`NOVELMIND_DATABASE_URL` in `backend/.env`) | **UP** — TCP connect OK; ORM queries succeed |
| Backend API | `http://localhost:8000` (uvicorn `app.main:app`) | **DOWN** — port 8000 not listening; `/health` timeout |
| Frontend | `http://localhost:3000` (`npm run dev`) | **NOT app** — `:3000` held by `vmnat` (VMware NAT), not Next.js; HTTP root times out |

### How to start (for browser UAT)

```powershell
# DB (if needed)
docker compose up -d db chroma

# Backend
cd D:\ADLINK\Myproject\novel-mind\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
# → http://localhost:8000  /docs

# Frontend (if :3000 is occupied by VMware, use another port and ensure CORS includes it)
cd D:\ADLINK\Myproject\novel-mind\frontend
npm run dev
# or: npx next dev -p 3001
```

CORS already allows `3000–3005` per `backend/.env` / `.env.example`.

### What cannot be browser-verified this session

- Live `/analysis` selection, layout, dialogs, graph/chart rendering
- Authenticated API against slime while owner session is active
- Visual multi-lane swimlane and full-height shell at 1280 / 390

---

## Sample novel evidence (slime, `novel_id=91`)

Source: `backend/scripts/_audit_novel_gaps.py 91` + ad-hoc read-only SQL (no writes).

| Field | Value |
|-------|--------|
| Title | 关于我转生成史莱姆这件事 |
| owner_id | 2 |
| chapters_db | 515 |
| text_chunks | 8851 |
| timeline_active_version | 14 |
| timeline_events | **1933** (chapters 1–513, 486 distinct) |
| timeline_causal_edges | 0 |
| characters | 40 |
| kg_accepted | 41 |
| rel_obs_accepted | 41 (all `establish`; 0 with `valid_to_chapter`) |
| clue_run | id=18 **completed**; active pointer **version_id=21** |
| machine_clues (v21) | **32** (all have `first_cue_chapter`; titles often look like raw excerpt noise) |
| clue_evidence_refs (v21) | **288** — roles: `cue` 32, `reinforcement` 256, **`payoff` 0** |
| clue_lifecycle_events | 0 |
| narrative_memory_versions / nodes / claims | **0** |
| reading_progress | `timeline_full_book: true` (chapter_id 326) |

**Implications for UAT on slime:**

1. **Structure tree will use chapter fallback** (no NM candidate) — exercises honest empty-NM path, not L3/L4 NM tree.
2. Timeline / relationships have rich data for facet scope smoke.
3. Clues: plant chapters exist; **plant→payoff span cannot be proven on this sample** (no payoff evidence).
4. Selecting the novel with saved `timeline_full_book: true` will prefer full-book load without re-confirm (preference already on).

---

## UX path map (code)

Primary surface: `frontend/src/app/analysis/page.tsx` → `StructureWorkspaceShell`.

```
/analysis
  ├─ header: 选书 (selectNovel — load only, no start-or-resume)
  ├─ empty: “选择一本小说”
  └─ StructureWorkspaceShell
       ├─ left rail: StructureTree (chapters | NM forest)
       │    └─ StructureNodePanel (claims/source-links when NM)
       └─ main facets (tabs)
            ├─ timeline: TimelineStatus + Controls + TimelineChart (swimlanes)
            ├─ relationships: RelationshipWorkspace (accepted default; provisional toggle)
            └─ clues: ClueWorkspace (plant/payoff cards; range filter)
  └─ modal: 确认显示全书 (fullBook enable)
```

Key behaviors:

| Behavior | Implementation |
|----------|----------------|
| No auto-start analysis | `selectNovel` loads timeline/status/structure only; `startAnalysis` only on explicit CTA (`timelineApi.startOrResume` + `clueApi.startOrResume`) |
| Chapter fallback | `loadStructure` → no/empty NM → `applyChapterForest` / `buildChapterFallbackTree` |
| Facet scope | Timeline: client `eventInChapterRange`; multi-chapter densify cap 120; Rel: `relationshipThroughChapter = min(user, node.chapterEnd)`; Clues: `clueIntersectsChapterRange` plant/payoff |
| Multi-lane timeline | `EVENT_TYPE_LANES` = plot / conflict / character / world in `timeline-chart.tsx` |
| Full-page layout | `data-testid="analysis-fullpage"` + shell track `h-full min-h-0` |
| Full-book confirm | Checkbox → `confirmFullBook` dialog → `enableFullBook` → preference API |
| NM promote | **Absent** product API (GET only); FE client documents never-promote; badge `candidate_preview` / 预览·未发布 |

---

## Automated smoke (this session)

```
frontend: npx vitest run src/components/structure src/app/analysis
→ 3 files, 47 tests passed (~3.4s)

Includes:
- page.test: does not auto-start on select; full-book confirm dialog
- structure-workspace: chapter fallback tree; multi-chapter densify; shell scope label
- relationships tests (in analysis suite path): provisional toggle honesty
```

DB audit: `python scripts/_audit_novel_gaps.py 91` (read-only).

---

## UAT checklist

Legend: **PASS** = code + tests and/or DB support product claim; **SKIP** = needs running UI/API or missing sample data for that assertion; **FAIL** = contradicted by evidence.

| # | Check | Result | Method | Evidence / notes |
|---|--------|--------|--------|------------------|
| 1 | Select novel without auto-start analysis | **PASS** | Code + Vitest | `selectNovel` does not call `startOrResume`; test *does not auto-start…*; start only via `startAnalysis` / empty-state CTA |
| 2 | Structure tree chapters fallback | **PASS** | Code + Vitest + DB | No NM versions for 91 → `applyChapterForest`; `buildChapterFallbackTree`; banner `nm-empty-banner` when `structureSource=chapters` |
| 3 | Scope filters facets by chapter range | **PASS** | Code + Vitest | Timeline client filter + densify; rel `through_chapter` fold; clue range props. **Residual:** timeline range is client-only (no server chapter_start..end) |
| 4 | Timeline multi-lane / full page layout | **PASS** (code) / **SKIP** (browser) | Code + unit | 4 type swimlanes; `analysis-fullpage` + shell full-height track. Browser layout not verified |
| 5 | Relationships accepted vs provisional | **PASS** | Code + Vitest + DB | Default `include_provisional` off; toggle + honesty banner tests; slime has 41 accepted observations for accepted-path smoke |
| 6 | Clues plant→payoff if data | **PASS** (UI) / **SKIP** (payoff on sample) | Code + Vitest + DB | UI + unit tests for plant/payoff labels and spoiler-safe unknown payoff; slime v21 has 32 plants, **0 payoff** evidence → full plant→payoff chain not sample-verifiable |
| 7 | Full-book confirmation | **PASS** | Code + Vitest | Dialog `确认显示全书` + `setFullBookPreference`; note: slime already has `timeline_full_book: true` so first paint may skip re-confirm until toggled off→on |
| 8 | No NM promote UI | **PASS** | Code grep | `api/narrative_memory.py` GET-only; no FE promote control; only `candidate_preview` badge copy |

---

## Blockers / residuals

### Hard blockers for live browser UAT

1. **Backend not running** on `:8000` — cannot hit `/api/timeline`, `/api/relationships`, `/api/clues`, `/api/narrative-memory`.
2. **Frontend app not running** — `:3000` is VMware `vmnat`, not Next; need `npm run dev` (possibly alternate port).
3. **Auth session** required for owned novels (slime `owner_id=2`) — login as owner before selecting novel 91.

### Data residuals (not product FAIL)

| Residual | Impact |
|----------|--------|
| NM rows = 0 for slime | Cannot UAT NM tree / claims / source-links on primary sample; chapter fallback path is the one exercised |
| Clue `payoff` role count = 0 | Plant cards OK; payoff span / payoff chain need different novel or re-run after quality fix |
| Timeline causal edges = 0 | Causal overlay toggle has nothing to draw |
| machine_clue titles look like raw text | Quality issue (content), not Structure Workspace chrome |
| 515-node chapter tree | Possible UI performance friction when expanded; not verified in browser |
| Client-only timeline chapter range | Multi-chapter scopes still download full timeline envelope then filter |

### Explicit non-goals this audit

- Did **not** promote NM / create active NM pointer  
- Did **not** start workers or mutate DB  
- Did **not** git commit / push  

---

## Recommended browser script (when BE+FE up)

1. Login as owner of novel 91.  
2. Open `/analysis` → select 史莱姆 → confirm **no** automatic “开始分析” network start; existing timeline events appear.  
3. Confirm left tree = 全书结构 + chapter nodes; scope label updates on chapter vs book select.  
4. Timeline tab: single-chapter selection shows swimlanes; book/multi-chapter shows densified set.  
5. Relationships: default accepted edges; toggle 显示临时共现 if available.  
6. Clues: list shows plant chapters; expect 兑现未公开 / no payoff chapter for current sample.  
7. Toggle 显示全书 off then on → confirmation dialog.  
8. Grep DevTools / UI: no “发布 / promote / 设为正式” for NM.

---

## Sign-off

| Layer | Status |
|-------|--------|
| Structure Workspace product claims (code) | **Ready** |
| Automated unit regression (structure + analysis) | **47 passed** |
| Sample DB readiness (slime facets) | **Timeline + relationships strong; clues plant-only; NM empty** |
| Browser UAT | **Blocked** until BE/FE started |

**Next:** start BE+FE, run browser script above; optionally build NM candidate for 91 offline (CLI only, no promote) if L3/L4 tree UAT is required.
