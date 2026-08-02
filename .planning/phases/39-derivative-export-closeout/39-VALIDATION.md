# Phase 39 Validation — Nyquist Gate

## Fixture matrix

Two owners/forks, two revisions, ordered chapters, one approved and one rejected asset, valid/invalid citation hashes, missing asset, stale revision, original-space ID, and a deterministic expected Markdown/EPUB manifest.

## Automated tests (planned)

```text
cd backend; pytest tests/unit/derivative_export tests/integration/test_derivative_export.py -q
cd backend; pytest tests/adversarial/test_derivative_export_isolation.py -q
cd frontend; npm run test:e2e:desktop -- derivative-export.spec.ts
cd frontend; npm run test:e2e:mobile -- derivative-export.spec.ts
```

Map: `REQ-CRE-07` → round-trip/content parity; `REQ-FORK-05` → asset/citation/version/security; `REQ-SHIP-02` → independent three-dimensional audit contract. All files are Wave 0 gaps; commands not run in research.

## Manual UAT

Create/edit/review a derivative, export Markdown and EPUB, download/reopen, compare chapter order/content/asset hashes/citations/manifest, attempt cross-owner and Original export, inspect blocked errors, and archive the three-dimensional report. If no EPUB validator is available, label interoperability unverified rather than green.

## Failure policy

Any parity mismatch, missing provenance, unauthorized export, Original mutation or falsely green audit is BLOCKED. Phase 22's 0/3 remains visible and independent.
