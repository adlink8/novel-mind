# Phase 45: Windows Packaging, Migration and Desktop Qualification - Context

**Gathered:** 2026-08-09
**Status:** Ready for planning
**Source:** User-confirmed v1.5 desktop direction

<domain>
## Phase Boundary

Produce and qualify a Windows release candidate from the proven shell/runtime/transport architecture. This phase owns installation, upgrade/uninstall policy, clean-machine evidence and v1.5 closeout; it does not purchase credentials/certificates or expand platform scope.

</domain>

<decisions>
## Implementation Decisions

### Windows release behavior

- **D-45-01:** The installer must first-run on a clean supported Windows VM without Docker or user-installed Node, Python, PostgreSQL or vector-service runtime.
- **D-45-02:** The application is single-instance, opens no service console windows and owns clean shutdown of its entire managed process tree.
- **D-45-03:** Installed resources are immutable; mutable state remains under the versioned `%APPDATA%/NovelMind` layout.

### Upgrade, uninstall and recovery

- **D-45-04:** Compatible upgrades preserve user data, logs and backups, run versioned migrations and provide a documented reversible recovery path on failure.
- **D-45-05:** Uninstall removes application binaries but preserves user data by default unless the user explicitly selects a clearly labelled data-removal path.
- **D-45-06:** Code-signing certificate acquisition is an external publication gate. Planning may make the pipeline signing-ready, but must not purchase a certificate or report signing complete.

### Qualification

- **D-45-07:** Electron integration and clean-VM UAT cover first run, route/workflow parity, local runtime recovery, offline/provider-blocked behavior, upgrade and data preservation.
- **D-45-08:** Security qualification includes CSP/navigation/window controls, malformed/unknown capability calls, IPC sender validation, credential redaction and renderer privilege negatives.
- **D-45-09:** Release evidence remains fail-closed: missing clean-VM, migration, recovery, security or workflow evidence blocks v1.5 closeout.

### the agent's Discretion

- Choose the final Windows installer target and CI artifact layout from the Phase 41 decision.
- Define the supported Windows version matrix and clean-VM fixture details consistent with current dependencies.
- Choose checksum/SBOM tooling that fits the repository without purchasing external services.

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

- `.planning/phases/41-electron-architecture-and-packaging-proof/41-CONTEXT.md` - packaging GO/NO-GO and dependency matrix.
- `.planning/phases/42-secure-desktop-shell/42-CONTEXT.md` - shell security boundary.
- `.planning/phases/43-managed-local-runtime-and-data-lifecycle/43-CONTEXT.md` - process/data lifecycle.
- `.planning/phases/44-desktop-transport-credentials-and-offline-behavior/44-CONTEXT.md` - endpoint, credential and offline contracts.
- `.planning/ROADMAP.md` - Phase 45 release criteria and Phase 22 independence.
- `.github/workflows` - current CI evidence and Windows runner patterns.
- `playwright.config.ts` and frontend browser tests - existing browser qualification seams, if present at execution time.

</canonical_refs>

<specifics>
## Specific Ideas

- Separate build reproducibility evidence from clean-VM behavioral evidence.
- Include upgrade from the immediately previous desktop fixture and intentional migration-failure recovery.
- Record checksums, bundled component versions/licenses, test logs and redacted runtime diagnostics in the release evidence package.

</specifics>

<deferred>
## Deferred Ideas

- macOS/Linux releases, auto-update rollout, Store publication and production web hosting are outside v1.5.
- Purchasing/issuing a code-signing certificate requires separate user authorization.

</deferred>

---

*Phase: 45-windows-packaging-migration-and-desktop-qualification*
*Context gathered: 2026-08-09*
