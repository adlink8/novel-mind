---
phase: 37
slug: constrained-derivative-generation
status: passed
verified_at: 2026-08-04
source_commit: b8594e3
---

# Phase 37 — Verification

> Independent evidence that Phase 37 (Constrained Generation) implementation satisfies
> its plans. Every claim below was executed by an independent test sub-agent on 2026-08-04
> against master.

## Execution Context

- Execution override: user-authorized 2026-08-04 (Phase 22 3/3 gate skipped; recorded in
  `.planning/STATE.md` + `config.json` `gate_overrides.phase_35_39_execution`).
- Baseline: master (37-01..37-05 merged; alembic single head `20260802_derivative_override01`).
- All 5 plans of Phase 37 (01, 02, 03, 04, 05) completed with SUMMARYs.

## Verification Results

| Plan | What was verified | Command | Result |
|---|---|---|---|
| 37-01 context package | auditable package freezing cutoff state/evidence/unresolved clues/world rules/intent, migration 20260801_derivative_context01 | `test_context_package.py` + boundaries + integration | 22 + 13 + 11 + 10 passed |
| 37-02 generation | budgeted candidate runner, sealed-package input, idempotency, lineage, migration 20260802_derivative_generation01 | `test_candidate_runner.py` + `test_derivative_generation_job.py` | 35 + 11 passed |
| 37-03 consistency gates | contradiction gates, frozen qualification fixtures, BranchSuggestion disabled-by-default | `test_gates.py` + `test_derivative_consistency.py` | 51 + 24 passed |
| 37-04 divergence override | explicit CanonDelta override, PublishedDerivativeRevision DTO, no-writeback, migration 20260802_derivative_override01 | `test_derivative_overrides.py` + `test_override_no_writeback.py` | 9 + 12 passed |
| 37-05 skill | continue-derivative-story Skill, dual-approval boundary (divergence + publish), DraftArtifact/ContinuityReport | `test_phase_37_skill.py` + skill vitest | 24 + 52 passed |

## Aggregate Evidence (independent test sub-agent, 2026-08-04)

| Check | Result |
|---|---|
| agent-service vitest | **927 passed / 23 files** |
| agent-service tsc --noEmit | **0 errors** |
| backend tests/unit (full, incl. derivative_generation 108) | **1171 passed** |
| backend tests/integration (derivative 3 files) | **30 passed** |
| backend tests/adversarial | **451 passed** |
| backend tests/integration/agent_runtime | **263 passed** (incl. phase_37_skill 24) |
| backend tests/ci | **37 passed** |
| backend total | **2059 passed** |
| frontend vitest | **404 passed / 46 files** |
| app import | OK |
| alembic | single head `20260802_derivative_override01` |

## Known Non-Blocking Items

- One stale test assertion found during full verification
  (`test_migration.py::test_heads_show_agent_runtime` asserted `head.startswith("20260801_")`
  but Phase 37 head is `20260802_derivative_override01`). Fixed by removing the fixed-prefix
  assertion (keeps single-head + chain-contains checks). Verified 5p after fix.
- Frontend Playwright e2e (37-04 `derivative-generation.spec.ts`, 15 tests) route-mocked
  but not runnable here (Next canary dev server fails to compile). Vitest + backend contract
  are the gate.
- `test_openapi_contract.py` / `test_tmp_diag.py` hang under pytest (subprocess → litellm/
  tiktoken download). Pre-existing environment limitation.
- agent_runtime suite needs `-o timeout=600` (module fixture schema reset exceeds the 30s
  default on this environment).
- Live provider-turn UAT not executed (no provider key). Phase 22 remains 0/3 blocked.

## Conclusion

Phase 37 implementation is verified against its plans. Verdict: **passed**.
Phase 38 (Derivative Visual Consistency) may proceed per its entry gate (Phase 22 3/3 +
passed Phase 37 verification artifact — the latter now exists).
