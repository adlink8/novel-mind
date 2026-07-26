> Layer numbering superseded by docs/adr/0001-layer-registry.md

# Wave 1.2 / 2.1 — Narrative Memory candidate build run log

**Date:** 2026-07-17  
**Scope:** Candidate-only L2–L4 structure tree data for at least one novel if eligibility allows.  
**Hard constraints honored:** no promotion, no active pointer, no Phase 08/09/11 worker rewrites.

## Verdict

**BLOCKED — no NM candidate version created, no build run started.**

| Item | Result |
|------|--------|
| `narrative_memory_versions` | **0 rows** |
| `narrative_memory_build_runs` | **0 rows** |
| `narrative_memory_nodes` (chapter_state / arc / global) | **0** |
| `version_id` | **none** |
| Promotion / active pointer | **not attempted** |

Primary gate failure: Phase 12 `provider_calls_allowed=false` on every novel with hierarchy (both require hierarchy `rebuild_required` for `content_hash_mismatch`).

---

## 1. CLI / authority surface reviewed

| Path | Role |
|------|------|
| `backend/scripts/run_asset_audit.py` | Read-only eligibility; exit `0` if `provider_calls_allowed`, else `2` |
| `backend/scripts/run_narrative_memory_build.py` | Candidate build control: `start \| status \| cancel \| resume`; **forbids** `--promote` / `--rollback` / `--current` / `--default` / `--all-books` |
| Phase 12–17 docs | Hierarchy must be `reusable_exact` before provider calls; `CandidateAuthority.create_version` rejects non-exact hierarchy |

### Production CLI transport note (secondary blocker)

`run_narrative_memory_build.py` injects `_NoopTransport` and documents that production dry-runs inject a controlled transport in tests only:

```text
# Phase 14 VERIFICATION residual:
# Production CLI transport is noop by default; operator dry-runs in CI inject controlled transport via worker tests.
```

Even after eligibility is fixed, **resume/start process path cannot call Vertex** without a scoped CLI transport wiring (e.g. mirror `timeline.worker._VertexTransport`). This wave did **not** change that code (eligibility is already decisive).

---

## 2. Novel inventory (local PostgreSQL)

Database: `postgresql+asyncpg://…@127.0.0.1:5432/novelmind` (`NOVELMIND_DATABASE_URL` from `backend/.env`).

| novel_id | owner_id | title | chapters | words | hierarchy_build_id | build | immutable | is_candidate | hierarchy nodes |
|----------|----------|-------|----------|-------|--------------------|-------|-----------|--------------|-----------------|
| **91** | 2 | 关于我转生成史莱姆这件事 | 515 | 4,938,084 | `cb_b4be519d7cf9453a` | committed | true | false | 18,303 |
| **104** | 2 | 龙族（1-4合集） | 420 | 2,206,713 | `cb_29ed4f483982455d` | committed | true | false | 9,413 |

Only these two novels exist with active hierarchy pointers. Prefer **91** (timeline + relationship + clue present).

Discovery query (join on `build_id` varchar, not integer PK):

```sql
SELECT n.id, n.owner_id, n.title, n.chapter_count,
       cap.build_id AS hierarchy_build_id,
       cb.status, cb.immutable, cb.is_candidate,
       (SELECT COUNT(*) FROM chunk_hierarchy_nodes chn WHERE chn.build_id = cap.build_id) AS node_count
FROM novels n
LEFT JOIN chunk_active_pointers cap ON cap.novel_id = n.id
LEFT JOIN chunk_builds cb ON cb.build_id = cap.build_id
ORDER BY n.id;
```

---

## 3. Asset audit / eligibility

### Commands

```powershell
cd D:\ADLINK\Myproject\novel-mind\backend
$env:PYTHONPATH = "D:\ADLINK\Myproject\novel-mind\backend"

.\.venv\Scripts\python.exe scripts\run_asset_audit.py --owner-id 2 --novel-id 91
# EXIT=2

.\.venv\Scripts\python.exe scripts\run_asset_audit.py --owner-id 2 --novel-id 104
# EXIT=2
```

### Novel 91 (slime) — preferred candidate

| Asset | Status | item_count | version_id | reason_codes |
|-------|--------|------------|------------|--------------|
| hierarchy (**required**) | **rebuild_required** | 18303 | `cb_b4be519d7cf9453a` | `content_hash_mismatch` |
| timeline (optional) | reusable_exact | 1933 | `14` | — |
| relationship (optional) | reusable_exact | 41 | `14` | — |
| clue (optional) | reusable_exact | 32 | `21` | — |

- `provider_calls_allowed`: **false**
- `policy_version`: `asset-eligibility-policy.v1`
- Hierarchy rebuild ranges: **17** ranges covering essentially ch. **1–514** (sample first `{1,20}` … last `{493,514}`)

### Novel 104

| Asset | Status | item_count | version_id | reason_codes |
|-------|--------|------------|------------|--------------|
| hierarchy (**required**) | **rebuild_required** | 9413 | `cb_29ed4f483982455d` | `content_hash_mismatch` |
| timeline | optional_unavailable | 0 | null | `source_unavailable` |
| relationship | optional_unavailable | 0 | null | `source_unavailable` |
| clue | optional_unavailable | 0 | null | `source_unavailable` |

- `provider_calls_allowed`: **false**
- Rebuild range: single range `{start_chapter: 0, end_chapter: 419}` (full book)

---

## 4. Why NM build cannot start

Gate chain (Phase 12 → 13 → 14):

1. **Eligibility:** `provider_calls_allowed` is derived only from required hierarchy being `reusable_exact`. Both novels fail with `content_hash_mismatch` (stored hierarchy node content hashes no longer match recomputed hashes from source chapter spans).
2. **Version create:** `CandidateAuthority.create_version` raises `EligibilityRejectedError("hierarchy must be reusable_exact")` — no candidate `version_id` can be minted.
3. **Builder:** `NarrativeMemoryBuilderWorker.start_run` / `process_run` re-audit and pause with `provider_calls_not_allowed` if somehow a stale version existed; `run_narrative_memory_build.py` also requires an explicit existing `--version-id`.
4. **Transport (latent):** CLI `_NoopTransport` would fail any real model stage even after eligibility is fixed.

Therefore:

```text
NM build CLI not executed for start/resume (no eligible version_id).
status would only return {"status":"missing"} for any invented version_id.
```

### Counts after this wave

| Metric | Count |
|--------|------:|
| chapter_state nodes | 0 |
| arc / volume nodes | 0 |
| global_story nodes | 0 |
| NM version_id | n/a |

---

## 5. Exact blockers (checklist for next ops wave)

| # | Blocker | Severity | Unblock action (out of this wave’s scope) |
|---|---------|----------|-------------------------------------------|
| B1 | Hierarchy `content_hash_mismatch` → `rebuild_required` on novel **91** (and 104) | **P0** | Phase 07 hierarchy rebuild/re-chunk for rebuild ranges; re-promote **immutable non-candidate** active build; re-run `run_asset_audit` until hierarchy `reusable_exact` |
| B2 | No `narrative_memory_versions` row | follows B1 | After B1: create candidate version via authority with frozen eligibility checksum + model lineage hashes |
| B3 | Production CLI transport is **noop** | P1 after B1 | Wire Vertex (or LiteLLM) `ModelTransport` into `run_narrative_memory_build.py` without promote flags; keep candidate-only |
| B4 | Full-book cost/budget for 515 chapters | ops | Start with chapter subset if worker supports `chapter_ids`, tighten `BudgetPolicy`, monitor cost |
| B5 | Vertex auth / proxy | env | `.env` has `NOVELMIND_CHAT_PROVIDER=vertex_google`, GCP project/location, `NOVELMIND_HTTPS_PROXY=http://127.0.0.1:7897` — not exercised this wave because eligibility blocked earlier |
| B6 | Novel 104 optional assets missing | secondary | Prefer novel 91 once hierarchy is exact; timeline/rel/clue already reusable_exact on 91 |

**Not blockers for hierarchy-only NM path:** missing timeline is optional (Phase 12). Novel 91 already has optional domains exact.

**Forbidden / not done:** NM promote, active pointer, Reader Chat cutover, 08/09/11 worker rewrites.

---

## 6. Suggested follow-up command sequence (after B1)

```powershell
cd D:\ADLINK\Myproject\novel-mind\backend
$env:PYTHONPATH = "D:\ADLINK\Myproject\novel-mind\backend"

# 1) Prove gate open
.\.venv\Scripts\python.exe scripts\run_asset_audit.py --owner-id 2 --novel-id 91
# expect EXIT=0 and hierarchy.status == reusable_exact

# 2) Create candidate version (operator/script using CandidateAuthority — not promote)
#    → capture integer version_id

# 3) After CLI transport is non-noop:
.\.venv\Scripts\python.exe scripts\run_narrative_memory_build.py start `
  --owner-id 2 --novel-id 91 --version-id <VERSION_ID> --json

.\.venv\Scripts\python.exe scripts\run_narrative_memory_build.py resume `
  --owner-id 2 --novel-id 91 --version-id <VERSION_ID> --json

.\.venv\Scripts\python.exe scripts\run_narrative_memory_build.py status `
  --owner-id 2 --novel-id 91 --version-id <VERSION_ID> --json

# 4) Count L2–L4 nodes (candidate only)
# SELECT node_kind, COUNT(*) FROM narrative_memory_nodes
# WHERE owner_id=2 AND novel_id=91 AND version_id=<VERSION_ID>
# GROUP BY node_kind;
```

---

## 7. Authorization / safety

- Candidate-only dry-run intent preserved.
- No writes to NM tables this run (audit is SELECT-only).
- No promotion path invoked.
- Temporary discovery helpers under `backend/scripts/_nm_*.py` were used for inspection only and may be removed; they are not part of product CLI.

---

## 8. Summary for parent agent

**Outcome:** BLOCKED on Phase 12 eligibility. Best novel is **id=91** (slime) with rich optional assets, but required hierarchy is `rebuild_required` / `content_hash_mismatch` almost book-wide → `provider_calls_allowed=false`. **No `version_id`, no L2–L4 node counts.** Secondary: build CLI still ships with noop transport. Next wave: rebuild hierarchy for 91 → re-audit → create candidate version → wire Vertex transport → `start`/`resume` without promote.
