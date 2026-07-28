---
phase: 12-read-only-asset-audit-and-eligibility
verified: 2026-07-15
status: passed
score: "4/4 requirements verified; 10/10 plan truths verified"
---

# Phase 12 Verification — Read-only Asset Audit and Eligibility

**Goal:** 在任何模型调用或上层写入前，以只读方式证明单本小说现有 hierarchy 与可选分析资产是否可被 v0.8 精确复用，并给出可机器处理的阻断原因和最小重建范围。

**Final verdict:** `passed`

Phase 12 goal is achieved. The implementation produces deterministic owner-scoped eligibility reports over the required Phase 07 hierarchy and optional timeline/relationship/clue assets, blocks invalid required hierarchy before later provider work, exposes read-only administrator API and CLI entry points, and has no provider, repair, promotion or pointer-write capability.

## Evidence Reviewed

- `12-01-PLAN.md`, `12-02-PLAN.md`, `12-03-PLAN.md`
- `12-01-SUMMARY.md`, `12-02-SUMMARY.md`, `12-03-SUMMARY.md`
- `12-CONTEXT.md`, `12-RESEARCH.md`, `.planning/REQUIREMENTS.md`
- `backend/app/services/narrative_memory/{audit_contracts,audit_sources,audit,audit_pg}.py`
- `backend/app/api/asset_audit.py`, `backend/scripts/run_asset_audit.py`, router registration in `backend/app/main.py`
- Phase 12 unit/PostgreSQL integration tests and Phase 11 unavailable-source regressions
- Gap-closure commit `d6f1f93` and current working-tree test assertion that invalid optional clue state does not block hierarchy-only eligibility

## Independent Commands

```text
cd backend
.\.venv\Scripts\python.exe -m pytest \
  tests/unit/narrative_memory \
  tests/integration/narrative_memory \
  tests/integration/clues/test_source_protocols.py -q
# 34 passed, 3 existing pytest-timeout configuration warnings

.\.venv\Scripts\ruff.exe check \
  app/services/narrative_memory \
  tests/unit/narrative_memory \
  tests/integration/narrative_memory
# All checks passed
```

The warnings are pre-existing environment/configuration warnings for unavailable `pytest-timeout`; no Phase 12 test was skipped or failed.

## Requirement Verification

| Requirement | Status | Evidence |
|---|---|---|
| V08-AUDIT-01 — operator can generate a read-only per-asset/version report | ✓ VERIFIED | Strict canonical `EligibilityReport`; superuser GET API and fixed CLI use the same SELECT-only service and emit equivalent reports. |
| V08-AUDIT-02 — exactly four explicit statuses | ✓ VERIFIED | Closed enums and deterministic evaluator produce exactly `reusable_exact`, `rebuild_required`, `blocked`, or `optional_unavailable`; duplicate/malformed/scope inputs fail closed. |
| V08-AUDIT-03 — invalid Phase 07 hierarchy blocks before provider calls | ✓ VERIFIED | Active build must be immutable, non-candidate and valid status; snapshot, manifest, tree, offsets, hashes, coverage and foreign-build rows are checked. Every invalid required case keeps the derived guard false. |
| V08-AUDIT-04 — no model, repair or active-pointer writes | ✓ VERIFIED | Code is SELECT-only; forbidden-capability scans pass; fresh observers around real HTTP API and independent CLI exact/blocked executions show authority and pointers unchanged. |

**Score:** 4/4 requirements verified.

## Plan Must-have Verification

| Truth | Status | Evidence |
|---|---|---|
| Every asset gets one closed status and stable reason codes | ✓ | Strict Pydantic contracts, unique asset-kind enforcement and canonical serialization tests. |
| Required hierarchy permits providers only when exact | ✓ | `provider_calls_allowed` is derived from required results; caller overrides are rejected. |
| Optional unavailable differs from healthy empty | ✓ | Real fact counts for timeline, relationship and clue; explicit unavailable and zero-fact cases. |
| No provider/writer/repair/promotion/dispatch capability | ✓ | Static scan plus direct source inspection. |
| Active hierarchy inventory is lossless | ✓ | All rows for build ID load before scope filtering; foreign-novel rows produce `NOVEL_SCOPE_MISMATCH`. |
| Owner/build/snapshot/manifest/tree/offset/hash/coverage proved before exact | ✓ | PostgreSQL corruption tests cover mutable build, foreign rows, missing pointer, normalized multi-span mismatch and per-chapter rebuild range. |
| Optional domains follow real version authority | ✓ | Owner/novel/status, snapshot/build/checksum, pointer manifest and actual fact counts are validated. |
| Authorized operator can reproduce report through API and CLI | ✓ | Superuser authorization, non-admin rejection, canonical API/CLI equivalence and deterministic exit codes. |
| Audit leaves provider/data/pointers/revisions/journals unchanged | ✓ | Fresh-session snapshots wrap real HTTP API and subprocess CLI in exact and blocked cases. |
| Blocked hierarchy cannot cross pre-provider guard | ✓ | Blocked/rebuild cases return guard false and CLI exit 2; optional clue anomalies remain non-blocking when hierarchy is exact. |

**Score:** 10/10 plan truths verified.

## Gap-closure Evidence

### Required hierarchy lifecycle and foreign rows

- Requires `immutable=true`, `is_candidate=false`, status `built|committed`.
- Loads every hierarchy row for the build before target-novel filtering.
- Foreign-scope row produces a blocking scope reason.
- Tests: `test_mutable_active_build_never_allows_provider`, `test_foreign_scope_node_is_not_hidden_by_target_filter`.

### Optional version authority and facts

- Timeline/relationship/clue re-check owner, novel, status, source snapshot, hierarchy build and hierarchy checksum.
- Timeline and clue validate pointer manifest identity.
- Actual domain fact counts distinguish true empty from non-empty assets; relationship count is checked against the completed run.
- Clue active pointer target is now strictly `validated`; `candidate` and `superseded` become `optional_unavailable` with `OPTIONAL_LINEAGE_MISMATCH`.
- `test_clue_requires_validated_pointer_target_and_counts_real_facts` covers validated empty/non-empty plus both invalid states and proves optional failure leaves an exact hierarchy's provider guard true.

### Independent no-side-effect authority

- `test_fresh_observer_wraps_real_api_and_cli_for_exact_and_blocked_cases` commits fixture authority, uses fresh database sessions, invokes the real HTTP route, launches the CLI as a subprocess, and compares before/after build/node content, pointers/revisions and relevant journal/run counts.
- Exact case returns API guard true and CLI exit 0; mutable-build case returns guard false and CLI exit 2; both leave authority byte-equivalent within the observed contract.

## Real-data Audit Evidence

Retained from `12-03-SUMMARY.md`:

- Novel 91 completed a read-only audit and safely returned hierarchy `rebuild_required`, CLI exit 2.
- Novel 104 inspected 9,413 hierarchy rows, found `content_hash_mismatch`, returned rebuild range 0–419 and CLI exit 2.
- No existing novel was repaired, reanalyzed or promoted.

## Scope Confirmation

- No Chapter State, Story Arc/Volume or Global Story Model schema/table was added; those remain Phase 13.
- No model call, budget, checkpoint, cache or builder was added; those remain Phase 14.
- No production active pointer or existing timeline/relationship/clue/Reader Chat consumer was changed.
- No new product UI or production dependency was introduced.

## Final Conclusion

**PASSED.** Phase 12 provides a reproducible read-only gate whose exact classifications are backed by current PostgreSQL authority, whose optional-source failures remain explicit and non-blocking, and whose invalid required hierarchy cannot authorize later provider calls. All previously reported gaps are closed with executable PostgreSQL tests.
