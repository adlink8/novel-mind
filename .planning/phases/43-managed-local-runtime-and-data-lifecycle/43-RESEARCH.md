# Phase 43 Research: Managed Local Runtime and Data Lifecycle

**Researched:** 2026-08-09
**Scope:** planning evidence only

## Repository Truth

- Development startup is currently distributed across Makefile/PowerShell/batch/Compose entrypoints, fixed ports and environment files; the desktop runtime must centralize lifecycle without rewriting service internals.
- FastAPI, Agent Service and persistence/vector components already have distinct health and migration concerns. Port-open alone is not sufficient readiness evidence.
- Several current startup/config files are modified in the working tree (`backend/app/config.py`, startup/tunnel scripts, `frontend/next.config.mjs`); execution plans must read and reconcile them rather than overwrite them.

## Deep Module Design

`DesktopRuntime` should hide orchestration complexity behind:

- `ensureReady(): Promise<RuntimeSnapshot>`
- `status(): Promise<RuntimeSnapshot>`
- `restart(target?: RuntimeComponent): Promise<RuntimeSnapshot>`
- `shutdown(): Promise<ShutdownReport>`

The snapshot should expose bounded typed state (`stopped`, `starting`, `migrating`, `ready`, `degraded`, `failed`, `stopping`), component health, endpoint handles and redacted failure codes. Process/PID/executable internals stay inside adapters.

## Adapter and Process Graph

- `DevelopmentProcessAdapter` may invoke current local dev entrypoints and Compose for developer convenience.
- `PackagedProcessAdapter` launches only bundled executables from immutable resources and writes only to app-data paths.
- Dependency graph: persistence/vector readiness before backend migration/readiness; Agent Service after backend contract; Next after runtime bootstrap is available; renderer window may show a startup/recovery surface before all services are ready.
- Use explicit spawn events, health probes, timeouts, retry budgets and owned-process identity. On Windows, shutdown needs process-tree handling rather than assuming parent exit reaps children.

## Data Lifecycle

- Define versioned roots beneath `%APPDATA%/NovelMind`: `data/`, `logs/`, `backups/`, `runtime/`, `secrets/` and migration metadata.
- First-run creates directories atomically and records schema/runtime versions.
- Upgrade makes a recoverable backup or snapshot before migration; failure preserves old data and a diagnostic state and must not mark ready.
- Installer resources are read-only inputs. Runtime downloads or writes into installation directories are prohibited.

## Validation Architecture

| Layer | Proof | Blocking condition |
|---|---|---|
| Unit/state | Transition-table tests for every runtime state and retry budget | Impossible/ambiguous transition or empty-success fallback |
| Adapter contract | Run both adapters against the same contract suite | Semantic drift between dev and packaged modes |
| Integration | Start graph, migrate, query health, restart one component, shutdown tree | Ordering/readiness/orphan failure |
| Fault injection | Kill dependency, occupy port, corrupt migration fixture, deny writable path | Failure not typed/recoverable or data partially committed |
| Data | Upgrade/rollback fixture with hashes and backup metadata | User data loss or writes outside app-data root |

## Official Primary Sources

- https://www.electronjs.org/docs/latest/tutorial/process-model
- https://www.electronjs.org/docs/latest/api/utility-process

## RESEARCH COMPLETE
