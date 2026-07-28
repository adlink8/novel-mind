# 06-06 Summary — Unified CI Producer DAG, Security, Artifacts & Nightly

**Status:** COMPLETE  
**Date:** 2026-07-12  
**Plan:** `.planning/phases/06-automated-quality-ci/06-06-PLAN.md`  
**Decisions:** D-10, D-13, D-14, D-16, D-17, D-18

## What Was Done

### Slice 1 — Unified DAG, fork safety, security

- New primary workflow `.github/workflows/ci.yml` (producer DAG only; no ci-gate)
- Disabled legacy workflows (`backend-ci.yml`, `frontend-ci.yml`, `full-ci.yml`) — no push/PR/schedule triggers (`workflow_call` stubs only)
- Event matrix:
  - **PR:** secretless static/unit/openapi/integration/browser/codeql/actionlint
  - **protected main:** full integration + live environment + alert eligibility
  - **schedule:** nightly self-hosted dual-model + promote + alert
  - **workflow_dispatch:** protected ref + 40-char `benchmark_commit` + optional nightly
- Fork safety: no `pull_request_target`; `live`/`nightly`/`promote-baseline`/`alert` gated on `allow_*` (never true on PR)
- Concurrency: `workflow+ref`; cancel-in-progress only for `pull_request` (nightly not cancelled mid-run)
- Job timeouts (D-16): static 5 / unit 10 / integration 15 / browser 15 / live 45 / nightly 60
- CodeQL once (Python + javascript-typescript) via `.github/codeql/codeql-config.yml`
- actionlint **v1.7.12** + `.github/actionlint.yaml` (custom `ollama` self-hosted label)
- Audits: Ruff, Bandit, pip-audit, npm audit, actionlint
- Service lock still fail-closed (Postgres 16.10 + Chroma 1.5.9 digests); phase tag → 06-06
- `scripts/ci/validate-workflow.py` + `tests/ci/test_workflow_security.py`

### Slice 2 — Artifacts, nightly, baseline, isolated alerts

- Retention (D-17):
  - PR JUnit/coverage/OpenAPI: **14d**
  - Playwright failure: **7d**
  - main integration/service logs: **30d**
  - nightly signed reports/baselines: **180d**
- Forbidden artifact paths: `uploads/**`, fulltext globs; content markers blocked
- Nightly: self-hosted `[self-hosted, linux, ollama]`, 3-repeat via `run_rag_quality.py --live-health --durable`
- `scripts/ci/promote-baseline.py` prepare→commit: only `passed`/`qualified` + valid schema + HMAC signature
- Alert job (D-18): environment `quality-alerts`, permissions `contents:read` + `issues:write` only, **no checkout**, consumes validated report artifacts, fork unreachable, issue dedup by fingerprint
- Policy: `.github/quality/baseline-policy.yml` (flake PR=0, 30d failure rate &lt;0.1%)
- Tests: `test_artifact_policy.py`, `test_baseline_promotion.py`

## Verification

```text
# actionlint v1.7.12
go install github.com/rhysd/actionlint/cmd/actionlint@v1.7.12
actionlint -format '{{json .}}' .github/workflows/ci.yml \
  .github/workflows/backend-ci.yml .github/workflows/frontend-ci.yml \
  .github/workflows/full-ci.yml
# → []  exit 0

python scripts/ci/validate-workflow.py
# → [OK] workflow policy valid

pytest tests/ci/test_workflow_security.py \
  tests/ci/test_artifact_policy.py \
  tests/ci/test_baseline_promotion.py -q
# → 49 passed
```

## Files Changed (06-06 scope)

| Path | Role |
|------|------|
| `.github/workflows/ci.yml` | Unified producer DAG |
| `.github/workflows/backend-ci.yml` | Disabled stub |
| `.github/workflows/frontend-ci.yml` | Disabled stub |
| `.github/workflows/full-ci.yml` | Disabled stub |
| `.github/codeql/codeql-config.yml` | CodeQL paths/queries |
| `.github/actionlint.yaml` | Self-hosted label allowlist |
| `.github/ci/service-lock.json` | Extended metadata for 06-06 |
| `.github/quality/baseline-policy.yml` | Retention/promotion/alert/timeouts |
| `scripts/ci/validate-workflow.py` | Workflow policy validator |
| `scripts/ci/promote-baseline.py` | Signed baseline prepare/commit |
| `tests/ci/test_*.py` | Security, artifact, promotion contracts |

## Deviations / notes

1. **actionlint `-format`:** Plan text said `-format JSON`; v1.7.12 requires Go template (`-format '{{json .}}'`). Empty array `[]` = clean.
2. **Self-hosted label `ollama`:** Declared in `.github/actionlint.yaml` so actionlint accepts custom runner labels.
3. **Legacy workflows retained as `workflow_call` stubs** (not deleted) so history/refs remain; they never auto-trigger.
4. **ci-gate / branch protection intentionally omitted** (06-07).

## Out of Scope (confirmed)

- Final `ci-gate` aggregate job
- Branch protection / `gh api` required contexts
- Phase release gate verifier

## Next

- Do **not** start 06-07 from this plan unless scheduled.
- 06-07 consumes producer results → `ci-gate` + branch protection + release gate.
