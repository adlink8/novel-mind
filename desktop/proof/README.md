# NovelMind Desktop Proof (Phase 41)

**Status:** IMPLEMENTED — Plan 41-01 Tasks 1–3 complete. Dependencies installed and
locked (`package-lock.json`). Fail-closed topology contract in place.
**Disposability:** This is a **proof-only** harness. It must never accumulate product UI or
backend domain logic. If the Phase 41 GO/NO-GO decision changes the packaging adapter, the
whole `desktop/` tree is cheap to discard and recreate.

## What this proof must establish

1. An Electron main process owns a local-loopback topology of: Next.js standalone,
   FastAPI, Agent Service, PostgreSQL/pgvector and vector storage — as one process tree
   (D-41-04, D-41-05).
2. All ports are **dynamically allocated loopback ports**; fixed packaged ports are rejected.
3. Every runtime writes only under an immutable resource root (read-only install tree) and a
   writable per-user app-data root.
4. Every spawned process has a named executable, arguments, readiness probe, log sink and a
   single shutdown owner.
5. The first desktop release is **Windows-only** (D-41-01). Browser remains a dev/test
   harness, not the v1.5 distribution target (D-41-03).

## Electron / Windows / Node pattern

- **Electron `43.3.0`** embeds **Node v24.18.1**, which satisfies the `agent-service`
  `Node >=22.19` floor without bundling a separate Node runtime. The proof therefore runs
  every child on the Electron runtime tree (Windows-only proof, D-41-01).
- **Node >=22.19** is the package engine floor (`desktop/proof/package.json` `engines.node`).
  Local dev Node v24.13.0 satisfies it; Electron's embedded Node re-proves it on the clean
  Windows fixture during integration/runtime validation.
- **electron-builder `26.15.3`** is the Windows packaging candidate: `win-unpacked` + NSIS
  installer, immutable resources tree. Not yet exercised in this plan — packaging spikes in
  a later Phase 41 plan.

## Dependency set (APPROVED 2026-08-10) — installed and locked

All five candidates were approved by the user. `npm install` was run in `desktop/proof/`;
`package-lock.json` (lockfileVersion 3) and `node_modules/` were produced. Resolved
versions:

| Package | Range | Resolved (installed) | License |
|---|---|---|---|
| electron | `^43.3.0` | `43.3.0` | MIT |
| electron-builder | `^26.15.3` | `26.15.3` | MIT |
| typescript (dev) | `^5.9.3` | `5.9.3` | Apache-2.0 |
| vitest (dev) | `^4.1.10` | `4.1.10` | MIT |
| @types/node (dev) | `^22.19.0` | `22.20.1` | MIT |

`node_modules/` is git-ignored (repo root `.gitignore`). Only source, config, test and
lockfile are versioned.

## Implementation

- `src/topology.ts` — typed proof-only process topology:
  - Five known components: `next`, `fastapi`, `agent_service`, `postgres_pgvector`,
    `vector_store`. Anything else is rejected (allowlist, T-41-01-01).
  - Each `ComponentDescriptor` carries: id, processType (`renderer`/`child`), executable
    source (kind + path), args, loopback endpoint (port `0` = runtime allocation), `dependsOn`,
    readiness probe (tcp/http), immutable resource root, writable app-data root, log sink,
    single shutdown owner (`harness` or a named component).
  - `validateTopology(candidate, mode)` fails CLOSED before any process starts:
    - missing component / missing any field / duplicate or unknown component
    - fixed packaged ports (nonzero endpoint or probe port)
    - non-loopback binds (endpoint or probe host other than `127.0.0.1`)
    - writable install paths (log sink or app-data root inside the resource root)
    - unresolved dependencies / shutdown owners (reference not in the graph)
    - `mode: "contract"` collapses all contract violations to a single stable
      `CONTRACT-REJECTED` signal for deterministic fail-closed testing.
- `src/main.ts` — proof harness entry (`npm start` = `node src/main.ts`):
  - Builds the single five-component proof fixture, validates it, allocates loopback ports,
    computes a deterministic `dependsOn` startup order (cycle detection), prints the plan,
    and exits nonzero with a rejected-topology report when the graph is unsafe.
  - **No domain or database API.** No process is actually spawned by this proof harness;
    child-process spawning belongs to a later Phase 41 plan with the real bundled runtimes.
- `tests/topology.test.ts` — fail-closed contract tests (35 tests, all passing).

## Verification

Run from `desktop/proof/`:

```bash
npm run typecheck        # tsc --noEmit
npm test -- --run tests/topology.test.ts
```

| Check | Result |
|---|---|
| `npm run typecheck` | PASS (2/2 runs) |
| `npm test` (35 tests: 5 positive + 30 negative/edge) | PASS (2/2 runs, 35/35) |
| `node src/main.ts` (valid topology) | exit 0, prints startup order, no spawn |
| Fail-closed smoke (fixed port / external bind / install write / unknown component) | all rejected before spawn |

## Fail-closed contract tests (negative)

- Every missing component (all 5, plus empty graph)
- Missing fields: executable, args, endpoint, dependencies, probe, resource root,
  app-data root, log sink, shutdown owner
- Fixed packaged ports: endpoint port, probe port
- Non-loopback binds: endpoint `0.0.0.0`, probe `0.0.0.0`
- Writable install paths: log sink in resource root, app-data root in resource root,
  backslash path variant
- Unknown components, duplicate components, non-object descriptor
- Unresolved dependency, unknown dependency, unresolved owner, unknown owner
- Contract mode collapses to a single stable rejection; unknown components stay distinct

## Runtime prerequisite check

- Local Node: **v24.13.0** (`node --version`, 2026-08-10) → satisfies `>=22.19.0`.
- Electron 43.3.0 embeds **Node v24.18.1** → satisfies `>=22.19.0`, so the Agent Service
  can run on Electron's embedded Node; a separate bundled Node runtime is not required for
  the proof (still to be re-proven on the clean Windows fixture during integration/runtime
  validation).
- Next renderer: `frontend/package.json` pins `next@16.3.0-canary.6` + `react@19.2.7`.
  Canary behavior may drift — the compatibility matrix must pin and document versions
  before release qualification (41-RESEARCH.md).
- Backend: FastAPI (backend/) and PostgreSQL/pgvector + vector storage remain Docker-supplied
  in dev; the packaged proof must replace them with bundled runtimes — exactly what later
  Phase 41 plans must demonstrate.

## Install / lock discipline

- `npm install` was run ONLY after the dependency set received explicit user approval.
- The `desktop/` tree is new (untracked); only files under `desktop/proof/` were created.
- No other repository files were touched.
