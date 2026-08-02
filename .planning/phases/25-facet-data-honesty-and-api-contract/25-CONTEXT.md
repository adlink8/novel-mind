# Phase 25 Context — Facet data honesty and API contracts

**Gathered:** 2026-07-27  
**Status:** Complete in merged PRs #16 and #18; GSD artifacts are being reconciled retroactively.

## Boundary

Make clue/relationship provenance and cost fields honest, retire misleading placeholder API contracts, and keep fanfiction explicitly deferred to v1.4. No NM promotion or consumer cutover.

## Decisions

- `short_title` is separate from judge rationale; existing records are not silently rewritten.
- Relationship observations carry intake/producer lineage through API/UI.
- Deprecated endpoints return an honest contract; fanfiction remains deferred rather than fake-successful.
