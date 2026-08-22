# Phase 41 Research: Electron Architecture and Packaging Proof

**Researched:** 2026-08-09
**Scope:** planning evidence only

## Repository Truth

- NovelMind is currently a three-runtime modular monolith: Next/React renderer, FastAPI backend, and Node Agent Service, with PostgreSQL/pgvector plus Chroma supplied by development infrastructure.
- `frontend/package.json` uses Next `16.3.0-canary.6` and React `19.2.7`; there are 13 page routes and extensive `next/link`/`next/navigation` use, so replacing Next with Vite would create avoidable migration risk.
- `frontend/next.config.mjs` currently rewrites `/api` to `127.0.0.1:8010` and `/agent` to `127.0.0.1:3100`. It is already modified in the working tree and must be reconciled before execution edits it.
- Node Agent Service requires Node `>=22.19`; packaging must prove the executable actually used at runtime rather than assume Electron's embedded Node can launch every service unchanged.

## Recommended Architecture Proof

1. Add a disposable `desktop/` proof package with Electron main as the owner of a local-loopback Next standalone process.
2. Configure `output: 'standalone'` only for the proof and explicitly copy `public` and `.next/static` into the standalone tree; Next documents that these assets are not copied automatically.
3. Bind all local services to loopback and allocate ports at runtime. Record process executable, arguments, environment, writable directories, readiness probe, log sink and shutdown owner in a machine-readable topology manifest.
4. Prove each bundled dependency independently on a clean Windows fixture: Next/Node, FastAPI/Python environment, Agent Service, PostgreSQL/pgvector and vector storage. Docker may remain a development fixture but is not valid packaged evidence.
5. Write `41-DECISION.md` with explicit GO/NO-GO criteria. A missing route, missing asset, user-runtime prerequisite, orphaned process or unproven redistribution constraint is NO-GO.

## Packaging Risks to Resolve

- Next standalone includes a minimal server and selected dependencies, but not public/static assets by default.
- Python native wheels, PostgreSQL binaries/extensions and Chroma's runtime/storage model are the highest-risk bundled components; the proof should not commit to a final installer until these run from an unpacked application resource tree with mutable data elsewhere.
- Electron `utilityProcess` is appropriate for a Node helper and provides Chromium-managed child-process semantics, but it is not a universal supervisor for Python/PostgreSQL; `DesktopRuntime` still needs a process adapter abstraction.
- Current canary Next behavior may drift. Pinning and a documented compatibility matrix are required before release qualification.

## Expected Planning Seams

- `desktop/package.json`, `desktop/src/proof/main.ts`, `desktop/src/proof/topology.ts`
- `desktop/scripts/copy-next-standalone.*`, `desktop/tests/route-parity.spec.ts`
- `.planning/phases/41-electron-architecture-and-packaging-proof/41-DECISION.md`
- Future execution may modify `frontend/next.config.mjs`; it must preserve the user's pre-existing change.

## Validation Architecture

| Layer | Proof | Blocking condition |
|---|---|---|
| Static | Electron/Next build config and topology manifest validation | Missing executable, asset copy, readiness or shutdown owner |
| Integration | Start proof, request/render all 13 routes, assert static assets and client navigation | Any route/asset/navigation failure |
| Runtime | Start/stop each bundled dependency from proof layout; inspect process tree and logs | User-installed runtime/Docker needed or orphan remains |
| Clean fixture | Run proof on clean supported Windows VM | Hidden prerequisite or write outside approved data path |
| Decision | Generate deterministic evidence summary | Any required evidence absent yields NO-GO |

## Official Primary Sources

- https://nextjs.org/docs/app/api-reference/config/next-config-js/output
- https://nextjs.org/docs/app/guides/self-hosting
- https://www.electronjs.org/docs/latest/tutorial/process-model
- https://www.electronjs.org/docs/latest/api/utility-process

## RESEARCH COMPLETE
