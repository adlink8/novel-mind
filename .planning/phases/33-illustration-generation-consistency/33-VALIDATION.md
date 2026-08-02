# Phase 33: Illustration Generation and Consistency - Validation

## Nyquist strategy

### Fixtures

- `illustration-mock-success`: deterministic bytes, usage, cost, request ID.
- `illustration-duplicate`: same idempotency key submitted concurrently.
- `illustration-failure`: schema error, timeout, provider 5xx, unknown outcome, budget exhaustion.
- `illustration-consistency`: same character across 3 scenes, deliberate identity drift, style drift, unsupported detail.
- `illustration-review`: candidate compare, mark_proposal_ready, reject, supersede, retry.

### Commands

|层|检查|命令|
|---|---|---|
|unit|job state machine, idempotency key, budget, storage path, evaluator|`cd backend; pytest tests/unit/illustrations -q`|
|integration|mock provider, concurrent duplicate, retry/reconcile, owner isolation|`cd backend; pytest tests/integration/illustrations -q`|
|frontend|gallery, compare, status/errors, approval|`cd frontend; npm test -- illustrations`|
|browser|desktop/mobile approval and failure recovery|`cd frontend; npm run test:e2e -- illustrations --project=chromium-desktop --project=chromium-mobile-390`|

### Manual UAT / optional live canary

1. Run mock generation and inspect exact SceneSpec/prompt/model/config lineage.
2. Trigger timeout; verify no false success and a recoverable status.
3. Submit duplicate requests; verify one charge/one result.
4. Inspect consistency drift report; verify it requires human decision.
5. Only after explicit provider/budget decision, run one low-cost live canary and archive provider request/usage/cost evidence.

### Gate

No image may be handed to Phase 34 unless `proposal_ready` plus complete
source/prompt/model/asset lineage is present. Phase 33 cannot publish or make it reader-visible.
