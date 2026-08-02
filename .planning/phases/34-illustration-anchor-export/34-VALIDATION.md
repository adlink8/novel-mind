# Phase 34: Illustration Anchors, Reader and Export - Validation

## Nyquist strategy

### Fixtures

- `anchor-valid`: exact range/hash, approved AssetRevision, caption/alt.
- `anchor-edited`: inserted/deleted text before and inside range; expected stale/repair.
- `anchor-missing`: approved metadata but missing binary; expected accessible placeholder and export report.
- `anchor-responsive`: long chapter, paged and scroll modes, 1280px and 390px.
- `export-golden`: text/source snapshot, two anchors, citations, one unavailable asset, expected manifest/HTML/EPUB inspection.

### Commands

|层|检查|命令|
|---|---|---|
|unit|offset conversion, hash validation, repair state, manifest ordering|`cd backend; pytest tests/unit/illustration_anchors tests/unit/export -q`|
|integration|owner scope, approved-only, missing asset, export parity|`cd backend; pytest tests/integration/illustration_anchors tests/integration/export -q`|
|frontend|figure/fallback/caption and progress-safe layout|`cd frontend; npm test -- illustration`|
|browser|desktop/mobile, long page, paged/scroll, keyboard/focus/no overlap|`cd frontend; npm run test:e2e -- illustration-anchors --project=chromium-desktop --project=chromium-mobile-390`|

### Manual UAT

1. Open a valid anchor from reader and verify source range/caption/asset.
2. Edit chapter content and reload: image is marked stale, not silently relocated.
3. Remove the binary: reader shows accessible placeholder and export includes explicit missing-asset record.
4. Export and inspect Markdown/HTML/EPUB: text, image revision, caption, citations, and source/version hashes match the manifest.
5. On 390px, verify no image/caption overlaps progress bar, input, navigation, or focus target.

### Gate

Fail on hash mismatch rendered as valid, approved-state bypass, inaccessible image, export divergence, or hidden missing asset. This phase cannot change Phase 22 qualification.
