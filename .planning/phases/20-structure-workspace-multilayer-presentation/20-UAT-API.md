# Phase 20 UAT — API Smoke (ordered Step 6)

**Date:** 2026-07-17  
**Target BE:** `http://127.0.0.1:8000`  
**FE:** optional (`:3005`); not required for this smoke  
**Novel:** `91`（关于我转生成史莱姆这件事）  
**Auth:** owner login as `admin` (DB `users.id=2` = `novels.owner_id` for novel 91). Credential taken from existing local helper pattern; **password not recorded here**.  
**Scope:** read-only smoke. No promote. No secret commit.

## Environment

| Check | Result |
|-------|--------|
| Backend `/api/health` | **UP** — HTTP 200, `status=ok`, `version=0.1.0` |
| Auth | **OK** — login returned bearer token |
| Novel 91 access | **OK** — `GET /api/novels/91` → id=91, chapter_count=515 |

## Results (PASS/FAIL per call)

| # | Call | Verdict | HTTP | Notes (no secrets) |
|---|------|---------|------|--------------------|
| 1 | `GET /api/health` | **PASS** | 200 | `status=ok`, `version=0.1.0` |
| 2 | `POST /api/auth/login` (owner of novel 91) | **PASS** | 200 | user=`admin`; token present |
| 3 | `GET /api/novels/91` (ownership smoke) | **PASS** | 200 | title 史莱姆; chapter_count=515 |
| 4 | `GET /api/timeline/91?chapter_start=1&chapter_end=3` | **PASS** | 200 | envelope keys `active`, `running_candidate`; active version_id=**14**, status=`active`; `active.counts.events=984`, participants=3152, causal_edges=0. **Observation:** response still returns full active event set size (~984); chapter_start/end appear not applied as server-side filter on this path (aligns with prior client-range notes). Call itself is healthy. |
| 5 | `GET /api/relationships/91/graph` | **PASS** | 200 | source=`active`, version_id=**14**; nodes=**21**, edges=**37**; through_chapter=268, full_book=false; envelope includes counts / available filters / degradation / generated_at |
| 6 | `GET /api/clues/91` (envelope) | **PASS** | 200 | keys `active`, `running_candidate`; active version_id=**22**; n_clues=**32**; running_candidate=null |
| 7 | `GET /api/narrative-memory/91/versions` | **PASS** | 200 | keys `novel_id`, `versions`, `publication_status`, `message`; **n_versions=1** (partial/non-empty OK for smoke) |

## Overall

| Metric | Value |
|--------|-------|
| Required smokes | health, timeline range, relationships graph, clues envelope, NM versions |
| Auth | PASS (not SKIP) |
| Failures | **0** |
| Verdict | **PASS** — all ordered API smokes returned 200 with expected envelope shapes |

## Residual / honesty notes

1. **Timeline range params:** HTTP success confirmed; server may not fold `chapter_start`/`chapter_end` into event counts (client-side filter still expected on FE). Not a smoke FAIL; product residual if server-side range was intended.
2. **NM:** one version listed — structure tree can use NM path if tree API succeeds; not exercised beyond versions list in this step.
3. **Clues:** active envelope has 32 machine clues; no start/reanalyze/promote in this step.
4. **Relationships:** graph has real nodes/edges on active source; provisional layer not requested.
5. No promote, no writes, no secrets written to planning artifacts.

## How re-run (operator)

```powershell
# BE must be up
# Login as novel-91 owner (admin / local credential — do not paste into reports)
# Then:
# GET http://127.0.0.1:8000/api/health
# GET .../api/timeline/91?chapter_start=1&chapter_end=3  (Bearer)
# GET .../api/relationships/91/graph  (Bearer)
# GET .../api/clues/91  (Bearer)
# GET .../api/narrative-memory/91/versions  (Bearer)
```
