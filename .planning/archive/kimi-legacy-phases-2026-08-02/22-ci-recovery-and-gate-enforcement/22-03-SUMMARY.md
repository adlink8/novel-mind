---
phase: 22-ci-recovery-and-gate-enforcement
plan: 03
status: partial
completed: 2026-07-27
---

# Plan 22-03 Summary

## Completed locally

- Read-only branch protection audit confirmed required `ci-gate` and `enforce_admins=true`.
- Downloaded and inspected the latest Browser smoke failure artifact from run `30225927304`.
- Isolated the CI auth symptom: registration returned 201, then bcrypt-backed login returned 401
  in two desktop tests; the failure was surfaced as a missing authenticated shell.
- Replaced the fragile Passlib bcrypt compatibility path with direct `bcrypt` hash/check calls,
  removed the unused Passlib runtime dependency, and added fail-closed hash verification coverage.
- Verified the original desktop authentication/error path against an isolated local backend.

## Not completed

- No remote push, rerun, merge, or branch-protection modification was authorized or performed.
- The latest remote Browser smoke result remains failed.
- Three independent nightly green runs are still pending (day 1/3).
- Therefore 22-03 remains partial and its roadmap checkbox stays unchecked.
