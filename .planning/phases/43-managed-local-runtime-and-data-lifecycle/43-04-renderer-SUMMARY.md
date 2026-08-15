---
phase: 43-managed-local-runtime-and-data-lifecycle
plan: "04"
subsystem: desktop-runtime-recovery-renderer
tags: [desktop, frontend, runtime, recovery, gate, no-empty-success, allowlist]
requires:
  - "43-04 desktop side (desktop/src/shared/runtime-status.ts RuntimeRecoveryState + recoveryActionIdsFor/isActionAllowed; desktop/src/runtime/recovery.ts RuntimeRecovery executor)"
provides:
  - "frontend/src/components/desktop/RuntimeGate — product routes render only when the desktop runtime is ready; browser mode degrades to plain children (no gate, no error)"
  - "frontend/src/components/desktop/RuntimeRecoveryPanel — honest lifecycle/recovery UI with defense-in-depth allowlist checks (T-43-04-02)"
  - "frontend/src/lib/desktop/runtime-recovery.ts — renderer-side recovery source seam (browser null / shell-status-backed default / injectable for tests and the future managed-runtime channel)"
affects:
  - "desktop/src/main/index.ts — future wiring point: bridge channel pushing RuntimeRecoveryState (43-04 desktop SUMMARY notes this is the future main-process wiring)"
  - "RuntimeGate.source prop — consumers may inject a full RuntimeRecoveryState source once the bridge recovery channel lands"
tech-stack:
  added: ["No new npm dependencies"]
  patterns:
    - "Renderer consumes the shared pure status contract (runtime-status.ts) at runtime; defense-in-depth re-checks isActionAllowed before rendering any action button"
    - "Inject data-source seam (RuntimeRecoverySource) mirroring the desktop RecoveryDataCapabilities seam — the component stays presentation-only"
key-files:
  created:
    - frontend/src/components/desktop/RuntimeGate.tsx
    - frontend/src/components/desktop/RuntimeRecoveryPanel.tsx
    - frontend/src/lib/desktop/runtime-recovery.ts
    - frontend/src/components/desktop/__tests__/RuntimeGate.test.tsx (15 tests)
  modified:
    - frontend/src/app/layout.tsx (RuntimeGate wraps product routes inside AppShell)
key-decisions:
  - "RuntimeGate renders plain children in browser mode (source null) — zero gate UI, zero errors; degrades identically to the rest of the desktop capability surface (capabilities.ts)."
  - "Non-ready states render the recovery panel; only ready renders product children (D-43-09 — failed/degraded never presents as empty domain success)."
  - "The default desktop source maps today's shell bridge status to an honest minimal state: ready only when the shell reports ready, no fabricated failure, and recoveryActions always empty because today's bridge cannot execute them (T-43-04-02)."
  - "The full starting/migrating/degraded/failed states and allowlisted actions are driven through the injectable RuntimeRecoverySource; the bridge recovery channel itself remains a future main-process wiring point (43-04 desktop SUMMARY)."
requirements-completed: [REQ-DESK-03, REQ-DESK-05, REQ-DESK-07]
metrics:
  duration_minutes: 55
  completed_at: "2026-08-10"
---

# Phase 43 Plan 04 (renderer): Runtime Gate + Recovery Panel Wiring — Summary

Wired the desktop runtime recovery contract into the renderer: `RuntimeGate` gates product routes
behind an honest `RuntimeRecoveryState` (children render only when ready — never an empty-success
domain state, D-43-09), `RuntimeRecoveryPanel` renders the bounded allowlisted recovery actions
(defense-in-depth re-check with `isActionAllowed`, T-43-04-02), and browser mode degrades to plain
children with no gate and no errors.

## Implemented

### `frontend/src/lib/desktop/runtime-recovery.ts` — renderer recovery source seam

- `RuntimeRecoverySource` interface: `getStatus()` (pull), `subscribe()` (push) and `request(actionId)`
  (bounded recovery action routed to the desktop authority, which re-validates before executing).
- `desktopRuntimeRecoverySource()` returns **null in browser mode** (no `window.novelMindDesktop`
  bridge) so the gate degrades to plain children — mirroring `capabilities.ts` degradation.
- Default shell source maps today's shell bridge status to an honest minimal `RuntimeRecoveryState`:
  `ready` only when the shell reports ready, `recoveryActions` always `[]` (today's bridge cannot
  execute lifecycle actions). `restart` maps to the existing `requestRuntimeRestart` capability; the
  other allowlist actions return a bounded not-wired error until the main-process recovery channel
  lands.
- Tests may inject a full source to drive starting / migrating / degraded / failed states and the
  allowlisted action set.

### `frontend/src/components/desktop/RuntimeRecoveryPanel.tsx` — bounded recovery UI

- Renders the lifecycle state (stopped / starting / migrating / stopping / ready / degraded / failed),
  the failed component, the stable redacted `errorCode` + `errorMessage`, and exactly the
  allowlisted action buttons.
- **Defense in depth (T-43-04-02):** every action is re-checked with `isActionAllowed(state, id,
  backupAvailable)` from the shared contract before its button is rendered — a stale or hostile state
  carrying an out-of-allowlist action never surfaces that button.
- `restoreBackup` renders as a destructive variant and only when `backupAvailable` allows it.

### `frontend/src/components/desktop/RuntimeGate.tsx` — product-route gate

- Subscribes to the recovery source (injectable `source` prop; default `desktopRuntimeRecoverySource()`).
- **Ready → children.** Every other state → recovery panel. Domain children are never rendered in a
  non-ready state (D-43-09).
- **Browser mode → plain children**, no gate UI, no errors.
- Channel failure (source rejects) → pass through children rather than fabricate a failure or block
  the app on a dead channel.
- While the first status is resolving, product content is held behind a loading indicator (no
  fabricated state).
- Actions: sets a busy state (disables all buttons), routes to `source.request`, surfaces a redacted
  action error on denial, re-pulls status after success.

### `frontend/src/app/layout.tsx` — wiring

- `RuntimeGate` wraps product route children inside `AppShell` (inside `AuthGate`): browser mode is a
  no-op wrapper; desktop mode gates 工作台/书架/分析/评测/创作/设置中心 behind runtime readiness.

## Verification (all executed)

1. `cd frontend && npx tsc --noEmit` — exit 0, no errors.
2. `cd frontend && npx vitest run` — **777 passed / 0 failed** (77 files). New: RuntimeGate.test.tsx
   **15 tests** (browser degradation, ready/migrating/degraded/failed states, restoreBackup gating,
   allowlist defense-in-depth, action routing, denial error, busy disable, push propagation, shell
   source ready vs not-ready). Existing desktop capabilities 8 tests + 754 pre-existing tests — no
   regression.
3. `cd desktop && npx tsc --noEmit` — exit 0 (shared contract compatibility; desktop untouched).
4. `git status --short` — changes are exactly `frontend/src/components/desktop/*`,
   `frontend/src/lib/desktop/runtime-recovery.ts`, `frontend/src/app/layout.tsx` and
   `.planning/phases/43-managed-local-runtime-and-data-lifecycle/43-04-renderer-SUMMARY.md`;
   pre-existing user modifications (`deploy/`, `frontend/next-env.d.ts`, `.gitignore`,
   `.planning/config.json`, etc.) untouched.

## Deviations from Plan

- The plan's `files_modified` listed the frontend gate/panel/test; the desktop main-process recovery
  channel (bridge push of `RuntimeRecoveryState` + `RuntimeRecovery.recover()` IPC) is NOT part of
  this wave — it is the documented future wiring point from the desktop 43-04 SUMMARY, and
  `desktop/src/main/index.ts` is protected by user-uncommitted working-tree changes. The renderer
  consumes the shared contract and the injectable source seam so the future channel plugs in without
  component changes.
- Component tests live at `frontend/src/components/desktop/__tests__/RuntimeGate.test.tsx` (vitest,
  jsdom) rather than the plan's `src/components/desktop/RuntimeGate.test.tsx` (README.md documents
  the `__tests__/` convention for component test files).
- `npx vitest run src/components/desktop/__tests__/RuntimeGate.test.tsx` was used instead of
  `npm test -- --run <path>` (the repo's `npm test` runs the full `vitest run`).

## Known Stubs

None. The default desktop source intentionally exposes `recoveryActions: []` on today's shell bridge
(no fabricated buttons); the full allowlisted action set is exercised via the injected source in
tests and becomes live when the main-process recovery channel is wired.

## Threat Flags

No new renderer-originated threat surface. The recovery action channel remains bounded main-side:
`RuntimeRecovery` denies unknown/out-of-state actions (`RECOVERY_DENIED`), and the renderer re-checks
the allowlist before rendering any action (T-43-04-02). No new network/file/process surface was added.

## Self-Check: PASSED

- 4 files created + 1 modified, all under `frontend/src/` plus `.planning/phases/43-*/` (verified by
  `git status --short`).
- `frontend tsc` clean; `frontend vitest run` 777/777; `desktop tsc` clean.
- No commits created (orchestrator-owned); no files outside the declared scope were touched.
