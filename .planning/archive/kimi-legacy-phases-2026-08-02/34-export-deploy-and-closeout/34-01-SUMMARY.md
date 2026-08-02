# Phase 34-01 Summary: Export Pipeline

## Outcome

Completed the authorized local export slice for immutable creative revisions. The API now
exports a selected revision as Markdown or a deterministic minimal EPUB; both formats include
revision provenance and never call a provider or mutate source state. The writing editor exposes
Markdown and EPUB download actions for each visible revision.

## Files

- `backend/app/services/creative_export.py`
- `backend/app/api/fanfiction.py`
- `backend/tests/test_fanfiction.py`
- `frontend/src/lib/api.ts`
- `frontend/src/components/writing/creative-project-editor.tsx`
- `frontend/src/lib/api.test.ts`
- `backend/openapi-baseline.json`
- `backend/tests/fixtures/openapi/nonbreaking.json`
- `backend/tests/fixtures/openapi/breaking.json`

## Boundary

This slice exports only Fanfiction Canon revisions. It does not generate content, include
Original Canon or Narrative Memory content, publish artifacts, deploy, change active pointers,
or cut over Reader Chat.

## Test, Fix, and Confirm

The initial Chinese `Content-Disposition` implementation returned HTTP 400 because raw Unicode
filenames are not Latin-1 encodable. It was fixed with an ASCII fallback plus RFC 5987 UTF-8
`filename*`. Backend, frontend, lint/type, migration, and OpenAPI contract checks then passed.
