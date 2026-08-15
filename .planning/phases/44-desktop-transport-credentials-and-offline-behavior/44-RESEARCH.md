# Phase 44 Research: Desktop Transport, Credentials and Offline Behavior

**Researched:** 2026-08-09
**Scope:** planning evidence only

## Repository Truth

- Current Next rewrites and service clients assume fixed loopback ports. Desktop startup requires dynamically allocated endpoints and a single resolver seam.
- Next public environment values are inlined during build. Runtime desktop endpoints/credentials therefore cannot rely on mutable `NEXT_PUBLIC_*` values after packaging.
- Existing Agent operations use SSE. Desktop transport should preserve the backend/SSE terminal-state contract and avoid replacing it with Electron IPC business transport.
- `backend/app/config.py` and `frontend/next.config.mjs` have unrelated working-tree edits; future execution must reconcile them.

## Recommended Transport

- Main/runtime creates loopback endpoints and short-lived local auth material after readiness.
- Preload exposes a typed bootstrap/status capability, not raw secrets or arbitrary URLs. Renderer resolves logical services through one client-side `RuntimeEndpointResolver`.
- Backend and Agent Service continue to carry domain commands and SSE over authenticated loopback HTTP. IPC is limited to desktop lifecycle/capability needs.
- Tokens should be audience-bound, short-lived or session-scoped, redacted from logs and rejected outside the approved loopback/sender context.

## Credential Storage

- Electron `safeStorage` is main-process only and uses Windows DPAPI. Its protection is user-bound, not a defense against every process running as the same user; plans must avoid overstating the guarantee.
- Prefer the current asynchronous safeStorage API, handle unavailable/decryption/rotation outcomes, and never fall back to renderer `localStorage` or plaintext logs.
- Provider keys remain provider credentials; local gateway tokens and provider keys should have distinct storage, rotation and error states.

## Offline Authority

- Define a capability matrix: local reading/editing/library/data inspection is offline-capable; provider-backed generation/embedding/image operations are blocked/unavailable when provider config or connectivity is absent.
- Connectivity is not a single global boolean. Combine runtime readiness, provider configuration and request result into typed per-capability state.
- Preserve SSE `failed`, `cancelled` and timeout terminals. Reconnect must not duplicate materialization or synthesize success.

## Validation Architecture

| Layer | Proof | Blocking condition |
|---|---|---|
| Static | No fixed production ports or desktop secrets in `NEXT_PUBLIC_*`, bundle or logs | Any runtime secret/endpoint frozen into renderer bundle |
| Contract | Bootstrap schema, resolver and local-auth audience/expiry tests | Renderer can choose arbitrary endpoint or bypass auth |
| Storage | safeStorage available/unavailable/decrypt/rotation tests with redaction checks | Plaintext fallback or leaked secret |
| SSE | Reconnect, cancellation, duplicate prevention and terminal error tests | Fabricated success or duplicated side effect |
| Offline | Provider-independent workflows pass with network disabled; provider actions block honestly | Local workflow unnecessarily blocked or provider false-success |

## Official Primary Sources

- https://www.electronjs.org/docs/latest/api/safe-storage
- https://nextjs.org/docs/app/guides/self-hosting
- https://nextjs.org/docs/pages/guides/environment-variables

## RESEARCH COMPLETE
