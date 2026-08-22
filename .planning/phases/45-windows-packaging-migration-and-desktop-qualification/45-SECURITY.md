# Phase 45 — Packaged Security Negative Audit (Plan 45-04, Task 1)

**Wave:** 3 · **Plan:** 45-04 · **Type:** execute
**Date:** 2026-08-11
**Requirement:** REQ-DESK-02 / REQ-DESK-10 / D-45-08 / D-45-09
**Machine boundary:** same honest boundary as 45-UAT.md — this audit ran on the
developer workstation (`clean_vm=false`, isolated `NOVELMIND_USER_DATA`, bundled
renderer served through the **shipped packaged exe's** embedded Node). It is the
release-evidence re-run of the Phase 42/44 dev-mode suites **against the packaged
artifact**, not a claim about pristine-VM execution.

## Artifact Under Test (checksum-bound)

| Artifact | SHA-256 |
|---|---|
| `desktop/dist/win-unpacked/NovelMind.exe` | `09b11247…db197` |
| `desktop/dist/win-unpacked/resources/app.asar` | `a0bf12b6…d804` |
| bundled `resources/next-standalone/server.js` | `8120c099…3f8a` |

Source of truth: `desktop/dist/CHECKSUMS.SHA256` (45-01 reproducible build),
`desktop/tests/fixtures/qualification-manifest.json` (45-03), both re-verified by
the SBOM gate below.

## Electron Security Checklist — shipped-artifact verdict

| # | Checklist item (Electron security tutorial) | Boundary implemented (42-02) | Packaged negative test result |
|---|---|---|---|
| 1 | Do not enable Node.js integration for remote content | `nodeIntegration: false`, `nodeIntegrationInWorker: false` in every production window | **PASS** — live `getLastWebPreferences` on the packaged window: nodeIntegration=false, nodeIntegrationInWorker never true; `require`/`process`/`module`/`global`/`nodeRequire` all `undefined` in the renderer main world |
| 2 | Enable context isolation | `contextIsolation: true` | **PASS** — live webPreferences read-back; six declared bridge capabilities only (`novelMindDesktop`), no generic invoke surface |
| 3 | Enable process sandboxing | `sandbox: true` | **PASS** — live webPreferences read-back; untrusted-second-window tests run with sandbox+contextIsolation too |
| 4 | Do not use `enableRemoteModule` | module never imported; no remote module usage | **PASS** — static: `src/main` imports no `@electron/remote`; no remote surface exposed to renderer |
| 5 | Restrict permissions | `setPermissionRequestHandler`/`setPermissionCheckHandler` blanket deny | **PASS** — `Notification.requestPermission()` resolves `denied` in the packaged window |
| 6 | Web Security / `allowRunningInsecureContent` | `webSecurity: true`, `allowRunningInsecureContent: false` | **PASS** — live webPreferences read-back on the packaged window |
| 7 | CSP for your pages | Production CSP injected on the app-document response header (`default-src 'none'`, no broad wildcard, no `<meta>` relaxation, `frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'none'`) + `X-Content-Type-Options: nosniff` | **PASS** — header present on the packaged app document, no `*` directives, no relaxing `<meta>` CSP |
| 8 | Do not set `allowRunningInsecureContent` | `false` | **PASS** — see #6 |
| 9 | Navigations and window open | approved-loopback-only `will-navigate`/`will-frame-navigate`/`will-redirect`; popups denied; `<webview>` refused | **PASS** — attacker-origin navigation blocked (window stays on loopback); `window.open` returns null, window count stays 1; `webview` inert; `javascript:`/`file:` cannot move the window |
| 10 | Do not use `shell.openExternal` with untrusted content | renderer never supplies shell args; only the validated HTTPS `openExternalLink` capability | **PASS** — javascript:/file:/data:/custom-scheme:/http:/credential-URLs all rejected with the stable redacted `REJECTED` code |
| 11 | Disable webviewTag | never enabled; `will-attach-webview` preventDefault | **PASS** — `<webview>` is a plain unknown element, not `HTMLWebViewElement` |
| 12 | Verify webPreferences flags against your threat model | `SECURE_POSTURE` asserted end-to-end | **PASS** — see #1/#2/#3/#6 |

## Negative Injection Results (packaged)

| Negative | Payload / actor | Result |
|---|---|---|
| Malformed capability call | `openExternalLink(12345)` (non-string) | **PASS** — `INVALID_PAYLOAD`, capability logic never runs |
| Oversized capability call | `openExternalLink("x" × 5120)` (> 4 KiB cap) | **PASS** — `PAYLOAD_TOO_LARGE`, rejected before dispatch |
| Unknown channel | `bridge:noSuchChannel` | **PASS** — "No handler registered"; no capability logic invoked |
| Forged sender | untrusted second `BrowserWindow` (test-only raw-invoke preload) on every known channel incl. `getLocalAuthToken`/`openExternalLink` | **PASS** — stable redacted `SENDER_NOT_MAIN_WINDOW` on all |
| Wrong frame / origin | senderFrame null / non-main / attacker origin (unit) | **PASS** — `SENDER_FRAME_UNTRUSTED` (dev suite, 21/21) |
| Local-auth replay | no runtime session → `getLocalAuthToken` twice | **PASS** — `null` twice (no token minted); unknown target → `null` (fail closed) |
| Secret/log redaction | `getBootstrap`/`getRuntimeStatus` surfaces | **PASS** — only schema-declared redacted state strings; `sk-…`/`api_key`/`secret=`/`BEGIN …`/password fragments absent from serialized payload |
| CSP bypass attempt | `<meta http-equiv="Content-Security-Policy">` injection | **PASS** — no meta CSP; header enforcement only |
| External resource loading | every `request` in the packaged window | **PASS** — all requests resolve to loopback hosts only |
| Source-map / secret material in bundle | scan of packaged `resources/` | **PASS** — 0 `.map`/`.pem`/`.key`/`.p12`/`.pfx`/`.env` files |

## Component Provenance / SBOM (`desktop/scripts/generate-sbom.ps1`)

`powershell -File desktop/scripts/generate-sbom.ps1 -Verify` — **12/12 PASS, run
twice with no drift**:

- `runtime-manifest.json` is byte-identical to the hash recorded in 41-DECISION.md
  (`cb8fa6c9…`) — the 41 NO-GO evidence was **not** tampered (T-41-03-01).
- Packaged server.js, installer exe, NovelMind.exe and app.asar all match the
  qualification manifest + CHECKSUMS.SHA256.
- Staged inventory is self-consistent: 1440 files / 34,019,789 bytes re-hashed
  per-file, aggregate `c3839923…` reproducible across runs.
- Component inventory matches the staged pins: electron 43.3.0 / embedded-node
  v24.18.1 / next 16.3.0-canary.6 / react 19.2.7; **notBundled** boundary =
  fastapi, agent_service, postgres_pgvector, vector_store (41-DECISION PREREQ-2/3/4).
- Secret scan over packaged resources is empty; `unsigned=true` is recorded —
  **no artifact is described as publicly trusted or signed**.

Output: `desktop/dist/release-sbom.json`.

## Open Threats / External Gates (honest)

| Item | Status |
|---|---|
| Code signing certificate | **External publication gate (D-45-06)** — the artifact is unsigned; acquiring a certificate and reporting signing complete require explicit user authorization and are NOT done here |
| Bundled Python/FastAPI, PostgreSQL/pgvector, vector store | **Not packaged** — 41-DECISION NO-GO remains unchanged (PREREQ-2/3/4, post-45); the packaged app fails closed for every component except `next` |
| Pristine clean-VM security execution | **Blocking gap** — this audit ran on the developer workstation (`clean_vm=false`); missing clean-VM evidence must not be represented as passed (D-45-07/D-45-09) |
| Packaged main-process adapter wiring | Documented post-45 prerequisite — the audit launches the shipped exe with the bundled renderer via the `NOVELMIND_RENDERER_URL` seam, the identical mechanism the packaged adapter will use |

## Numbers

- Packaged release-security suite: **17/17 passed** (12 webPreferences/privilege/
  CSP/navigation + 3 IPC negatives + 2 local-auth/redaction + 2 external-loading).
- Dev-mode IPC/policy regression: **21/21 passed**; credential/local-auth units:
  **16/16 passed**. Total security surface: **54 passed / 0 failed**.
- `cd desktop && npm run typecheck`: **PASS**.
- SBOM `-Verify`: **12/12 PASS twice, no drift**.
