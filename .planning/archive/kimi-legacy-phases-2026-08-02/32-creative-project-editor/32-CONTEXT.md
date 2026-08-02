# Phase 32 Context: Creative Project & Editor

## Scope

Implement the local creative-project workflow that replaces the fanfiction API placeholder: owner-scoped project CRUD, Markdown editing with autosave, chapter planning, revision history with diff and rollback, and a real `/writing` entry point.

## Boundaries

- Creative content remains in Fanfiction Canon only.
- No model calls, generation, provider routing, paid resources, or external writes.
- No Narrative Memory promotion, active-pointer mutation, or Reader Chat cutover.
- Existing owner/superuser authorization conventions remain authoritative.
- AI continuation remains an explicit `501` deferred boundary until a separately authorized generation phase.

## Decisions

- Store immutable revision snapshots in `fanfiction_revisions`; rollback creates a new snapshot rather than deleting history.
- Revision scope is project-wide, with `chapter_id` identifying chapter snapshots and `NULL` identifying project Markdown snapshots.
- Diff is a deterministic unified text diff between two owner-scoped snapshots.
- The editor exposes project and chapter Markdown as local text areas; autosave uses the existing authenticated API and keeps manual save as an explicit fallback.

## Verification target

Backend unit/API tests, migration matrix, OpenAPI contract, frontend API/component checks, lint/compile, and a production-build type check where the local toolchain permits.
