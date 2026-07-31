# 22-G2 Plan — Artifact, Environment and Provider Authority

## Goal

Ensure the scheduled workflow completes on an available control-plane runner, emits an
artifact for every terminal quality state, and never confuses dependency availability with
quality qualification.

## Steps

1. Separate the always-available orchestration/preflight path from any optional
   provider-capable runner.
2. Emit a signed/schema-validated report for `passed`, `qualified`,
   `blocked_dependency`, `failed_policy` and `quality_regression`.
3. Gate baseline promotion on explicit promotability, not job success alone.
4. Make runner/provider authority visible in the report lineage.
5. Add workflow-policy tests covering missing runner/provider and no-artifact paths.
6. **Test, Fix, and Confirm:** run actionlint and CI policy tests, then inspect one scheduled
   artifact. Fix authority or promotion ambiguity before closing G2.

## Must-Haves

- Always-available orchestration path.
- Missing provider remains non-comparable with `metrics=null`.
- Only `passed|qualified` signed reports can promote a baseline.
- Artifact retention and schema checks remain fail-closed.
- No paid/provider calls are triggered merely to make CI green.

## Verification

- `actionlint -color`
- `PYTHONPATH=. pytest tests/ci -q`
- scheduled run artifact inspection.
- negative promotion test with `blocked_dependency`.
