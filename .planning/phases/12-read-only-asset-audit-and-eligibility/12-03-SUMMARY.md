---
phase: 12-read-only-asset-audit-and-eligibility
plan: 03
status: complete
completed: 2026-07-15
requirements: [V08-AUDIT-01, V08-AUDIT-03, V08-AUDIT-04]
key-files:
  created:
    - backend/app/api/asset_audit.py
    - backend/scripts/run_asset_audit.py
    - backend/tests/integration/narrative_memory/test_audit_entrypoints.py
    - backend/tests/integration/narrative_memory/test_audit_no_side_effects.py
  modified:
    - backend/app/main.py
    - backend/app/services/narrative_memory/audit_contracts.py
    - backend/app/services/narrative_memory/audit_pg.py
---

# Phase 12 Plan 03 Summary

## Result

Delivered reproducible administrator API and CLI audit entry points plus independent authority before/after and forbidden-capability checks. The report is round-trippable canonical JSON and the CLI exits `0` only when required assets are exactly reusable, otherwise `2`.

## Implemented

- `GET /api/admin/asset-audit/{novel_id}?owner_id=...`, restricted to authenticated superusers.
- `scripts/run_asset_audit.py --owner-id ... --novel-id ...`, with canonical JSON and deterministic exit status.
- API and CLI call the same SELECT-only service and produce identical reports.
- Fresh PostgreSQL observer compares build/node counts, relationship runs, pointer journals, and active chunk/timeline/clue pointers before and after audit.
- Capability scan proves the package and entry points contain no model gateway/provider, promotion, active-pointer setter, session mutation, commit, or flush path.
- Deferred `Chapter.content` is explicitly loaded, closing a production-only `MissingGreenlet` failure found by the real CLI smoke test.
- Chapter 0 is supported for imported prologues; rebuild chapter numbers are coalesced into compact ranges.

## Real read-only audit evidence

- Novel 91 (`关于我转生成史莱姆这件事`): completed without mutation; required hierarchy is `rebuild_required` because evidence content cannot be re-sliced exactly; optional timeline/relationship/clue are `optional_unavailable`; CLI exit `2`.
- Novel 104 (`龙族（1-4合集）`): completed without mutation; hierarchy build `cb_29ed4f483982455d`, 9,413 hierarchy rows, `content_hash_mismatch`, minimal coalesced rebuild range chapter `0–419`; optional sources unavailable; CLI exit `2`.
- These results block provider calls by design. No existing book was reanalyzed or repaired.

## Verification

- Full Phase 12 unit + fresh migrated PostgreSQL integration: **21 passed** before the final production-smoke fixes; targeted post-fix unit and deferred-content tests also passed.
- Ruff over API, service, CLI, and tests: **passed**.
- Live read-only CLI smoke executed successfully for novels 91 and 104 after fixing deferred content/chapter-0 handling.
- Existing pytest timeout configuration warnings remain unchanged.

## Commit

- `863c23c feat(12-03): expose read-only asset audit`

## Outcome

Phase 12 is implementation-complete. Both real books are safely classified as needing hierarchy rebuild before Phase 14 may call a provider; Phase 13 contract work can proceed without rebuilding them.
