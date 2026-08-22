# Phase 37 Validation — Nyquist Gate

## Frozen fixtures

One short novel snapshot with state at chapter 2: valid continuation; wrong character knowledge; impossible causal order; unresolved clue incorrectly paid off; missing world rule; citation outside package; explicit allowed divergence; and a branch-suggestion response with a triggering conflict, canonical delta hash and evidence refs. Provider responses are deterministic fixture payloads.

## Automated tests (planned)

```text
cd backend; pytest tests/unit/derivative_generation -q
cd backend; pytest tests/adversarial/test_derivative_generation_boundaries.py -q
cd backend; pytest tests/integration/test_derivative_generation.py -q
```

Map: `REQ-CRE-05` → package contract/hash/ref tests; `REQ-CRE-06` → contradiction and override adversarial tests; `REQ-FORK-03` → branch/no-original-write integration. Wave 0 creates fixtures and test files; commands not run now.

`REQ-FORK-06` → `BranchSuggestion` schema/fixture tests: all six fields are present, `enabled_by_default` is literally `false`, no suggestion auto-creates a fork, and `allow_divergence` approval cannot be reused for `publish_derivative_revision`; publication requires a separate approval bound to the same exact hashes.

```text
cd agent-service; npx vitest run tests/skills/continue-derivative-story.test.ts
cd backend; pytest tests/integration/agent_runtime/test_phase_37_skill.py -q
```

## Manual UAT

Select fork/cutoff/intent, inspect compiled package before generation, request candidate, inspect gate report, review a disabled-by-default BranchSuggestion, verify it does not auto-fork, accept only valid candidate, submit explicit divergence, retry/cancel a job, and confirm Original retrieval/eval/facet remain unchanged. Capture model/prompt/schema/config hashes and verdict.

## Failure policy

Invalid schema/evidence/out-of-cutoff/constraint violation is `blocked` or `needs_override`, never silently repaired or published. A BranchSuggestion with missing fields, default enabled state, automatic fork side effect, reused divergence approval or absent independent publish approval is a failure. Provider success without gate success is not a pass.
