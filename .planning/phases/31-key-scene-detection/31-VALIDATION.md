# Phase 31: Key Scene Detection - Validation

## Nyquist strategy

### Fixtures

- `scene-action`: explicit turn, multiple cast, location/time evidence.
- `scene-quiet`: low action but high emotional/character salience.
- `scene-ambiguous`: visually weak or conflicting evidence; must remain candidate/review.
- `scene-duplicate`: overlapping adjacent ranges and repeated motif.
- `scene-spoiler`: future chapter beyond cutoff; must be absent from visible results.

### Commands

|层|检查|命令|
|---|---|---|
|unit|boundary parsing, score breakdown, deterministic order, no-signal handling|`cd backend; pytest tests/unit/key_scenes -q`|
|integration|source hash/cutoff/owner scope and frozen set manifest|`cd backend; pytest tests/integration/key_scenes -q`|
|frontend|candidate reason/score/diversity display and reject/confirm action|`cd frontend; npm test -- key-scenes`|
|browser|desktop + 390px review, source jump, spoiler-safe list|`cd frontend; npm run test:e2e -- key-scenes --project=chromium-desktop --project=chromium-mobile-390`|

### Manual UAT

1. Open a frozen set and inspect why each scene ranked.
2. Confirm action and quiet scenes both survive the diversity quota.
3. Reject one candidate; refresh and verify it remains in history but leaves the approved set.
4. Change cutoff; verify candidate metadata and thumbnails from future chapters are not returned.

### Gate

Fail if range/hash mismatch, unsupported coordinate, score instability, spoiler leak, or duplicate approval occurs. This validation does not alter Phase 22's 0/3 status.
