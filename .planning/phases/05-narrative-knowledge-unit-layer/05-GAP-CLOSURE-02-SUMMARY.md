---
phase: 05-narrative-knowledge-unit-layer
plan: GAP-CLOSURE-02
subsystem: narrative-release-integrity
tags: [hmac, promotion-envelope, chroma, rollback, restore]
requires:
  - phase: 05-narrative-knowledge-unit-layer
    provides: candidate-bound evaluation, promotion journals, and rollback checkpoints
provides:
  - canonical versioned promotion evidence envelope
  - environment-only production signing trust anchor
  - mandatory direct-Chroma rollback and restore checkpoint validation
affects: [phase-05-verification, narrative-promotion, narrative-recovery]
tech-stack:
  added: []
  patterns: [signed aggregate release evidence, fail-closed projection recovery]
key-files:
  created: [.planning/phases/05-narrative-knowledge-unit-layer/05-GAP-CLOSURE-02-SUMMARY.md]
  modified: [backend/app/services/knowledge_units/promotion.py, backend/app/services/knowledge_units/rollback.py, backend/app/services/knowledge_units/incremental.py, backend/scripts/promote_narrative_unit_index.py, backend/scripts/refresh_narrative_units.py, backend/scripts/rollback_narrative_unit_index.py]
key-decisions:
  - "promotion-evidence.v2 signs candidate lineage, complete domain reports, direct-Chroma reconcile, approver identity/time, and before/after checkpoints as one canonical envelope."
  - "Production promote/refresh obtain NARRATIVE_EVAL_SIGNING_SECRET only from the environment; CLI key override is forbidden."
  - "Rollback and restore require a checkpoint probe, and production paths validate PostgreSQL build truth against actual Chroma item metadata before changing pointer or watermark state."
patterns-established:
  - "Release authorization is revalidated from the persisted signed envelope at commit time."
  - "Projection recovery validates the target collection before mutating authoritative PostgreSQL state."
requirements-completed: [REQ-NU-06, REQ-NU-07]
duration: 45min
completed: 2026-07-12
---

# Phase 05 Gap Closure 02 Summary

**Tamper-evident promotion authorization and mandatory direct-Chroma recovery gates close the two authoritative revision-loop blockers.**

## Performance

- **Duration:** 45 min
- **Completed:** 2026-07-12T06:31:00+08:00
- **Tasks:** 2
- **Files modified:** 11 including planning state and this summary

## Accomplishments

- Added canonical `promotion-evidence.v2`, signed with HMAC-SHA256 and verified with `hmac.compare_digest` through the existing `verify_run` path at both prepare/idempotent replay and commit.
- Bound candidate/build/checksum/collection/source lineage, complete signed domain evaluation reports and summaries, direct-Chroma reconcile, approver identity, approval timestamp, and before/after checkpoints into one immutable envelope.
- Removed `--evidence-secret` from production promote/refresh CLIs. Missing `NARRATIVE_EVAL_SIGNING_SECRET` exits non-zero; caller-selected CLI secrets are rejected.
- Made rollback/restore probes mandatory. Production rollback CLI and `complete_refresh` failure handling construct a real vector-store checkpoint probe and fail closed on missing collection, wrong build, or wrong manifest before pointer/watermark mutation.

## Task Commits

1. **Seal promotion evidence envelope and CLI trust anchor** — `6990a78`
2. **Enforce direct-Chroma rollback/restore checkpoints** — `56ac02f`

## Files Created/Modified

- `backend/app/services/knowledge_units/promotion.py` — builds, persists, and revalidates the versioned aggregate evidence envelope.
- `backend/app/services/knowledge_units/rollback.py` — requires strict collection probes and checks Chroma metadata against PostgreSQL build truth.
- `backend/app/services/knowledge_units/incremental.py` — passes the signing secret at commit and the real store gateway on failure rollback.
- `backend/scripts/promote_narrative_unit_index.py` — environment-only signing secret for prepare and commit.
- `backend/scripts/refresh_narrative_units.py` — removes caller-controlled secret input.
- `backend/scripts/rollback_narrative_unit_index.py` — wires the production Chroma gateway.
- `backend/tests/test_knowledge_unit_promotion.py` — covers forged reconcile, approver, candidate, and domain-run substitutions.
- `backend/tests/test_knowledge_unit_rollback.py` — covers missing collection, wrong manifest, and rollback-then-restore through the production gateway implementation.
- `backend/tests/test_knowledge_unit_cli.py` — covers rejected secret override and missing environment secret.

## Verification

- Targeted promotion/rollback/incremental/CLI: `35 passed`.
- Phase 05 plus hybrid regression: `103 passed`.
- Backend regression: `353 passed, 12 deselected` with `-m "not e2e"`.
- Ruff over Phase 05 services, CLIs, and tests: `All checks passed`.
- `python -m compileall -q app scripts tests`: passed.
- Alembic: one head `e5b8c20d4a73`; offline `upgrade head --sql` generated 41,418 bytes successfully.
- Live Chroma: heartbeat and temporary create/add/get/delete metadata round-trip passed; temporary collection was deleted.
- Live PostgreSQL `upgrade/current/check`: environment-blocked because `127.0.0.1:5432` refused the connection. No online pass is claimed.

## Deviations from Plan

None. Work was restricted to the two blockers in the authoritative second-round verifier notice. Cross-process refresh resume, multi-pointer `--active`, 05-02 CLI parity, and `05-VERIFICATION.md` were intentionally untouched.

## Known Stubs

None found in files created or modified by this closure.

## Threat Flags

| Flag | File | Description |
|---|---|---|
| threat_flag: release-authorization | `backend/app/services/knowledge_units/promotion.py` | A single signed envelope now authorizes exact promotion evidence and lineage. |
| threat_flag: projection-recovery | `backend/app/services/knowledge_units/rollback.py` | Rollback/restore now reads external Chroma state before authoritative pointer changes. |

## Residual Risks

- Online PostgreSQL migration and transaction smoke remain unverified until the local PostgreSQL service is available.
- The live Chroma smoke proves service CRUD and metadata round-trip; it does not perform a production-dataset cutover.
- `05-VERIFICATION.md` remains owned by the next independent verifier and was not modified.

## Self-Check: PASSED

- All listed implementation/test files exist.
- Commits `6990a78` and `56ac02f` exist in git history.
- No unrelated files were staged or committed.

---
*Phase: 05-narrative-knowledge-unit-layer*
*Completed: 2026-07-12*
