---
phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel
plan: 10
subsystem: timeline-release-authority
tags: [postgresql, cli, ci, sha256, subprocess, release-gate]

requires:
  - phase: 08-09
    provides: signed production qualification report and DB authority resolver
provides:
  - Self-observing CLI release mode with fixed argv command execution
  - SHA-256 attestations derived from exact captured stdout/stderr bytes
  - Independent PostgreSQL success, command-failure, and DB-mismatch coverage in CI
affects: [phase-08-reverification, timeline-release-gate, ci-integration]

tech-stack:
  added: []
  patterns: [code-owned command specifications, output-bound command attestations, independent database authority observation]

key-files:
  created: [.planning/phases/08-versioned-novel-analysis-orchestration-and-interactive-timel/08-10-SUMMARY.md]
  modified: [backend/scripts/run_timeline_qualification.py, tests/ci/test_timeline_release_gate.py, backend/tests/integration/timeline/test_real_qualification.py, .github/workflows/ci.yml]

key-decisions:
  - "The production release CLI accepts only a report path and owns both the fixed command list and PostgreSQL session factory."
  - "Command evidence is valid only when the verifier recomputes SHA-256 from internally retained combined output bytes."
  - "Machine-readable release evidence exposes command, exit code, and digest but not captured test/service output."

patterns-established:
  - "Release command boundary: fixed cwd/argv specs execute without a shell or caller-selected commands."
  - "Authority boundary: a fresh SQLAlchemy session factory re-reads run, version, pointer, audit, and evidence rows."

requirements-completed: [REQ-TIME-01, REQ-TIME-02, REQ-TIME-05, REQ-TIME-09]
duration: 7min
completed: 2026-07-13
---

# Phase 08 Plan 10: Executable Release Authority Summary

**The Phase 08 release gate now executes fixed commands, hashes their real combined output, and independently re-reads PostgreSQL authority before returning a machine-readable verdict.**

## Performance

- **Duration:** 7 min
- **Started:** 2026-07-13T06:43:42Z
- **Completed:** 2026-07-13T06:49:58Z
- **Tasks:** 2
- **Files modified:** 4 implementation/test files plus this summary and planning state

## Accomplishments

- Added `--verify-release --report PATH`, which imports `async_session_factory` internally, executes code-owned argv/cwd command specs without a shell, and calls `verify_release_evidence_from_db()`.
- Captures combined stdout/stderr bytes, records real exit codes, computes SHA-256 inside the collector, and recomputes the digest during verification; fabricated 64-character digests cannot qualify.
- Added real PostgreSQL tests proving a controlled production-worker report qualifies through a separate observer session, while a real exit code 9 and a persisted manifest mismatch both fail closed.
- Added the real qualification test file to the existing locked-service CI integration job after Alembic migration.

## Task Commits

1. **Task 1 RED: release gate contracts** - `d883a69`
2. **Task 1 GREEN: self-observing CLI release gate** - `f0fc0af`
3. **Task 2: PostgreSQL release authority and CI integration** - `abd85af`

## Decisions Made

- The CLI exposes no authority, digest, command-result JSON, or caller-selected command-list arguments.
- Raw captured output remains internal. The emitted verdict includes only command display text, exit code, and SHA-256 to avoid leaking test or service output.
- The low-level pure verifier remains injectable only for negative contracts. Copied report authority and fabricated digests appear solely in tests that assert `blocked_release`; no positive path uses them.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- Task 2's new integration tests passed on first execution because Task 1 had already implemented the shared release entrypoint they exercise. Task 1 retained a distinct failing RED commit followed by GREEN.
- Existing pytest warnings for unavailable `pytest-timeout` configuration remain pre-existing and out of scope.

## Known Stubs

None.

## Threat Flags

| Flag | File | Description |
|---|---|---|
| threat_flag: fixed-subprocess-release-gate | `backend/scripts/run_timeline_qualification.py` | Executes a code-owned fixed command set without shell interpolation and binds verdicts to captured output digests. |
| threat_flag: independent-db-authority | `backend/scripts/run_timeline_qualification.py` | Opens fresh PostgreSQL sessions to resolve report references before release qualification. |

## Verification

- `cd backend; .venv/Scripts/python.exe -m pytest tests/integration/timeline/test_real_qualification.py -x` — **5 passed** against PostgreSQL.
- `backend/.venv/Scripts/python.exe -m pytest tests/ci/test_timeline_release_gate.py tests/ci/test_workflow_security.py tests/ci/test_ci_gate.py -x` — **47 passed**.
- `backend/.venv/Scripts/python.exe backend/scripts/run_timeline_qualification.py --verify-release --report does-not-exist.json` — emitted `blocked_release`, `quality_comparable=false`, exit code **1**.
- Python compilation of all three changed Python files — **passed**.
- `.planning/.../08-VERIFICATION.md` — **unchanged**; independent verifier remains responsible for re-verification.

## User Setup Required

None.

## Next Phase Readiness

- Phase 08 gap closure plans are 10/10 complete and ready for independent re-verification.
- Residual risk is limited to CI/runtime environment availability for the full fixed command set; missing executables and non-zero commands fail closed with exit code/digest evidence.

## Self-Check: PASSED

- All seven allowed implementation/planning files exist.
- Commits `d883a69`, `f0fc0af`, and `abd85af` resolve in git history.
- Stub scan found no TODO/FIXME/placeholder patterns in plan-owned implementation and test files.
- `08-VERIFICATION.md` remains unchanged, and all unrelated user changes remain unstaged.

---
*Phase: 08-versioned-novel-analysis-orchestration-and-interactive-timel*
*Completed: 2026-07-13*
