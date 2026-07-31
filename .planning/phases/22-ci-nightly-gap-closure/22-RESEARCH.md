# Phase 22 Gap Closure Research

## Findings

1. Branch protection is correctly configured with only `ci-gate` required and
   `enforce_admins=true`.
2. The current Nightly job is bound to `[self-hosted, linux, ollama]`; GitHub job timeout
   starts only after runner acquisition, so a missing runner can queue until platform
   cancellation without producing the signed report.
3. `alert` only sees `failure` or `cancelled`. When an upstream required job fails,
   `nightly=skipped`, so the scheduled failure may create no alert.
4. When no artifact exists, the alert fingerprint is `nightly-fail:<run-id>`, defeating
   deduplication across the same root cause.
5. The latest unit failure is a timing-sensitive frontend expectation; the same suite
   passes locally (29 files, 248 tests), so it requires repeat verification rather than
   being labeled fixed by one pass.

## Authority Rules

- A provider outage or missing runner must never produce comparable quality metrics.
- `blocked_dependency` is an honest quality result, not a qualified result.
- CI execution health and model quality qualification are separate dimensions.
- Baseline promotion accepts only a signed, schema-valid, `passed|qualified` report.
- Alert identity derives from root-cause class/report signature, never run ID alone.
