# Phase 38 Validation — Nyquist Gate

## Fixture matrix

Immutable Original Visual Bible snapshot; derivative fork; three Scene Specs sharing one identity; intentional style divergence; wrong namespace/path; changed source hash; rejected/accepted candidate; two owners; mixed original/derivative asset list.

## Automated tests (planned)

```text
cd backend; pytest tests/unit/derivative_visual tests/adversarial/test_visual_namespace_isolation.py -q
cd backend; pytest tests/integration/test_derivative_visual.py -q
cd frontend; npm test -- visual-review
cd frontend; npm run test:e2e:desktop -- derivative-visual.spec.ts
```

Map: `REQ-FORK-04` → source immutability/namespace tests; `REQ-CRE-06` → explicit divergence/gate tests. Files are Wave 0 gaps; commands not run during research.

## Manual UAT

Fork Visual Bible, create Scene Spec, generate candidate fixture, inspect identity/source/divergence, reject then accept, compare three chapters, refresh, and verify Original references and index are unchanged. Repeat at 390px for review controls and error state.

## Failure policy

Any original asset mutation, source hash mismatch, wrong namespace, hidden divergence or owner leak is BLOCKED; do not delete offending evidence to make the suite pass.
