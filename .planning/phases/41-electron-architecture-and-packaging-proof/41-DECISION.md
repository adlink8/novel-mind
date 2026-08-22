# Phase 41 Decision — Bundled-Runtime Architecture GO/NO-GO

**Phase:** 41 Electron Architecture and Packaging Proof
**Plan:** 41-03 (wave 2, final decision)
**Decision date:** 2026-08-10
**Source commit:** `5167eee`
**Decision kind:** evidence-backed, fail-closed (D-41-06)

## Verdict: **NO-GO**

Phase 41 does **not** grant GO. Zero of the five runtime components satisfies the
bundled/no-Docker/no-user-runtime executable contract, so Phases 42–45 execution
readiness remains **blocked** until the exact failed prerequisites below are re-proven in
a revised plan.

This NO-GO is reproducible: run
`powershell -File desktop/proof/scripts/verify-bundled-runtime.ps1 -Manifest desktop/proof/runtime-manifest.json`
(exit 1) and
`cd desktop/proof && npm test -- --run tests/runtime-feasibility.test.ts`
(fail-closed assertions). Evidence hashes in `desktop/proof/runtime-manifest.json` bind
every verdict row; tampering any mandatory row deterministically flips the verdict to
NO-GO (T-41-03-01).

## Decision Table

Legend: `PASS` = proof evidence + hash validated this row; `FAIL` = row unproven or
contradicted by measured environment facts.

| # | Mandatory row | Required evidence | Verdict | Evidence / hash |
|---|---|---|---|---|
| 1 | Route count / render / assets (13 routes) | Frozen 13-route inventory matches discovered `frontend/src/app` routes; every route HTTP 200 + hydration + client nav; `public` and `_next/static` assets served from standalone tree | **PASS** | `desktop/proof/tests/route-inventory.json` `d8bac42f…`; `desktop/proof/tests/route-parity.spec.ts` `55fe1718…` (suite green in 41-02) |
| 2 | Loopback startup (dynamic ports) | Dynamic loopback port allocation; no fixed packaged port; readiness probe on 127.0.0.1 | **PASS** (contract) | `desktop/proof/src/topology.ts` `fdf2e54b…`; `desktop/proof/tests/topology.test.ts` `8fb92761…` (35 tests, fixed-port/non-loopback rejected) |
| 3a | Runtime component: **next** | Bundled/embedded Node executable in proof layout starts `server.js`; readiness + owned shutdown | **FAIL** | Startup resolves user `node` v24.13.0 (`C:\Program Files\nodejs\node.exe`); Electron dist absent → no embedded-node executable present. Readiness + taskkill shutdown proven but insufficient (manifest verdict FAIL). |
| 3b | Runtime component: **fastapi** | Bundled Python in proof layout runs uvicorn + deps + alembic | **FAIL** | No bundled Python; dev venv base interpreter is user-installed `C:\Users\li\.workbuddy\binaries\python\versions\3.13.12`; no readiness/shutdown evidence. |
| 3c | Runtime component: **agent_service** | Bundled/embedded Node executable in proof layout starts `start.mjs` | **FAIL** | No start evidence; Electron embedded Node v24.18.1 declared (satisfies `>=22.19.0`) but binary absent; pi-package redistribution licensing unresolved. |
| 3d | Runtime component: **postgres_pgvector** | Windows-native bundled PostgreSQL 16 + pgvector starts from proof layout | **FAIL** | Only Docker image evidence (`pgvector/pgvector:pg16`); no Windows-native distribution, no bundled start/readiness/shutdown. |
| 3e | Runtime component: **vector_store** | Bundled Chroma (server or embedded) starts from proof layout | **FAIL** | Only Docker image evidence (`chromadb/chroma:latest`); embedded-vs-server decision unresolved; no bundled start/readiness/shutdown. |
| 4 | no-Docker / no-user-runtime | No startup command token resolves a user-installed runtime or Docker under a stripped PATH | **FAIL** | Measured: `node`→user PATH; `python.exe`→user PATH (`Python314`); `docker`→user PATH (`C:\Program Files\Docker…`). Docker unreachable under stripped system PATH, but recorded commands still resolve user runtimes/Docker on the developer machine. |
| 5 | Writable paths | Mutable data/logs provably under `%APPDATA%/NovelMind`, never inside the resource root | **FAIL** | Topology contract declares app-data root and fails closed on install-path writes, but packaged writable-path operation is unproven (manifest `mutableDataPath.proven=false` for all 5). |
| 6 | Process-tree shutdown | Owned shutdown (taskkill /T /F) evidence for every component | **FAIL** | Proven only for `next` (`build-next-standalone.ps1` `7e29502c…`); no shutdown evidence for fastapi/agent_service/postgres/vector. |
| 7 | Unresolved licensing / redistribution | Redistribution note for every bundled runtime dependency | **FAIL** | torch/sentence-transformers/litellm (FastAPI), pgvector Windows extension, Chroma, `@earendil-works/pi-*` — no recorded redistribution rights in proof layout. |

**GO criterion:** GO is computed only when **every** mandatory row has passing evidence and
validated hash. Any absent or unknown row — including one component FAIL — yields
NO-GO. There is no partial/unknown row that can produce GO.

## Per-Component Verdicts (from `desktop/proof/runtime-manifest.json`)

| Component | Verdict | Key reason |
|---|---|---|
| `next` | **FAIL** | startup resolves user `node`; no embedded-node executable in proof layout |
| `fastapi` | **FAIL** | no bundled Python; user-installed base interpreter; no readiness/shutdown evidence |
| `agent_service` | **FAIL** | no executable start evidence; embedded Node absent; pi-package licensing unresolved |
| `postgres_pgvector` | **FAIL** | Docker-image-only; no Windows-native bundled distribution |
| `vector_store` | **FAIL** | Docker-image-only; embedded-vs-server choice unresolved |

## Failed Prerequisites (must be re-proven before Phase 42–45 may run)

1. **PREREQ-1 — Bundled Node executable:** prove `server.js` (next) and `start.mjs`
   (agent_service) start from a bundled/embedded Node binary inside the resource tree
   (e.g. Electron embedded Node v24.18.1 via `ELECTRON_RUN_AS_NODE=1`, or a packaged
   `node.exe`) with loopback readiness and owned shutdown. Today every recorded start
   resolves system `node`; the Electron dist binary is absent from the proof layout.
2. **PREREQ-2 — Bundled Python for FastAPI:** prove a relocatable/standalone Python
   runtime under the resource root runs uvicorn + backend dependencies + alembic
   migrations with writable data under `%APPDATA%/NovelMind`. The dev venv depends on
   user-installed Python 3.13.12.
3. **PREREQ-3 — PostgreSQL 16 + pgvector Windows-native distribution:** replace the
   Docker image with a Windows-native bundled distribution and prove
   init/start/readiness/shutdown on a loopback port. The pgvector Windows build and its
   redistribution are unproven.
4. **PREREQ-4 — Vector store bundling:** decide embedded mode (chromadb Python library
   inside the bundled FastAPI venv) or a bundled Chroma server, then prove
   start/readiness/shutdown. Only the Docker image exists today.
5. **PREREQ-5 — Licensing/redistribution:** record and bundle redistribution rights for
   torch/sentence-transformers/litellm, the pgvector extension distribution, Chroma, and
   `@earendil-works/pi-*`; each bundled runtime needs an explicit license/redistribution
   note.
6. **PREREQ-6 — Packaged writable-path operation:** exercise the `%APPDATA%/NovelMind`
   writable layout (logs, data, sqlite, pgdata, chroma) so the mutable-data row is
   proven, not merely declared by the topology contract.

## Allowed Replanning Boundary

A revised 41-03 plan may change **packaging internals**: the bundled runtime distribution
source per component (Python distribution, Windows PostgreSQL build, Node source, vector
embedded-vs-server), the resource-tree layout, and the per-component process adapter. It
**must not**:

- expand into a broad UI rewrite (D-41-07);
- weaken the no-Docker/no-user-runtime local-desktop contract (D-41-04);
- reduce the already-proven 13-route coverage (D-41-02);
- alter Phase 22's independent 0/3 blocked verdict (unchanged; this decision does not
  touch `.planning/phases/22-ci-nightly-gap-closure`).

## Evidence Provenance and Anti-Tamper (T-41-03-01 / T-41-03-02)

- Commands: `desktop/proof/scripts/verify-bundled-runtime.ps1` (stripped-PATH +
  Docker-unavailable simulation) and `npm test -- --run tests/runtime-feasibility.test.ts`.
- Versions: Next `16.3.0-canary.6` / React `19.2.7`; Electron `43.3.0` (embedded Node
  v24.18.1 declared, binary absent); agent-service engine `>=22.19.0`; Python 3.13.14 dev
  venv on user base 3.13.12; `pgvector/pgvector:pg16` and `chromadb/chroma:latest`
  (Docker only).
- Fixture: `desktop/proof/runtime-manifest.json` (hash
  `cb8fa6c95821c77dfa93f1aa6b17c75b04e1f19da373ae386bad9c6868344666`), evidence report
  `desktop/proof/logs/runtime-feasibility-evidence.json`, `frontend/.next/standalone`
  artifact (hash `8120c099…`).
- Timestamp: 2026-08-10T09:15:36Z (manifest generation).

## Verification Trail

- `powershell -File desktop/proof/scripts/verify-bundled-runtime.ps1 -Manifest desktop/proof/runtime-manifest.json` → exit 1, overall NO-GO, all five components FAIL. ✅
- `cd desktop/proof && npm run typecheck` → PASS. ✅
- `cd desktop/proof && npm test -- --run tests/runtime-feasibility.test.ts` → PASS (fail-closed verdict + tamper determinism). ✅
- Tamper test: a single falsified evidence hash deterministically flips to NO-GO; a single missing component deterministically flips to NO-GO. ✅
- `git diff --exit-code -- .planning/phases/22-ci-nightly-gap-closure` → clean (Phase 22 status unchanged). ✅
