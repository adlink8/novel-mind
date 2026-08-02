# Phase 22 Context — CI recovery and gate enforcement

**Gathered:** 2026-07-27  
**Status:** NEAR-COMPLETE; implementation is merged, but three-night observation and the latest PR #23 Browser smoke are not green.

## Boundary

Validate CI and branch-protection behavior using read-only CI evidence. Do not bypass required checks, push, merge, or alter remote protection settings.

## Decisions

- `ci-gate` must aggregate producer results and remain required.
- Browser smoke and nightly observations are independent evidence; a single failed producer keeps the phase partial.
- No live-provider benchmark or promotion job is enabled by this phase.
