# 20-01 SUMMARY — NM read-only structure API

## Result

Implemented product-facing **read-only** Narrative Memory structure API for Structure Workspace. Explicit `version_id` only; always `candidate_preview`; server-side `through_chapter` filters; no promotion/builder writes.

## Files changed

| Path | Role |
|------|------|
| `backend/app/schemas/narrative_memory_product.py` | Product Pydantic contracts (versions, tree, claims, source-links) |
| `backend/app/services/narrative_memory/structure_query.py` | Read-only list/tree/claims/links + pure cutoff/assembly helpers |
| `backend/app/api/narrative_memory.py` | FastAPI router (`require_user` + `require_owned_novel`) |
| `backend/app/main.py` | Register router at `/api/narrative-memory` tag 叙事记忆结构 |
| `backend/tests/unit/narrative_memory/test_structure_query.py` | Unit tests (pure + mocked session) |

## Routes

- `GET /api/narrative-memory/{novel_id}/versions`
- `GET /api/narrative-memory/{novel_id}/versions/{version_id}/tree?through_chapter=`
- `GET /api/narrative-memory/{novel_id}/versions/{version_id}/nodes/{node_id}/claims?through_chapter=`
- `GET /api/narrative-memory/{novel_id}/versions/{version_id}/nodes/{node_id}/source-links?through_chapter=`

## Design notes

- **Preview path** does not call `load_eligible_version` (too strict for incomplete builds). Owner-scoped SELECT only; never writes.
- **Visibility**: nodes with `chapter_end <= through_chapter`; claims `visible_from_chapter <= through_chapter`; source links `chapter_number <= through_chapter` via visible claims.
- **Readiness** (best-effort): `empty` | `incomplete` | `preview_eligible` | `sealed_candidate` from node counts + optional manifest/validation report.
- **through_chapter**: optional; defaults to novel `chapter_count` or a high sentinel; clamped to novel max; rejects `< 1`.

## Tests run

```
cd backend
.\.venv\Scripts\python.exe -m pytest tests/unit/narrative_memory/test_structure_query.py -q --tb=short
```

**12 passed** (empty versions, cutoff filters nodes, candidate_preview always, claims cutoff, foreign version 404, readiness, assembly).

## Residual risks

- Mocked DB tests do not exercise real PostgreSQL filters; integration against live NM rows deferred.
- Readiness is heuristic (not full builder stage graph); product should treat non-`sealed_candidate` as incomplete preview.
- Superuser ownership: `require_owned_novel` allows superuser read of any novel; structure_query scopes by `current_user.id` — superuser browsing another owner’s novel may 404 versions. Align later if product needs superuser cross-owner preview.
- No FE (20-02).

## Non-goals confirmed

- No NM promotion / active pointer
- No builder start
- No Phase 08/09/11 worker changes
