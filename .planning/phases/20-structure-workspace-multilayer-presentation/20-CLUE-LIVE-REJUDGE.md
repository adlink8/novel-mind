# Ops: non-cache clue re-judge for novel 91

**Date:** 2026-07-17  
**Scope:** Force live Vertex clue semantic judge calls on novel **91** (owner **2**) to improve payoff/title quality after prior run was 100% `cache_hit` (v22).  
**Constraints honored:** no NM promote; no wipe of other novels; only novel 91 clue attempt cache + new clue runs/versions.

---

## Verdict

| Item | Result |
|------|--------|
| Overall | **COMPLETED** (live path exercised) |
| Live provider calls | **Yes** — subset 3 + full 29 fresh `succeeded` (3 `cache_hit` reuse from subset) |
| Vertex/auth failures | **None** |
| Active `version_id` | **24** (validated; hierarchy `cb_9f9aee6bf1cb427b`) |
| Machine clues | **32** |
| `payoff_chapter` set | **0 / 32** (0 lifecycle events) |
| Title quality | **Not improved** — still evidence-meta rationales |
| NM | **not promoted** |

**Bottom line:** Live re-judge succeeded operationally but **did not** fix product payoff/title gaps. Root causes are worker/gate path + rationale-as-title design, not cache alone.

---

## 1. Cache path (how force re-call works)

### Code path

`backend/app/services/clues/worker.py` → `_judge_and_persist`:

1. Build exact key via `_exact_cache_key(...)` (stage, snapshot, hierarchy, timeline, candidate_id, package_hash, prompt/schema/decoding/config/policy hashes, model lineage).
2. `runtime.call_repo.load_exact_cache(cache_key)` (`budget.py`):
   - Requires `status == "succeeded"`
   - `response_hash IS NOT NULL`
   - `usage["validated_output"]` is a dict
3. On hit → parse judgment, `record_cache_hit`, **no** `judge_package` network call.
4. On miss → reserve budget → `runtime.judge.judge_package(...)` (Vertex).

### Force levers (no `force=true` flag exists)

| Lever | Invasive? | Effect |
|-------|-----------|--------|
| **Invalidate attempt cache** (strip `validated_output`, null `response_hash` on novel’s `succeeded` rows) | Low (novel-scoped) | Same key cannot hit |
| **Hierarchy/timeline/package change** | Ops rebuild | Cache key changes → natural miss |
| Bump `PROMPT_VERSION` / schema / decoding hashes | Code change | Global key break for all novels |
| Add `force_live` flag to worker | Code change | Not done this wave |

**Ops script:** `backend/scripts/_clue_live_rejudge.py`  
- `--limit N` invalidate first N novel succeeded caches (0 = all for that novel)  
- `--max-candidates N` subset live probe (0 = full production 32)  
- `--skip-invalidate` rely on hierarchy key miss only  
- `--full` dispatch worker  

There is **no** production API `force=true` for clue re-judge.

---

## 2. Runs executed

### Probe A — subset (3 candidates)

```text
python scripts/_clue_live_rejudge.py --novel-id 91 --owner-id 2 --limit 3 --max-candidates 3 --full
```

| Field | Value |
|-------|--------|
| Invalidated attempts | ids **34, 35, 36** (novel 91 only; `ops_cache_invalidated`) |
| `run_id` | **20** |
| `version_id` | **23** (briefly active; 3 clues only) |
| Attempts | **3 succeeded** (all live) |
| Wall time | ~48s |
| Vertex errors | none |

### Probe B — full book (32 candidates)

```text
python scripts/_clue_live_rejudge.py --novel-id 91 --owner-id 2 --skip-invalidate --max-candidates 0 --full
```

Hierarchy already `cb_9f9aee6bf1cb427b` (vs old v21/v22 `cb_b4be519d7cf9453a`) → most keys naturally miss. Three candidates reused v23 exact-cache from probe A.

| Field | Value |
|-------|--------|
| `run_id` | **21** |
| `version_id` | **24** (active pointer revision **5**) |
| Hierarchy | `cb_9f9aee6bf1cb427b` |
| Timeline | v14 |
| Status | `completed` |
| Progress | `{total_candidates: 32, completed_candidates: 32}` |
| Attempts | **29 succeeded** + **3 cache_hit** |
| Wall time | ~2m 15s (09:43:48 → 09:46:02) |
| Vertex errors | none; **0** failed / outcome_unknown |

### Cost (documented)

| Metric | Value |
|--------|------:|
| Live calls (full run) | 29 |
| Input tokens (live only) | **469,150** |
| Output tokens (live only) | **10,968** |
| Avg latency | **~4.2 s / call** |
| DB `cost_usd` sum | **0.0** (audit did not populate cost; price snapshot lists flash \$0.10 / \$0.40 per M) |
| **Estimated** provider cost | ≈ 469k×\$0.10/M + 11k×\$0.40/M ≈ **\$0.05** (order-of-magnitude) |
| Subset probe extra | ~3 × ~16–17k input (already paid in probe A) |

Full-book live is cheap on flash but **serial** (~2+ min). Subset-first is preferred for auth smoke.

---

## 3. Result metrics (version 24)

### Counts

| Metric | Value |
|--------|------:|
| `machine_clues` | **32** |
| Evidence roles | cue **32** + reinforcement **256** + payoff **0** |
| `clue_lifecycle_events` | **0** |
| Rows with `payoff_chapter` set | **0** |
| `publication_status` | all **provisional** |
| Title meta heuristic (`cue evidence` / `ev-hn_`) | **32 / 32** |

### Live classifications (29 succeeded attempts on run 21)

| classification | count |
|----------------|------:|
| payoff | 19 |
| unrelated | 7 |
| reinforcement | 2 |
| cue_only | 1 |

(Plus 3 cache hits from subset judgments — same meta-rationale pattern.)

Model **often labels payoff**, but worker never materializes lifecycle/payoff evidence (see §5).

### Sample titles / summaries (v24, not invented)

| id | cue_ch | conf | title (stored) | summary gist |
|----|-------:|-----:|----------------|--------------|
| 68 | 2 | 0.95 | `In the cue evidence (ev-hn_e923…` | hears telepathic voice “小不点” |
| 69 | 3 | 0.95 | `In the cue evidence (ev-hn_e964…` | blind slime; cannot see |
| 70 | 1 | 0.95 | `The cue evidence (ev-hn_e44e6e9…` | Satoru Mikami stabbed |
| 71 | 2 | 0.95 | `The cue evidence (ev-hn_e7e4e41…` | unique skill Predator 捕食者 |
| 72 | 3 | 0.90 | `The cue evidence describes the…` | magicules / 魔素 perception |
| 73 | 2 | 0.95 | `In the cue evidence (ev-hn_e3c8…` | blind slime laments vision |
| 74 | 2 | 0.95 | `In the cue evidence (ev-hn_e41f…` | otherworld people wonder |
| 75 | 2 | 0.95 | `In the cue evidence (ev-hn_e422…` | falls into water, cannot swim |

Readable content is in **summary**; **title** is a 32-char stem of the same meta rationale (`build_machine_clue_title` ← first line of `judgment.rationale`).

---

## 4. Why live re-judge did not improve payoff/title

### Payoff gap (product / gate path — not cache)

Worker always gates first transition as **CANDIDATE → ACTIVE**, but `CLASSIFICATION_FOR_TARGET[ACTIVE]` only allows **`cue_only`**.

When LLM returns `classification=payoff` (majority of live judgments):

1. First gate rejects (schema/classification mismatch for ACTIVE).
2. `_persist_decision` sets `publication_status=provisional` and **returns early**.
3. Later REINFORCED / PAID_OFF append path never runs → **0 lifecycle**, **0 payoff_chapter**, **0 role=payoff** evidence rows.

So replaying live judgments **cannot** populate payoffs without a worker/gate path fix (e.g. allow progressive path for `payoff` / `reinforcement` classifications). **Do not invent payoffs in data.**

### Title gap (prompt + title builder)

- Judge schema has **no** dedicated short-title field.
- Prompt asks for free-form `rationale`; model opens with “The cue evidence (ev-hn_…)…”.
- `build_machine_clue_title` truncates that first line → meta titles.

Live calls confirmed the model still emits that style under `clue_semantic_judge.v1`. Improving titles needs prompt/schema/title-builder change, not only cache bust.

---

## 5. What was / was not touched

| Action | Done? |
|--------|-------|
| Novel 91 attempt cache invalidate (3 rows) | yes |
| Novel 91 new runs 20–21, versions 23–24 | yes |
| Active clue pointer → v24 | yes (clue active only) |
| Other novels’ clue data wipe | **no** |
| NM promote / active NM pointer | **no** |
| Prompt/schema/gate code changes | **no** (ops re-judge only) |

---

## 6. Commands for parent verification

```powershell
cd D:\ADLINK\Myproject\novel-mind\backend
$env:PYTHONPATH = "D:\ADLINK\Myproject\novel-mind\backend"
.\.venv\Scripts\python.exe scripts\_clue_live_rejudge.py --novel-id 91 --inspect-only
.\.venv\Scripts\python.exe scripts\_clue_sample.py
```

Expect:

- `active_clue_version` **24**
- run **21** attempts: mostly `succeeded`, few `cache_hit`
- machine_clues for v24 = 32
- lifecycle / payoff_chapter still 0

---

## 7. Parent agent return summary

| Field | Value |
|-------|--------|
| Live calls happened? | **Yes** (29+3 on full path; 3 on subset probe) |
| `version_id` | **24** |
| Clue count | **32** |
| Payoff count (`payoff_chapter` / lifecycle) | **0** |
| Sample titles | Meta stems e.g. `In the cue evidence (ev-hn_e923…`, `The cue evidence (ev-hn_e7e4e41…` — see §3 |
| Blockers remaining | Gate path rejects `payoff` classification before lifecycle; titles derive from meta rationales |
| Retries | Vertex never failed; no second retry needed |

### Recommended next (not done here)

1. Fix `_persist_decision` progressive path so `reinforcement` / `payoff` classifications can enter ACTIVE→… chain without requiring `cue_only`.
2. Prompt: require short human title in rationale first line **or** add `short_title` to judgment schema + title builder.
3. Re-run live only after (1)(2); pure re-judge alone is insufficient.
