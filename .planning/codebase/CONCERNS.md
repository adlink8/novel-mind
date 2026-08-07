---
last_mapped_commit: b679b49
---

# Codebase Concerns

**Analysis Date:** 2026-08-07

## Executive Risk Register

| Severity | Concern | Primary evidence | Recommended priority |
|---|---|---|---|
| High | Release qualification remains blocked at 0/3 real scheduled green runs | `.planning/STATE.md`, `.github/workflows/ci.yml` | Restore the operator-owned runner path and collect three consecutive comparable signed runs before any production claim. |
| High | Declared coverage thresholds are not applied to real CI coverage output | `.quality/coverage-policy.yml`, `.github/workflows/ci.yml`, `backend/tests/test_test_policy.py` | Add a CI evaluator that parses backend/frontend reports and fails on overall, critical, branch, and diff thresholds. |
| High | First public registrant becomes superuser without a bootstrap credential | `backend/app/api/auth.py` | Require an explicit one-time bootstrap token or offline admin provisioning and close public bootstrap after initialization. |
| High | Planning authority is stale relative to the mapped commit and Phase 40 implementation | `.planning/config.json`, `.planning/STATE.md`, `.planning/ROADMAP.md`, `IMPLEMENTATION-STATUS.md` | Reconcile the cursor, baseline commit, roadmap scope, and Phase 40 artifacts in one governance update. |
| Medium | Agent poller can exceed its configured concurrency because timer ticks overlap | `agent-service/src/poller.ts`, `agent-service/tests/poller.test.ts` | Serialize ticks or maintain one global in-flight semaphore and add a slow-run overlap test. |
| Medium | Large API/service/UI modules concentrate unrelated responsibilities | `backend/app/services/rag_quality.py`, `backend/app/services/scene_spec/compiler.py`, `backend/app/services/agent_tools/facade.py`, `frontend/src/lib/api.ts`, `frontend/src/app/analysis/page.tsx` | Split only along existing domain contracts, keeping wire types and transaction ownership explicit. |
| Medium | Illustration gallery performs multiple database round trips per asset | `backend/app/services/illustrations/review.py` | Batch-load jobs, events, consistency reports, and proposal state, then enforce a query-count regression test. |
| Medium | Runtime reproducibility and dependency risk are uneven | `backend/requirements.txt`, `frontend/package.json`, `agent-service/package.json`, `.github/workflows/ci.yml` | Lock backend production dependencies, exit Next canary when feasible, and retain time-bounded vulnerability waivers. |

No confirmed Critical issue was found in the inspected tree. “High” means release-blocking, privilege-impacting, or capable of making a quality gate materially misleading. “Medium” means a credible reliability, maintainability, performance, or defense-in-depth failure that is bounded by current mitigations. “Low” means localized debt with a clear workaround.

## Governance / Coupling / Quality / Style Scorecard

**Scoring basis:** 0 = absent or unmanaged, 1 = ad hoc, 2 = partial with major gaps, 3 = defined and usually enforced, 4 = strong automated enforcement, 5 = comprehensive and continuously evidenced. Scores reflect commit `b679b49`, not historical phase claims.

| Dimension | Score | Evidence-based rationale |
|---|---:|---|
| Governance | 2.5/5 | Strong GSD artifacts and fail-closed release language exist in `.planning/STATE.md` and `.planning/ROADMAP.md`, but `.planning/config.json` still pins `912ca6b`, Phase 40 has no normal phase directory, and production qualification remains 0/3. |
| Coupling | 2.0/5 | Domain folders are present, but `backend/app/services/agent_tools/facade.py` is 1,535 lines, `backend/app/services/scene_spec/compiler.py` is 1,740 lines, `frontend/src/lib/api.ts` is 1,340 lines, and service classes live inside `backend/app/api/illustrations.py` and `backend/app/api/derivative_generation.py`. |
| Code quality | 3.0/5 | The repository has extensive unit, integration, contract, adversarial, and browser suites under `backend/tests/`, `agent-service/tests/`, and `frontend/e2e/`; however, real coverage thresholds are not enforced, Python has no static type-check gate, and the agent service has no coverage policy. |
| Style consistency | 3.0/5 | Ruff formatting and Next ESLint run in `.github/workflows/ci.yml`, but `backend/ruff.toml` selects only `E4`, `E7`, `E9`, and `F`; production code contains numerous targeted suppressions and oversized modules with mixed API/service responsibilities. |
| Security posture | 3.0/5 | Owner isolation, JWT validation, secret validation, Bandit, audit jobs, and adversarial tests are present in `backend/app/core/security.py`, `.github/workflows/ci.yml`, and `backend/tests/adversarial/`; bootstrap administration, login throttling, browser token exposure, and a waived Chroma advisory remain. |
| Reliability / operability | 2.5/5 | Durable leases and recovery exist for selected flows such as `backend/app/services/import_service.py`, but many jobs still launch through FastAPI `BackgroundTasks`, poller ticks can overlap, and the scheduled provider gate has no successful evidence. |

## Tech Debt

### High — Coverage policy is declarative, not an executing gate

- Issue: `.quality/coverage-policy.yml` declares backend 80/70, frontend 75/65, critical-file, and 90% diff-coverage thresholds. CI generates `backend/artifacts/backend-coverage.xml` and frontend coverage in `.github/workflows/ci.yml`, but neither command uses a fail-under threshold and no workflow step passes the generated reports to `evaluate_coverage_report`.
- Files: `.quality/coverage-policy.yml`, `.github/workflows/ci.yml`, `backend/tests/test_test_policy.py`, `frontend/vitest.config.ts`, `frontend/src/__tests__/coverage-policy.test.ts`
- Impact: CI can remain green while actual coverage is below the documented policy. The tests prove that a synthetic low report would fail, not that the current repository report is evaluated.
- Fix approach: Move the evaluator out of `backend/tests/test_test_policy.py` into a reusable CI script, parse Cobertura/Vitest output, calculate critical-glob and changed-line coverage, and fail the `unit` job before artifact upload.

### High — Planning cursor and baseline drift

- Issue: HEAD is mapped at `b679b49`; `.planning/config.json` and the frontmatter of `.planning/STATE.md` still use baseline `912ca6b`, while `.planning/STATE.md` appends Phase 40 work and `IMPLEMENTATION-STATUS.md` records Phase 40 at later commits. `.planning/ROADMAP.md` still snapshots `912ca6b` and formally ends at Phase 39.
- Files: `.planning/config.json`, `.planning/STATE.md`, `.planning/ROADMAP.md`, `IMPLEMENTATION-STATUS.md`, `agent-service/src/poller.ts`, `backend/tests/integration/agent_runtime/test_phase_40_backfill.py`
- Impact: Commands using `.planning/STATE.md` as the single cursor can select the wrong active plan or compare verification against a stale baseline. Phase 40 code is real but lacks the same phase artifact lifecycle used for Phases 25.2–39.
- Fix approach: Decide whether Phase 40 is a formal phase or an explicitly governed hotfix stream; then atomically update baseline/cursor/roadmap and add matching context, plan, summary, validation, and verification evidence if it is formal.

### Medium — Backend dependency ranges are not reproducible

- Issue: Nearly all backend runtime and development packages use open lower bounds, whereas `agent-service/package.json` pins exact versions and both Node projects commit lockfiles.
- Files: `backend/requirements.txt`, `backend/requirements-dev.txt`, `agent-service/package.json`, `agent-service/package-lock.json`, `frontend/package-lock.json`
- Impact: A clean CI install can resolve different transitive versions without source changes, producing non-reproducible failures or silently changing behavior and security exposure.
- Fix approach: Generate and commit a hash-checked Python lock/constraints file for supported Python versions; keep human-edited top-level intent in `backend/requirements*.txt` and make CI install the resolved lock.

### Medium — Framework canary is used in the production dependency set

- Issue: `frontend/package.json` pins Next.js and its ESLint config to a canary release.
- Files: `frontend/package.json`, `frontend/package-lock.json`, `frontend/next.config.mjs`
- Impact: Canary behavior and build tooling can change outside stable support expectations. `.planning/STATE.md` already records a canary dev-server compilation limitation, making browser verification environment-sensitive.
- Fix approach: Qualify a stable Next.js release against unit, type, build, and Playwright gates; keep the canary only behind an explicit compatibility spike if a required feature depends on it.

### Medium — Python static analysis is intentionally narrow

- Issue: `backend/ruff.toml` selects only syntax/import-related `E4`, `E7`, `E9`, and `F` rules. CI runs no mypy or pyright gate, while production code contains `type: ignore`, private-member access, and broad-exception suppressions.
- Files: `backend/ruff.toml`, `.github/workflows/ci.yml`, `backend/app/services/scene_spec/compiler.py`, `backend/app/services/narrative_memory/recovery.py`, `backend/app/services/agent_tools/facade.py`
- Impact: Contract drift and invalid optional/union assumptions can survive until runtime, particularly in large orchestration modules.
- Fix approach: Introduce typing incrementally by critical package, beginning with `backend/app/core/`, `backend/app/services/agent_runtime/`, and publication/approval boundaries; ratchet errors rather than enabling whole-tree strictness at once.

### Low — React hook suppressions preserve fragile synchronization

- Issue: `frontend/src/app/analysis/page.tsx` suppresses exhaustive-dependency and set-state-in-effect rules; `frontend/src/components/app-shell.tsx` also suppresses state-in-effect for local hydration.
- Files: `frontend/src/app/analysis/page.tsx`, `frontend/src/components/app-shell.tsx`
- Impact: A dependency change can cause stale closures, repeated polling, or extra renders without lint detecting it.
- Fix approach: Extract polling and hydration into tested hooks with explicit state machines; use lazy state initialization where browser-only access permits it.

## Known Bugs

### Medium — Poller concurrency limit is per tick, not global

- Symptoms: `createPoller` starts `tick()` immediately and then invokes it through `setInterval`; no `inFlightTick` guard or shared semaphore prevents a second tick while the first is awaiting model execution. Each tick independently slices up to `concurrency` items.
- Files: `agent-service/src/poller.ts`, `agent-service/tests/poller.test.ts`
- Trigger: Make a skill execution take longer than `pollIntervalMs` while queued work remains. Multiple ticks can claim distinct runs, so total active executions can exceed the configured concurrency.
- Workaround: Increase the polling interval above worst-case execution time or run with a lower operational queue rate; database claim conflict prevents duplicate ownership of one run but does not enforce process-wide concurrency.

### Medium — Legacy fanfiction surface permanently returns a stale deferral

- Symptoms: Every route under `/api/fanfiction` returns HTTP 501 and says the feature is deferred to v1.4, while derivative project/editor/generation/export routes are mounted and v1.4 is recorded complete.
- Files: `backend/app/api/fanfiction.py`, `backend/app/api/__init__.py`, `backend/app/main.py`, `IMPLEMENTATION-STATUS.md`
- Trigger: Call any mounted `/api/fanfiction` endpoint after the v1.4 derivative feature set is deployed.
- Workaround: Use the `/api/derivative-*` APIs directly.
- Fix approach: Remove the legacy route in a versioned breaking change, or implement an authenticated compatibility adapter that maps old operations to the derivative domain without weakening canon isolation.

## Security Considerations

### High — Unauthenticated first-user superuser bootstrap

- Risk: The public registration endpoint marks the first active user as `is_superuser=True`. On a newly deployed database reachable before operator provisioning, the first external registrant can claim administrative authority.
- Files: `backend/app/api/auth.py`, `backend/app/models/user.py`, `backend/tests/adversarial/test_agent_tools_adversarial.py`
- Current mitigation: PostgreSQL advisory locking prevents two concurrent registrations from both becoming the first admin; it does not authenticate who is allowed to bootstrap.
- Recommendations: Require a one-time bootstrap secret delivered out-of-band, provision the initial admin through an operator command, or disable registration until initialization is complete. Add adversarial tests for unauthorized bootstrap and bootstrap closure.

### Medium — Authentication endpoints have no rate limit or lockout

- Risk: `/api/auth/login` and `/api/auth/register` perform database work and bcrypt verification without an application-level throttle, enabling credential stuffing and resource exhaustion when publicly exposed.
- Files: `backend/app/api/auth.py`, `backend/requirements.txt`, `backend/app/main.py`
- Current mitigation: Login errors do not disclose whether username or password was wrong, and passwords are bcrypt-hashed in `backend/app/core/security.py`.
- Recommendations: Add per-IP and per-account throttling at the trusted edge and application boundary, bounded retry delays, audit events, and tests that fail closed when proxy identity is untrusted.

### Medium — Browser bearer token remains script-readable and no CSP is configured

- Risk: Login sets an HttpOnly cookie but also returns the access token; `frontend/src/lib/api.ts` persists it in `sessionStorage` and injects it into requests. Any successful same-origin script injection can read the bearer token. `frontend/next.config.mjs` defines rewrites but no Content-Security-Policy header.
- Files: `backend/app/api/auth.py`, `frontend/src/lib/api.ts`, `frontend/src/lib/sse.ts`, `frontend/next.config.mjs`, `frontend/src/app/layout.tsx`
- Current mitigation: Session storage limits persistence to the tab session, the cookie is HttpOnly/SameSite, and React renders ordinary content as text in reviewed components.
- Recommendations: Prefer cookie-authenticated browser calls with CSRF protection or an in-memory short-lived bearer for SSE; deploy a nonce/hash-based CSP and explicitly inventory intentional inline scripts.

### Medium — Known Chroma advisory is waived

- Risk: CI explicitly ignores `PYSEC-2026-311` for the pinned Chroma client while waiting for an upstream-fixed compatible release.
- Files: `backend/requirements.txt`, `.github/workflows/ci.yml`
- Current mitigation: The client version is pinned and CI documents the compatibility reason; other Python advisories remain gated by `pip-audit`.
- Recommendations: Keep Chroma network-isolated, record exposure assumptions, assign an owner and review date, and remove the waiver immediately when a compatible fixed release exists.

### Low — Production safety depends on debug being explicitly disabled

- Risk: `Settings.debug` and `auth_cookie_secure` default to development values. Production secret validation runs only when debug is false.
- Files: `backend/app/config.py`, `backend/app/main.py`
- Current mitigation: When debug is false, validators reject default/short/reused secrets; database URLs are logged with passwords hidden.
- Recommendations: Make deployment manifests set production mode and secure cookies explicitly, add a production-profile startup test, and fail deployment health checks when debug remains enabled on a public host.

## Performance Bottlenecks

### Medium — Illustration gallery has O(n) query amplification

- Problem: `build_gallery` loads all assets, then performs separate job, review-event, consistency, and candidate-gate lookups inside the asset loop.
- Files: `backend/app/services/illustrations/review.py`, `backend/app/api/illustrations.py`, `backend/tests/integration/illustrations/test_review.py`
- Cause: The response assembler uses per-row service calls instead of bulk queries or eager loading.
- Improvement path: Fetch jobs by ID set, events grouped by asset ID, latest reports via a window/subquery, and proposal inputs in batches. Add query-count assertions for galleries of 1, 10, and 100 assets.

### Medium — Monolithic orchestration increases change and execution cost

- Problem: Several production files exceed 1,000 lines and combine DTO conversion, persistence, policy, orchestration, and transport concerns.
- Files: `backend/app/services/rag_quality.py`, `backend/app/services/scene_spec/compiler.py`, `backend/app/services/agent_tools/facade.py`, `backend/app/services/narrative_memory/builder_worker.py`, `frontend/src/lib/api.ts`, `frontend/src/app/analysis/page.tsx`
- Cause: Each phase extended central facades and pages rather than adding narrow domain adapters with stable boundaries.
- Improvement path: Split by existing contracts and transaction boundaries. Avoid a broad rewrite; first isolate pure validation/serialization, then repositories, then orchestration. Preserve contract tests around each extracted seam.

### Low — Large media and vendored archives increase clone/install footprint

- Problem: The repository tracks multi-megabyte GIFs and vendored package archives.
- Files: `docs/images/flipbook-turn.gif`, `docs/images/shelf-open-book.gif`, `agent-service/vendor/pi-packages/pi-mcp-adapter-2.17.0.tgz`, `agent-service/vendor/pi-packages/earendil-works-pi-agent-core-0.83.0.tgz`
- Cause: Documentation animation and offline package governance are stored directly in Git.
- Improvement path: Keep vendoring only where supply-chain policy requires it, verify checksums through `agent-service/vendor/pi-packages/CHECKSUMS.txt`, and optimize documentation media without rewriting history unless explicitly authorized.

## Fragile Areas

### Agent tool facade and structured-output materialization

- Files: `backend/app/services/agent_tools/facade.py`, `backend/app/services/agent_runtime/structured_output_integrity.py`, `backend/app/services/agent_runtime/materializers.py`, `agent-service/src/structured-output/analysis-envelope-builder.ts`
- Why fragile: The Python and TypeScript services share envelope types, hashes, tool names, evidence rules, and terminal-state semantics without one generated cross-language schema package. Central files are large and Phase 40 routing/materialization changes cross both processes.
- Safe modification: Update skill schema, registry, TypeScript envelope builder, Python validator/materializer, and cross-language fixtures together. Preserve fail-closed behavior for unknown types and missing evidence.
- Test coverage: Extensive phase integration tests exist under `backend/tests/integration/agent_runtime/` and `agent-service/tests/`, but no unified coverage threshold applies to `agent-service/`.

### Background job dispatch and recovery

- Files: `backend/app/api/novels.py`, `backend/app/api/rag.py`, `backend/app/api/clues.py`, `backend/app/api/timeline.py`, `backend/app/api/reader_chat.py`, `backend/app/api/illustrations.py`, `backend/app/main.py`
- Why fragile: Multiple durable job rows are launched with request-process `BackgroundTasks`. Import startup recovery is explicit in `backend/app/main.py`, but equivalent startup redispatch is not centralized for every job family. A process exit after commit and before task execution can leave work awaiting manual retry or family-specific recovery.
- Safe modification: Introduce one durable worker/queue contract with atomic claim, lease expiry, idempotent retry, and startup reconciliation. Migrate one job family at a time and retain existing status APIs.
- Test coverage: Individual lease/retry tests exist, but there is no cross-family crash-window test that commits a job, suppresses dispatch, restarts the service, and proves eventual terminal state.

### Configuration portability

- Files: `backend/app/config.py`, `frontend/next.config.mjs`, `agent-service/src/config.ts`, `Makefile`
- Why fragile: Backend defaults include machine-specific model and SDK paths, while frontend and agent-service default to fixed local ports. A developer can start a partially functional stack whose AI provider path silently differs from CI.
- Safe modification: Move host-specific paths into documented environment profiles, keep safe portable defaults, and add a configuration diagnostic that reports capability availability without exposing credential values.
- Test coverage: No direct backend tests reference the Vertex adapter or its machine-path settings.

### API/service boundary erosion

- Files: `backend/app/api/illustrations.py`, `backend/app/api/derivative_generation.py`, `backend/app/services/illustrations/worker.py`, `backend/app/services/derivative_generation/runner.py`
- Why fragile: `IllustrationJobService` and `DerivativeGenerationJobService` are defined in API modules while related domain logic also lives under services. This makes transport modules transaction owners and reusable business services simultaneously.
- Safe modification: Move service classes to their domain service packages, leave dependency wiring and HTTP error translation in API modules, and verify route behavior with existing integration suites.
- Test coverage: Integration tests cover behavior, but there is no architecture test preventing future service implementations from being added under `backend/app/api/`.

## Scaling Limits

### Agent backfill worker concurrency

- Current capacity: `agent-service/src/poller.ts` applies `min(items.length, concurrency)` within each tick.
- Limit: Because ticks overlap, actual process concurrency is approximately the sum of all still-running ticks rather than the configured value; long model calls can grow active sessions until upstream capacity or memory is exhausted.
- Scaling path: Use a global semaphore and a non-overlapping scheduling loop, expose queue depth/in-flight/lease-age metrics, and test cancellation during shutdown.

### Illustration review gallery

- Current capacity: All revisions for one novel are loaded in one response and each asset triggers several additional queries.
- Limit: Query count and response size grow linearly with asset history; there is no pagination parameter in `build_gallery`.
- Scaling path: Add stable cursor pagination, batch associations, and an explicit maximum page size while preserving owner/novel scoping.

### Local in-process heavy AI work

- Current capacity: Embedding, generation, analysis, and export paths share application processes or local companion services configured in `backend/app/config.py` and `agent-service/src/config.ts`.
- Limit: CPU/memory-heavy work competes with API latency, and FastAPI `BackgroundTasks` does not provide distributed backpressure.
- Scaling path: Separate durable workers by workload class, persist queue state in PostgreSQL or a dedicated broker, and define per-owner budgets and global concurrency limits before multi-instance deployment.

## Dependencies at Risk

### `chromadb==1.5.9`

- Risk: A known advisory is explicitly ignored in CI because no compatible fixed version is currently adopted.
- Impact: Exposure depends on whether untrusted clients can reach Chroma and which vulnerable path is exercised.
- Migration plan: Track upstream, test client/server compatibility with the pinned image, upgrade both sides together, remove the waiver, and rerun `pip-audit` plus retrieval integration tests.

### Next.js canary line

- Risk: Production frontend behavior depends on a prerelease framework and matching prerelease ESLint config.
- Impact: Build, routing, server rendering, or development behavior can regress outside a stable patch contract.
- Migration plan: Establish a stable-version branch, run `npm ci`, typecheck, lint, unit coverage, production build, and desktop/mobile Playwright suites, then update both Next and eslint-config-next atomically.

### Unlocked Python runtime graph

- Risk: Open-ended minimum versions in `backend/requirements.txt` allow resolution drift.
- Impact: CI, developer machines, and deployment images can run different dependency graphs; a transitive release can break runtime without a repository diff.
- Migration plan: Adopt a generated constraints/lock file with hashes, automate scheduled refresh PRs, and retain `pip-audit` against the resolved environment.

## Missing Critical Features

### Production qualification evidence

- Problem: The release gate remains blocked because the self-hosted provider benchmark has 0/3 scheduled green observations.
- Files: `.planning/STATE.md`, `.planning/phases/22-ci-nightly-gap-closure/22-G2-PLAN.md`, `.github/workflows/ci.yml`, `IMPLEMENTATION-STATUS.md`
- Blocks: Production-quality claims, baseline promotion, and a trustworthy assessment of live provider behavior. Local deterministic and contract tests do not replace this evidence.

### Controlled administrator provisioning

- Problem: No authenticated bootstrap ceremony protects first-superuser creation.
- Files: `backend/app/api/auth.py`, `backend/app/models/user.py`
- Blocks: Safe unattended deployment of an empty database to a public network.

### Authentication abuse controls

- Problem: Login and registration have no throttling, lockout, or audit-oriented abuse policy.
- Files: `backend/app/api/auth.py`, `backend/app/core/security.py`, `backend/app/main.py`
- Blocks: Defensible public exposure without relying entirely on external infrastructure that is not expressed as an application contract.

## Test Coverage Gaps

### High — Real coverage enforcement

- What's not tested: Actual repository coverage reports against the policy thresholds, critical globs, and changed-line minimum.
- Files: `.quality/coverage-policy.yml`, `.github/workflows/ci.yml`, `backend/tests/test_test_policy.py`, `frontend/vitest.config.ts`
- Risk: Coverage regressions can merge while the “coverage policy” contract tests stay green.
- Priority: High

### High — Bootstrap and login abuse paths

- What's not tested: Rejection of unauthorized initial-admin claims, bootstrap closure, request throttling, and lockout/recovery semantics.
- Files: `backend/app/api/auth.py`, `backend/tests/adversarial/test_agent_tools_adversarial.py`
- Risk: Privilege capture or credential-stuffing resistance is left to deployment timing and unspecified edge behavior.
- Priority: High

### Medium — Agent-service coverage threshold

- What's not tested: Line/branch coverage is not collected or gated for the agent runtime, despite its authority-sensitive registry, policy, MCP, SSE, and poller code.
- Files: `agent-service/vitest.config.ts`, `agent-service/package.json`, `.quality/coverage-policy.yml`, `agent-service/tests/`
- Risk: New untested branches in tool policy or materialization transport can merge as the suite grows.
- Priority: Medium

### Medium — Poller non-overlap and shutdown behavior

- What's not tested: A slow execution crossing multiple intervals, global concurrency enforcement, and stop behavior while a run is in flight.
- Files: `agent-service/src/poller.ts`, `agent-service/tests/poller.test.ts`
- Risk: Concurrency oversubscription and incomplete shutdown can appear only under realistic model latency.
- Priority: Medium

### Medium — Vertex/Gemini adapter and portable configuration

- What's not tested: Token acquisition subprocess failures, proxy behavior, schema conversion edge cases, configured SDK path absence, and provider error normalization.
- Files: `backend/app/services/vertex_gemini.py`, `backend/app/config.py`, `backend/tests/`
- Risk: The default chat provider can fail only in deployment, outside the otherwise broad automated suite.
- Priority: Medium

### Medium — Query-count and pagination regressions

- What's not tested: Maximum query count and bounded response size for illustration galleries with growing history.
- Files: `backend/app/services/illustrations/review.py`, `backend/tests/integration/illustrations/test_review.py`
- Risk: Functional tests pass while production latency degrades linearly.
- Priority: Medium

### Low — Legacy compatibility route

- What's not tested: Whether `/api/fanfiction` should be removed, redirected, or mapped to the delivered derivative domain after v1.4.
- Files: `backend/app/api/fanfiction.py`, `backend/app/main.py`, `backend/app/api/derivative_projects.py`
- Risk: Clients receive a permanently stale 501 contract despite the replacement feature being available.
- Priority: Low

---

*Concerns audit: 2026-08-07*
