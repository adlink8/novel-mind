# Phase 21 Branch Delta Decision

## Decision

Use `master` as the only execution and planning baseline. Do not merge, rebase, or bulk
cherry-pick `feat/phase21-debtfix`.

## Rationale

The branch and `master` contain mutually incompatible migrations, tests, API contracts and
planning state. `master` has the newer governance authority; the branch has valuable but
unintegrated product capabilities. Selective reimplementation preserves both facts.

## Routing

1. Keep Phase 23–25.1 master implementations unchanged.
2. Route question/evidence context work to Phase 26.
3. Route full-book NM recovery and one-click orchestration to Phase 28.
4. Route reader-facing quality closure to Phase 29.
5. Route image generation to Phase 30–34.
6. Route Canon Fork and constrained derivative work to Phase 35–39.
7. Keep NM promotion/cutover in `999.x` backlog pending explicit authorization and frozen A/B.

## Prohibited Actions

- Treating the branch as a planning authority.
- Reporting branch-only commits as merged.
- Reusing its migrations without a new migration-head review.
- Restoring deleted master tests or contracts by taking the branch version.

## Test, Fix, and Confirm

- Confirm divergence and patch-equivalence with the commands in `MANIFEST.md`.
- Confirm representative master-only contracts exist.
- Confirm each retained branch requirement has a new roadmap owner.
- Re-run the delta audit before any later selective port.
