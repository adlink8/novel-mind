# Phase 22-03 Read-only Audit — 2026-07-27

## Confirmed

- `master` branch protection requires the `ci-gate` check.
- `enforce_admins=true`.
- PR #23 remains open and unmerged.
- Latest CI run `30225927304` has green static, unit/contract, integration, OpenAPI, and CodeQL
  producers.
- The same run has Browser smoke failure and `ci-gate` failure; live/nightly/promote jobs were
  skipped.

## Failure diagnosis and local remediation

- The downloaded Playwright artifact for run `30225927304` shows both failing desktop tests
  received `POST /api/auth/register = 201`, then `POST /api/auth/login = 401`; the login query
  found the user, but the Passlib bcrypt verification path returned false.
- The same CI log records Passlib probing the removed `bcrypt.__about__` attribute. This is the
  local root-cause candidate for the CI-only authentication failure, not a missing navigation
  selector.
- Local code now uses the maintained direct `bcrypt` API for hash/check and removes the unused
  Passlib dependency. Targeted security tests pass (`11 passed`), and the original desktop
  Playwright auth/error path passes (`5 passed`) against an isolated 8011 backend.
- This is not remote evidence: no push, rerun, merge, or branch-protection write was performed.
  The remote run remains failed until the local fix is incorporated by an authorized remote
  workflow and a new green Browser smoke result exists.

## Blocked criteria

- Browser smoke is not green.
- Three independent nightly runs have not been observed; current planning evidence remains day
  1/3 and the date gate has not been reached.

No rerun, push, merge, branch-protection change, or remote configuration write was performed.
