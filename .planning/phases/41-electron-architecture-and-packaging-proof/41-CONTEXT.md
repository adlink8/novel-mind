# Phase 41: Electron Architecture and Packaging Proof - Context

**Gathered:** 2026-08-09
**Status:** Ready for planning
**Source:** User-confirmed v1.5 desktop direction

<domain>
## Phase Boundary

Prove or reject the Windows Electron architecture before committing to the full desktop migration. This phase may add a disposable proof harness and decision artifacts, but it must not rewrite the existing business UI or claim a release-ready installer.

</domain>

<decisions>
## Implementation Decisions

### Product and renderer

- **D-41-01:** The first desktop release is Windows-only and local-first.
- **D-41-02:** The existing Next/React application remains the renderer; all 13 current routes must be exercised without a parallel UI rewrite.
- **D-41-03:** Browser deployment remains a development/test harness, not the primary v1.5 distribution target.

### Runtime and packaging proof

- **D-41-04:** Application-managed bundled dependencies are the baseline: users must not install Docker, Node, Python, PostgreSQL or a vector service.
- **D-41-05:** The proof must cover Next standalone assets, FastAPI, Agent Service, PostgreSQL/vector persistence, dynamic ports, health, logs and process-tree shutdown as one topology.
- **D-41-06:** Phase 41 ends with an evidence-backed GO/NO-GO record. Any missing route, runtime prerequisite or bundled-dependency proof produces NO-GO and blocks Phases 42-45.
- **D-41-07:** A NO-GO may change packaging internals in a revised plan, but cannot silently expand into a broad UI rewrite or weaken the local-desktop contract.

### the agent's Discretion

- Select the smallest Electron proof harness and packaging-spike layout.
- Choose a candidate Windows packaging tool for the proof, provided the decision is reversible and compared against repository constraints.
- Choose representative clean-machine and process-observation commands for proof evidence.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Project authority

- `.planning/PROJECT.md` - v1.5 product contract and authority boundaries.
- `.planning/REQUIREMENTS.md` - REQ-DESK requirements.
- `.planning/ROADMAP.md` - Phase 41 goal, dependencies and fail-closed success criteria.
- `frontend/package.json` - current Next/React versions and scripts.
- `frontend/next.config.mjs` - current rewrites and build behavior; execution must reconcile the pre-existing dirty working-tree change before editing.
- `Makefile` - current three-service development topology and ports.
- `docker-compose.yml` - current database/vector development dependencies to replace in packaged operation.
- `agent-service/package.json` - Node runtime constraints and service scripts.

### External primary references

- `https://nextjs.org/docs/app/api-reference/config/next-config-js/output` - standalone output behavior and asset-copy caveat.
- `https://nextjs.org/docs/app/guides/self-hosting` - supported Next self-hosting model.
- `https://www.electronjs.org/docs/latest/tutorial/process-model` - Electron main/renderer process responsibilities.

</canonical_refs>

<specifics>
## Specific Ideas

- Treat route parity, copied `public`/`.next/static` assets and installed-target startup as separate proof assertions.
- Record every executable/runtime source, version, license/redistribution note, writable path and shutdown owner in a packaging matrix.
- Keep proof artifacts cheap to discard if the GO/NO-GO decision changes the packaging adapter.

</specifics>

<deferred>
## Deferred Ideas

- Secure production preload and complete shell hardening belong to Phase 42.
- Managed data migration/recovery belongs to Phase 43.
- macOS/Linux packaging, auto-update and production web hosting are outside v1.5.

</deferred>

---

*Phase: 41-electron-architecture-and-packaging-proof*
*Context gathered: 2026-08-09*
