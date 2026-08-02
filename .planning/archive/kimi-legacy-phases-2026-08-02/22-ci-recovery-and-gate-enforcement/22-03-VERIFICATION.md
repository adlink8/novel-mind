---
phase: 22-ci-recovery-and-gate-enforcement
plan: 03
status: partial
verified: 2026-07-27
---

# Plan 22-03 Verification

| Check | Result | Evidence |
|---|---|---|
| Required branch check is present | PASS | Read-only GitHub branch protection response: `ci-gate`, `enforce_admins=true` |
| Failure root cause is recorded | PASS | Run `30225927304` artifact: register 201, login 401; Passlib/bcrypt compatibility warning |
| Local auth remediation | PASS | Direct `bcrypt` implementation; `pytest tests/test_security.py` = 11 passed |
| Original desktop Browser auth/error path | PASS | Isolated backend on 8011; Playwright core/error tests = 5 passed |
| Latest remote Browser smoke green | BLOCKED | Remote run `30225927304` remains failed; no remote write was authorized |
| Three independent nightly runs green | PENDING | Current observation is day 1/3; date gate not reached |

## Test, Fix, and Confirm

- Test: reproduced the CI artifact locally and inspected trace/network evidence.
- Fix: removed the Passlib bcrypt compatibility layer and used direct bcrypt APIs.
- Confirm: targeted backend tests, lint/compile checks, and original desktop Playwright path pass;
  remote Browser smoke and nightly evidence remain outstanding.
