# Ops re-run: clue analysis for novel 91 (later-window clamp)

**Date:** 2026-07-17  
**Scope:** Re-run Phase 11 clue pipeline on sample novel **91** (owner **2**) after later-window clamp fix (`clues/evidence.py` + `clues/candidates.py`).  
**Constraints honored:** no NM promote; no force-push; only novel 91 clue active run reset (other novels untouched).

---

## Verdict

| Item | Result |
|------|--------|
| Overall | **COMPLETED** |
| Vertex/auth failures | **None** (no live Vertex calls this run) |
| Clamp smoke | **OK** — all sampled later packages `chapter_span ≤ 4` |
| Fresh LLM judgments | **0 / 32** — all `cache_hit` |
| Payoff coverage | **0** clues with `payoff_chapter` set |

Pipeline closed successfully for novel 91. Quality of titles/payoff remains weak; re-run largely **replayed cached judge outputs** under a new version.

---

## 1. How the run was started

**Path used:** inline DB + worker script (no API server required).

```powershell
cd D:\ADLINK\Myproject\novel-mind\backend
$env:PYTHONPATH = "D:\ADLINK\Myproject\novel-mind\backend"
.\.venv\Scripts\python.exe scripts\_rerun_clues_smoke.py --novel-id 91 --owner-id 2 --full
```

Script behavior (`backend/scripts/_rerun_clues_smoke.py`):

1. Reset/create **active** `clue_analysis_runs` row for `(owner_id=2, novel_id=91)` → `pending`
2. Smoke: claim run → `_prepare_run` → `_build_candidates` → print later chapter spans → release to `pending`
3. `--full`: `dispatch_clue_run(run_id)` → production runtime + Vertex-configured judge (cache may short-circuit)
4. Print final run status / link samples

**Alternative (not used):** HTTP API in `backend/app/api/clues.py` (`dispatch_clue_run` via BackgroundTasks) if API worker is up.

---

## 2. Progress timeline

| Time (local) | Stage | Notes |
|--------------|-------|--------|
| 08:51:41 | prepare | `run_id=19` created; claimed for smoke |
| 08:51:41 | prepared | `version_id=22`, hierarchy `cb_b4be519d7cf9453a`, timeline v14 |
| ~08:51:48 | smoke | **32 candidates**; sample later spans all ≤ 4 chapters |
| 08:51:48 | SMOKE_OK | claim released |
| 08:51:55 | judging | `completed_candidates` 0→32 in ~2s |
| 08:52:02 | completed | v21 → `superseded`; v22 → `validated`; active pointer → 22 |

Wall time ≈ **21s** (consistent with full cache hits, not live generation).

---

## 3. Result metrics

### Run / version

| Field | Value |
|-------|--------|
| `run_id` | **19** |
| `run.status` | `completed` |
| `status_reason` | empty |
| `progress` | `{stage: completed, total_candidates: 32, completed_candidates: 32}` |
| `version_id` | **22** |
| version status | `validated` |
| active pointer | **version_id=22**, revision **3** |
| prior active | version **21** → `superseded` |

### Clue counts (version 22)

| Metric | Value |
|--------|--------|
| `machine_clues` | **32** |
| `publication_status` | all **provisional** |
| evidence refs | **288** total = **32 cue** + **256 reinforcement** + **0 payoff** |
| `clue_lifecycle_events` | **0** |
| `clue_links` | **0** |
| first_cue_chapter range | min **1**, max **3** (3 distinct chapters only) |

### `payoff_chapter`

| Scope | with `payoff_chapter` set | total |
|-------|--------------------------|-------|
| Lifecycle rows, version 22 | **0** | **0** |
| Lifecycle rows, novel 91 (all versions) | **0** | **0** |
| Machine clues with evidence `role=payoff` | **0** | **32** |

**Interpretation:** Judge/cache outputs produced provisional machine clues with cue+reinforcement only. Lifecycle transitions that would populate `payoff_chapter` (PAID_OFF) did not run / produced no events. Query projection that derives item-level payoff from lifecycle therefore also shows none.

### Model attempts (run 19)

| status | error_code | count |
|--------|------------|------:|
| `cache_hit` | null | **32** |

| Budget ledger | value |
|---------------|-------|
| settled_calls | 0 |
| settled tokens / cost | 0 |

No Vertex auth errors; **no fresh provider traffic**. Retries not required.

### Clamp smoke sample (pre-dispatch)

All later windows stayed within `MAX_LATER_CHAPTERS` (4):

| idx | cue ch | later chapters | later_units | chapter_span |
|----:|--------|----------------|------------:|-------------:|
| 0 | 1 | 2,3 | 8 | 2 |
| 1 | 2 | 3,4,6 | 8 | 4 |
| 2 | 2 | 3,6 | 8 | 4 |
| 3 | 1 | 2,3 | 8 | 2 |
| 4 | 2 | 3,4 | 8 | 2 |
| 5 | 2 | 3 | 8 | 1 |
| 6 | 2 | 3,4,5,6 | 8 | 4 |
| 7 | 2 | 3 | 8 | 1 |

→ Confirms clamp prevents hard-fail `later windows span more than 4 chapters`.

---

## 4. Sample titles / summaries (not raw cue dumps)

Stored `title` fields are truncated meta strings (~32 chars, often `The cue evidence (ev-hn_…`). Readable content is in `summary`:

| id | cue_ch | conf | Summary gist (truncated) |
|----|-------:|-----:|-------------------------|
| 33 | 1 | 0.95 | Protagonist Satoru Mikami, 37, normal life in Japan; meeting / setup |
| 34 | 2 | 0.95 | Blind in dark environment; unique skill context |
| 35 | 2 | 0.95 | Discovers unique skill Great Sage (大贤者); Q&A reliance |
| 36 | 1 | 0.95 | Stabbed protecting junior colleague Tamura |
| 37 | 2 | 0.95 | Obtains Water Propulsion (水压推进) by ejecting water |
| 38 | 2 | 0.90 | Falls into river / underground water body |
| 39 | 2 | 0.90 | Links Predator (捕食者) + Great Sage to analyze Hipokute |
| 40 | 2 | 0.95 | Slime biology: every cell is muscle and brain |

**vs v21:** v21 titles were mostly raw Chinese cue text dumps (32/32 dumpish); v22 titles are short English meta labels (0 dumpish by newline/length>60 heuristic) but still not good human-facing titles.

---

## 5. Failures / residual issues

| Issue | Severity | Notes |
|-------|----------|-------|
| Vertex/auth | none | N/A this run |
| Worker crash | none | completed cleanly |
| Payoff empty | **product gap** | 0 lifecycle events; 0 payoff evidence; early chapters only (1–3) |
| Title honesty | **product gap** | titles are evidence-id meta, not concise clue names |
| Cache-only re-run | **ops note** | new version promotes cached judgments; does not re-validate live model quality |
| Candidate locality | **coverage** | 32 candidates all cue_ch ∈ {1,2,3} on a 515-chapter novel |

No intentional wipe of other novels’ data. Novel 91 kept historical versions (e.g. v21 rows still in `machine_clues` as superseded version data).

---

## 6. Commands for parent verification

```powershell
cd D:\ADLINK\Myproject\novel-mind\backend
$env:PYTHONPATH = "D:\ADLINK\Myproject\novel-mind\backend"
.\.venv\Scripts\python.exe scripts\_clue_sample.py
# expect active_version 22, machine_clues include v22 rows
```

SQL sketch:

```sql
SELECT id, status, version_id, progress
FROM clue_analysis_runs WHERE id = 19;

SELECT version_id, revision FROM clue_active_pointers WHERE novel_id = 91;

SELECT count(*) FROM machine_clues WHERE novel_id = 91 AND version_id = 22;

SELECT count(*) FILTER (WHERE payoff_chapter IS NOT NULL), count(*)
FROM clue_lifecycle_events WHERE novel_id = 91 AND version_id = 22;
```

---

## 7. Bottom line for parent agent

- **version_id:** 22 (active)  
- **clue count:** 32 machine clues  
- **payoff_chapter set:** **0 / 32** (0 lifecycle rows)  
- **sample titles:** see §4 (summary gists; DB titles are truncated meta)  
- **failures:** none operational; quality residuals = empty payoffs, weak titles, cache-only judging  
- **clamp fix:** smoke-proven (later span ≤ 4); full dispatch succeeded after prior hard-fail class of error  
