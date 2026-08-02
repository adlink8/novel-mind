# Phase 33: Illustration Generation and Consistency - Research

**Researched:** 2026-08-01
**Domain:** Durable provider-neutral image candidate generation
**Confidence:** MEDIUM

## Summary

Issue #29 requires refactoring image generation into Asset Revision, injecting character references/visual state, supporting preview/regeneration/versioning, and evaluating consistency and source faithfulness. [CITED: https://github.com/adlink8/novel-mind/issues/29] The roadmap requires idempotent jobs, immutable lineage, explicit failures, budgets, human approval, and a provider-neutral API. [CITED: `.planning/ROADMAP.md` Phase 33]

The repository has no image-generation route or image provider business service in `master`; it does have a mature Reader Chat pattern for durable jobs, leases, retries, attempt records, manifests, budgets, hashes, and cost. [VERIFIED: codebase grep] The existing AI router/provider layer can be a call seam, but image payloads and asset revisions need a separate contract. [ASSUMED]

**Primary recommendation:** clone the control-plane shape of Reader Chat for an `IllustrationJob` and `AssetRevision`, use mocked providers for deterministic validation, and make human approval the only handoff to Phase 34. [ASSUMED]

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|---|---|---|---|
| Job/attempt/budget/asset lineage | Database / Storage | API / Backend | durable recovery and audit need transactional state. [VERIFIED: Reader Chat code] |
| Provider invocation | API / Backend | external provider | backend enforces scope, budget, and request hash. [VERIFIED: AI service analog] |
| Preview/approval/compare | Browser / Client | API / Backend | browser presents candidates; server records decisions. [ASSUMED] |
| Binary delivery | CDN / Static | Database / Storage | metadata belongs in DB; bytes need controlled storage. [ASSUMED] |

## User Constraints

- Honor `D-33-01` through `D-33-04` in `33-CONTEXT.md`.
- No generated asset silently becomes Canon or reader-visible published content; Phase 33
  ends at `proposal_ready`, while Phase 34 owns publish approval and deterministic publication.
- Phase 22 remains blocked/0/3. [CITED: `.planning/STATE.md`]

## Standard Stack

| Component | Version | Use | Provenance |
|---|---|---|---|
| FastAPI/Pydantic/SQLAlchemy | requirements ranges | API, strict contracts, durable state | [VERIFIED: `backend/requirements.txt`] |
| Existing Reader Chat job/budget pattern | current | lease/retry/cost/idempotency design | [VERIFIED: codebase grep] |
| Existing AI router/provider seam | current | configured provider selection and credentials | [VERIFIED: codebase grep] |
| PostgreSQL 16 | baseline | job/asset/review source of truth | [CITED: architecture data model] |

No image SDK is recommended until the provider is selected; package names discovered only from training are not verified. [ASSUMED]

## Architecture Patterns

- Idempotency key should include `owner_id`, `novel_id`, `scene_spec_hash`, `prompt_revision_hash`, `model_lineage`, and `config_hash`; enforce one nonterminal job per key. [ASSUMED based on Reader Chat unique nonterminal job]
- Persist `IllustrationAttempt` with request/response hashes, provider request ID, usage, cost, latency, status, error code, and cache source. [VERIFIED: Reader Chat analog]
- Store binary by content hash under an owner/novel-scoped path; metadata row remains authoritative for MIME, dimensions, rights, and approval. [ASSUMED]
- Consistency evaluation consumes frozen reference assets + generated revision and emits a versioned report; score cannot mutate Visual Bible. [ASSUMED]

## Don't Hand-Roll

| Problem | Don't Build | Use Instead |
|---|---|---|
| Durable retry/lease | in-memory task list | Reader Chat worker/job/attempt pattern |
| Cost accounting | approximate UI counter | budget reservation/settlement and price snapshot pattern |
| Provider credentials | new key store | existing encrypted AI model config and router |
| Image identity score | unversioned heuristic | fixture-backed evaluator with explicit unavailable/blocked state |

## Common Pitfalls

- **Duplicate provider calls:** retries without idempotency may charge twice and produce divergent candidates. [CITED: `.planning/ROADMAP.md` Phase 33; VERIFIED Reader Chat pattern]
- **Unknown provider outcome:** a timeout cannot be labeled failed if the provider may have created an asset; use `outcome_unknown` and reconcile by request ID/hash. [VERIFIED: Reader Chat attempt statuses]
- **Budget bypass:** reserve before call, settle after usage; do not trust client estimates. [VERIFIED: Reader Chat budget code]
- **Reference contamination:** a generated image can be visually plausible but unsupported; preserve SceneSpec/evidence lineage and separate consistency score. [CITED: `REQ-VIS-04`; ASSUMED]
- **Binary path traversal or public leakage:** validate path containment and owner access like upload storage, but do not expose raw filesystem paths. [VERIFIED: `novel_service.py` security pattern]

## Code Examples

```python
job_key = sha256_json({
    "owner_id": owner_id,
    "novel_id": novel_id,
    "scene_spec_hash": scene_spec_hash,
    "prompt_revision_hash": prompt_hash,
    "model_lineage": model_lineage,
    "config_hash": config_hash,
})
```

This mirrors existing canonical request/config hash use in Reader Chat. [VERIFIED: codebase grep]

## Validation Architecture

| Req | Behavior | Type | Command | File |
|---|---|---|---|---|
| REQ-VIS-04 | same idempotency key produces one job/one proposal-ready result | unit/integration | `pytest tests/integration/illustrations/test_idempotency.py -q` | Wave 0 |
| REQ-VIS-04 | budget reservation/settlement and retry limits | unit | `pytest tests/unit/illustrations/test_budget.py -q` | Wave 0 |
| REQ-VIS-04 | provider failure/timeout/unknown outcome are explicit | contract | `pytest tests/unit/illustrations/test_failures.py -q` | Wave 0 |
| REQ-VIS-04 | consistency report retains fixture/model lineage | fixture eval | `pytest tests/integration/illustrations/test_consistency.py -q` | Wave 0 |
| REQ-VIS-04 | human approval gates reader-visible status | browser/manual | `npm run test:e2e -- illustrations` | Wave 0 |

Existing validation layers are pytest, Vitest, Playwright, and optional live provider canary; live canary must be explicitly budgeted and is not required for deterministic contract tests. [VERIFIED: project test docs; ASSUMED canary policy]

### Wave 0 Gaps

- mock provider with deterministic bytes and injected timeout/unknown outcome;
- budget and price snapshot fixtures;
- reference/generation consistency fixture set;
- approval/rejection/compare browser fixture.

## Security Domain

V2/V3 auth/session, V4 owner/novel/asset access, V5 prompt/input/MIME validation, V6 existing cryptography/content hashes apply; provider URL/key checks reuse `url_security.py` and encrypted model config. [VERIFIED: codebase grep]

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|---|---|---|---|---|
| Python backend environment | jobs/budgets/storage | ✓ by repository setup | requirements-managed | mock provider fixtures |
| PostgreSQL 16 | durable job/asset state | project-supported | 16 baseline | integration test DB fixture |
| Node/npm + Playwright | candidate gallery/UAT | ✓ by manifest/scripts | manifest-managed | Vitest component tests |
| Image provider credentials/SDK | live generation | not confirmed and not required for contract work | — | deterministic mock provider; live canary requires explicit decision |

No provider call, credential inspection, package install, or implementation test was performed. [VERIFIED: manifests/codebase; ASSUMED runtime availability]

## Sources

- HIGH: Issue #29; requirements/roadmap/state; Reader Chat models/services; AI router/provider and upload security code.
- MEDIUM: architecture docs 03/08/10.

## Assumptions Log

| # | Claim | Risk |
|---|---|---|
| A1 | Reader Chat job/budget model is the correct control-plane analog. | Image provider semantics may need additional reconciliation states. |
| A2 | Content-hash filesystem/object storage is acceptable for initial bytes. | Deployment may require object storage from day one. |
| A3 | Consistency evaluator can be fixture-backed before a live model is selected. | Quality gate could be blocked until evaluator selection. |

## Open Questions (RESOLVED)

1. RESOLVED — Provider selection remains provider-neutral at the contract boundary via an adapter; deterministic mock adapters are the executable qualification path and any live provider requires explicit user authorization.
2. Budget and cost use the existing usage/budget ledger and its price snapshots; no parallel budget authority is introduced, and unknown usage/cost remains explicit.
3. RESOLVED — The local asset store is the current authority for asset bytes; retain an object-storage adapter seam for deployment configuration. AssetRevision metadata remains storage-independent and records immutable content hash, MIME, dimensions, provenance, and rights.

4. RESOLVED — Live canary is disabled by default; enabling it requires explicit authorization and an explicit hard budget, while deterministic mock-provider validation remains the default qualification path.
