# Phase 42 Research: Secure Desktop Shell

**Researched:** 2026-08-09
**Scope:** planning evidence only

## Repository Truth

- The current renderer is already the complete product UI. Electron should host it and add a narrow desktop boundary, not duplicate routes/components.
- No Electron dependency or preload/main-process package exists today, so the desktop package can be isolated under `desktop/` without coupling domain code to Electron.
- Existing browser tests and frontend unit tests provide route/workflow parity seams; Electron tests must add shell/security negatives rather than replace browser coverage.

## Security Contract

- Electron's current security guidance requires current Electron, no Node integration for web content, context isolation, process sandboxing, restrictive CSP, controlled permissions/navigation/windows, IPC sender validation and avoidance of `file://` when a custom protocol can be used.
- `DesktopBridge` should expose one typed method per allowed capability. Electron explicitly warns that exposing `ipcRenderer.send` or another generic IPC primitive defeats argument filtering.
- Main process owns BrowserWindow and lifecycle. Preload is a translation/validation boundary; renderer remains ordinary web code with no direct Node/Electron imports.
- IPC validation needs both schema checks and authorization checks against the expected `webContents`/frame/origin. Unknown channel, malformed payload, wrong sender or wrong lifecycle state must reject deterministically.

## Recommended Structure

- `desktop/src/main/index.ts` - app and BrowserWindow lifecycle only.
- `desktop/src/main/window-policy.ts` - origin, navigation, window-open, permissions and download policy.
- `desktop/src/preload/index.ts` - `contextBridge.exposeInMainWorld('novelMindDesktop', ...)`.
- `desktop/src/shared/bridge-contract.ts` - serializable request/response types and capability names.
- `desktop/src/main/ipc/*` - one handler per capability with sender/payload validation.
- `desktop/tests/security/*` and `desktop/tests/route-parity/*` - positive/negative shell tests.

## Failure Modes

- A permissive CSP or `webSecurity: false` can turn renderer XSS into desktop compromise.
- Loading arbitrary remote content into the privileged product window expands trust scope.
- Generic IPC, shell/openExternal input, unvalidated navigation or untrusted sender checks are release blockers.
- A preload API that returns secrets or filesystem paths can bypass renderer sandboxing even when flags are correct.

## Validation Architecture

| Layer | Proof | Blocking condition |
|---|---|---|
| Config | Assert BrowserWindow security flags and Electron fuses/policies | Any production window is unsandboxed or Node-enabled |
| Contract | Type/schema tests for every bridge method | Generic IPC or unserializable/unbounded payload |
| Negative | Attempt `require`, filesystem/shell, unknown channel, bad sender, popup and external navigation | Any attempt succeeds |
| Parity | Electron Playwright visits all 13 routes and critical existing workflows | Shell changes product behavior or routes |
| CSP | Inspect response/header policy and blocked-origin behavior | Broad wildcard or unexpected executable origin |

## Official Primary Sources

- https://www.electronjs.org/docs/latest/tutorial/security
- https://www.electronjs.org/docs/latest/tutorial/context-isolation
- https://www.electronjs.org/docs/latest/tutorial/sandbox/
- https://www.electronjs.org/docs/latest/tutorial/process-model

## RESEARCH COMPLETE
