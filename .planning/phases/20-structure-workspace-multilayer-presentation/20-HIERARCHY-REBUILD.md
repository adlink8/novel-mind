# Phase 07 hierarchy rebuild — novel 91

**Date:** 2026-07-17  
**Scope:** Force rebuild hierarchy for `novel_id=91`, `owner_id=2`; re-audit until `reusable_exact`.  
**Promotion:** not attempted (no NM promote).

## Verdict

**SUCCESS** — hierarchy is `reusable_exact`; asset audit `EXIT=0` (`provider_calls_allowed=true`).

| Field | Value |
|-------|--------|
| novel_id | 91 |
| owner_id | 2 |
| title | 关于我转生成史莱姆这件事 |
| chapters | 515 |
| **new build_id** | **`cb_9f9aee6bf1cb427b`** |
| previous active | `cb_b4be519d7cf9453a` → intermediate failed `cb_a36bc6678e09497f` |
| hierarchy status | **reusable_exact** |
| hierarchy item_count | 18,378 |
| audit EXIT | **0** |
| provider_calls_allowed | **true** |
| rebuild duration (final) | **26.8 s** |
| first rebuild (pre-fix) | 28.9 s → still `content_hash_mismatch` |

## Method

Used existing API only:

```python
from app.services.analysis_service import ensure_hierarchy
# async_session_factory → load Novel(91, owner=2) → ensure_hierarchy(db, novel, force=True)
```

Then:

```powershell
cd D:\ADLINK\Myproject\novel-mind\backend
$env:PYTHONPATH = "D:\ADLINK\Myproject\novel-mind\backend"
.\.venv\Scripts\python.exe scripts\run_asset_audit.py --owner-id 2 --novel-id 91
```

## First force rebuild (failed eligibility)

- `ensure_hierarchy(..., force=True)` completed and promoted `cb_a36bc6678e09497f` (~28.9s).
- Audit still reported hierarchy `rebuild_required` / `content_hash_mismatch` with 17 rebuild ranges (essentially ch. 1–514, excluding a few ultra-short chapters).

### Root cause (blocking bug)

Evidence nodes failed NM audit check:

```text
chapter.content[source_start:source_end] != node.content
```

In `segment_from_proposals` multi-span merge, segment content was rebuilt as:

```python
text = "\n".join(s.content for s in current)
```

while `source_start`/`source_end` spanned the original chapter range. Light-novel formatting (blank lines + indentation between atomic spans, and mid-dialogue splits) meant the join dropped interstitial whitespace and invented single newlines — hash of stored content was self-consistent, but **not** equal to the chapter slice.

Diagnostics on post-rebuild build `cb_a36bc6678e09497f`:

| Check | Count |
|-------|------:|
| node content_hash vs recomputed | 0 mismatches |
| evidence slice vs node.content | **9049** mismatches |
| affected chapters | 498 |

### Fix

File: `backend/app/services/chunking/segmentation.py`

- Pass `source_content` from `segment_chapter` into `segment_from_proposals`.
- Segment content = **exact** `source_content[source_start:source_end]`.
- Unit test: `test_segment_content_matches_chapter_source_slice`.

Related unit tests: 11 passed (`test_candidate_segmentation` + `test_hierarchy`).

## Second force rebuild (after fix)

| Field | Value |
|-------|--------|
| build_id | `cb_9f9aee6bf1cb427b` |
| duration | 26.8 s |
| status | committed + active pointer updated |

### Asset audit (final)

```json
{
  "novel_id": 91,
  "owner_id": 2,
  "provider_calls_allowed": true,
  "policy_version": "asset-eligibility-policy.v1"
}
```

| Asset | Status | item_count | version_id | notes |
|-------|--------|------------|------------|-------|
| **hierarchy** (required) | **reusable_exact** | 18378 | `cb_9f9aee6bf1cb427b` | reason_codes empty |
| timeline (optional) | optional_unavailable | 1933 | 14 | `optional_lineage_mismatch` (hierarchy build changed) |
| relationship (optional) | optional_unavailable | 41 | 14 | same |
| clue (optional) | optional_unavailable | 32 | 22 | same |

Optional lineage mismatch is expected after hierarchy rebuild; optional assets do **not** block `provider_calls_allowed` when hierarchy is exact.

## Durations summary

| Step | Duration |
|------|----------|
| First force rebuild (pre-fix) | ~28.9 s |
| Root-cause diagnosis | ~few minutes (scripts) |
| Second force rebuild (post-fix) | ~26.8 s |
| Final asset audit | ~2 s |
| Segmentation/hierarchy unit tests | ~0.1 s |

## Constraints honored

- No narrative-memory promote / no active NM pointer
- No reinvented hierarchy builder — `ensure_hierarchy(force=True)` only
- Code commit limited to the real bug that blocked `reusable_exact`

## Unblocks

NM candidate authority / build eligibility for novel 91 (required hierarchy gate).  
Next ops (out of this step): wire NM build CLI transport if needed; create candidate version; build L2–L4 **without** promote. Optional timeline/relationship/clue re-lineage if exact optional reuse is desired later.
