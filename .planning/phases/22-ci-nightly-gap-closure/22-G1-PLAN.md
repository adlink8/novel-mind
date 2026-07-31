# 22-G1 Plan — Nightly Failure Classification and Reproduction

## Goal

Make every scheduled failure attributable to one stable class with a reproducible local or
workflow entry point.

## Steps

1. Add a stable classification envelope for upstream test failure, runner/provider
   dependency, benchmark policy failure, artifact validation and promotion failure.
2. Capture job result, report status, report signature/policy hash and relevant run URL
   without fulltext or secrets.
3. Add a repository test that maps representative producer results to one classification.
4. Reproduce the 2026-07-31 frontend failure under coverage with repeated targeted runs.
5. Document the exact commands and expected output in the gap validation artifact.
6. **Test, Fix, and Confirm:** run the classifier tests, targeted frontend test repeatedly,
   full frontend coverage once, and CI policy tests; fix any deterministic failure before
   closing G1.

## Must-Haves

- Stable reason codes; no free-text-only classification.
- `nightly=skipped` due to upstream failure is observable.
- No credentials, provider payloads or novel fulltext in artifacts/issues.
- A local reproduction command exists for every repository-controlled class.

## Verification

- `npm run test:coverage`
- repeated `npx vitest run src/app/analysis/relationships.test.tsx`
- `PYTHONPATH=. pytest tests/ci -q`
- inspection of the generated classification envelope.
