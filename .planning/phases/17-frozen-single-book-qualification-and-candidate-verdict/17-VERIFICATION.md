---
phase: 17-frozen-single-book-qualification-and-candidate-verdict
status: passed
verified_at: 2026-07-16
---

# Phase 17 Verification: Frozen Single-book Qualification and Candidate Verdict

## Status: passed

## Scope statement (mandatory)

Single-book **candidate-only** qualification. Verdict is only `qualified_candidate` or `blocked`.  
Does **not** promote, activate, or cut over timeline / relationship / clue / Reader Chat.  
Does **not** claim closure of v0.3 project-wide 100-confirmed, faithfulness, or cost gaps.

## Must-haves

| Must-have | Status | Evidence |
| --- | --- | --- |
| Fixture/policy frozen before results; no result fields | VERIFIED | `qualification_fixtures` + unit tests; hashes stable |
| Five non-empty buckets with gold Phase 07 leaves | VERIFIED | `single_book_v1.json` counts; PG re-slice |
| Paired envelopes differ only by strategy/cache | VERIFIED | `assert_envelopes_paired` + runner tests |
| Complete metrics fail-closed | VERIFIED | `qualification_metrics` + unit |
| Two-verdict pure evaluator; Judge non-authority | VERIFIED | adversarial spoiler/unsupported |
| Append-only PG authority; no selector | VERIFIED | migration + authority PG |
| Fresh verifier pointer before/after | VERIFIED | verifier PG |
| Fixed CLI digests; exit 0 only qualified | VERIFIED | command PG |
| No promotion/API/consumer cutover | VERIFIED | CI contract scan |

## Test results

```text
63 passed (unit + adversarial + CI + integration PG)
ruff: clean on qualification_* + models + script + tests
alembic head: 17memqual01 (upgrade/downgrade round-trip)
```

## Fixture / policy digests

- fixture: `d311ff26d0da1af2dd5407d6007c522413ab4012c2d18f4e8cb7f241c4513578`
- policy: `aab9a983391ca88f1c7be2c011af901092a6d965a63e8a1483940b447becd4ce`

## Residual / out of scope

- Live-provider single-book run requires separate hard-budget operator acknowledgement (not CI authority).
- Deterministic hierarchical path uses stub favoring gold leaves; production Phase 15 experiment is wired for offline use when eligibility rows exist (`--require-version-rows`).
- Phase 13 structural `qualified_candidate` is distinct from Phase 17 quality qualification (separate tables/schemas).

## Plans

- [x] 17-01
- [x] 17-02
- [x] 17-03
