# Phase 44: Desktop Transport, Credentials and Offline Behavior - Context

**Gathered:** 2026-08-09
**Status:** Ready for planning
**Source:** User-confirmed v1.5 desktop direction

<domain>
## Phase Boundary

Connect the sandboxed renderer to the managed local runtime with secure startup configuration, local authentication and honest online/offline behavior. This phase does not redefine backend domain logic or package the final installer.

</domain>

<decisions>
## Implementation Decisions

### Endpoint and auth bootstrap

- **D-44-01:** Actual local endpoints and local auth material are created/injected at runtime; no production desktop path assumes fixed ports or compile-time `NEXT_PUBLIC_*` service URLs.
- **D-44-02:** The renderer consumes typed bootstrap/capability data and cannot discover arbitrary services, read environment variables or hold gateway/provider secrets in web storage.
- **D-44-03:** Local credentials and provider keys use Electron `safeStorage`/OS-backed protection where available, with explicit unavailable/decryption-failed states and redacted logs.
- **D-44-04:** Local service authentication fails closed on missing, invalid or expired material and supports bounded re-bootstrap without silently disabling auth.

### Streaming and connectivity

- **D-44-05:** Existing SSE semantics remain authoritative for Agent and long-running local operations, including cancellation, reconnect and terminal error preservation.
- **D-44-06:** Provider-independent reading, editing and local-data workflows start without internet.
- **D-44-07:** Provider-dependent actions show explicit unavailable/blocked/misconfigured states; offline failure cannot be converted into a fabricated successful result.
- **D-44-08:** Electron transports capabilities and lifecycle state only; backend services remain the domain/factual authority.

### the agent's Discretion

- Define the typed bootstrap payload and token rotation/expiry mechanics.
- Select the renderer-side endpoint resolver seam and migration path from current fixed URLs.
- Choose network reachability signals that avoid treating a single probe as universal internet truth.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

- `.planning/phases/42-secure-desktop-shell/42-CONTEXT.md` - secure bridge constraints.
- `.planning/phases/43-managed-local-runtime-and-data-lifecycle/43-CONTEXT.md` - readiness and dynamic endpoint contract.
- `.planning/ROADMAP.md` - Phase 44 success criteria.
- `frontend/src/lib/api/client.ts` - current API client seam, if present at execution time.
- `frontend/src/lib/sse.ts` - current SSE seam, if present at execution time.
- `frontend/next.config.mjs` - current fixed rewrite behavior; execution must reconcile the pre-existing dirty working-tree change before editing.
- `backend/app/config.py` - current service/provider configuration; execution must preserve unrelated pre-existing edits.
- `https://www.electronjs.org/docs/latest/api/safe-storage` - OS-backed credential protection API.
- `https://nextjs.org/docs/app/guides/environment-variables` - build-time/runtime environment behavior.

</canonical_refs>

<specifics>
## Specific Ideas

- Bootstrap can be represented as a one-session capability object with explicit expiry and service readiness.
- Add negative tests for stale token, wrong sender, wrong endpoint, offline provider call and interrupted SSE terminal state.
- Centralize URL selection so route code does not learn desktop-specific port logic.

</specifics>

<deferred>
## Deferred Ideas

- Final installer and clean-VM qualification are Phase 45.
- Account sync, remote secrets vault and multi-device session sharing are outside v1.5.

</deferred>

---

*Phase: 44-desktop-transport-credentials-and-offline-behavior*
*Context gathered: 2026-08-09*
