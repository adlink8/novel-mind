# Phase 42: Secure Desktop Shell - Context

**Gathered:** 2026-08-09
**Status:** Ready for planning
**Source:** User-confirmed v1.5 desktop direction

<domain>
## Phase Boundary

Create the production-shaped Electron main/preload/renderer boundary and prove route/workflow parity. This phase owns shell security and desktop capabilities, not local service orchestration, credentials migration or installer qualification.

</domain>

<decisions>
## Implementation Decisions

### Shell ownership

- **D-42-01:** Electron main owns windows, lifecycle and privileged capabilities; the existing Next/React renderer remains UI-only.
- **D-42-02:** The renderer uses `contextIsolation: true`, `sandbox: true` and `nodeIntegration: false` in every production window.
- **D-42-03:** BrowserWindow loads only the approved local application origin and blocks unapproved navigation, redirects, popups, downloads and new windows by default.

### Bridge and IPC

- **D-42-04:** Preload exposes a typed, capability-specific `DesktopBridge`; it must never expose `ipcRenderer`, filesystem, shell, environment variables or a generic invoke/send primitive.
- **D-42-05:** Every IPC handler validates sender/webContents, channel, payload and lifecycle state, with deterministic rejection for unknown or malformed requests.
- **D-42-06:** Desktop-only UX is additive and minimal; existing business routes and verified workflows stay on the current React/Next surface.

### Security evidence

- **D-42-07:** CSP, navigation/window policy and IPC negative tests are release-blocking shell evidence, not optional hardening follow-up.

### the agent's Discretion

- Define the concrete capability names and TypeScript schemas for the minimal bridge.
- Select a custom protocol or loopback-origin loading mechanism consistent with the Phase 41 GO record.
- Choose test seams for BrowserWindow policy and preload contract tests.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

- `.planning/phases/41-electron-architecture-and-packaging-proof/41-CONTEXT.md` - upstream product and proof decisions.
- `.planning/ROADMAP.md` - Phase 42 goal and acceptance boundary.
- `frontend/src/app` - current route surface and renderer ownership.
- `frontend/src/components` - existing UI/workflow components to reuse.
- `https://www.electronjs.org/docs/latest/tutorial/security` - Electron security checklist.
- `https://www.electronjs.org/docs/latest/tutorial/context-isolation` - safe preload exposure pattern.
- `https://www.electronjs.org/docs/latest/tutorial/sandbox/` - renderer sandbox model.

</canonical_refs>

<specifics>
## Specific Ideas

- Keep `DesktopBridge` narrow enough to review as a security API.
- Include tests that actively attempt access to `require`, `process`, arbitrary IPC and external navigation.
- Treat route parity as a regression suite against the existing 13-route inventory.

</specifics>

<deferred>
## Deferred Ideas

- Service process lifecycle and app-data migrations are Phase 43.
- OS-protected credentials and offline/provider behavior are Phase 44.
- Installer, signing and clean-VM qualification are Phase 45.

</deferred>

---

*Phase: 42-secure-desktop-shell*
*Context gathered: 2026-08-09*
