# 22-G3 Plan — Three Green Observations and Alert Lifecycle

## Goal

Close Phase 22 only after three consecutive scheduled runs are green and automated alerts
deduplicate and resolve by stable root cause.

## Steps

1. Replace run-ID fallback fingerprints with stable classification fingerprints.
2. Update the matching open alert on recurrence rather than opening another issue.
3. On a qualifying green scheduled observation, close alerts for resolved classifications
   and append the resolving run.
4. Record each scheduled observation with run ID, commit, artifact status and conclusion.
5. Reset the consecutive counter on failure, cancellation or missing required artifact.
6. **Test, Fix, and Confirm:** exercise create/dedup/resolve logic with fixtures and record
   three real scheduled green runs before marking Phase 22 complete.

## Must-Haves

- Three consecutive *scheduled* observations; no inferred or fabricated green.
- Stable fingerprint and idempotent issue updates.
- Auto-close includes a resolving run URL and preserves history.
- Counter resets fail-closed.

## Verification

- alert lifecycle unit/policy tests.
- GitHub issues show one issue per root cause, not one per run.
- `22-VALIDATION.md` contains three consecutive scheduled green run URLs.
