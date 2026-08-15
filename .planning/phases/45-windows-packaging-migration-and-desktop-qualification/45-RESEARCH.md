# Phase 45 Research: Windows Packaging, Migration and Desktop Qualification

**Researched:** 2026-08-09
**Scope:** planning evidence only

## Repository Truth

- The repository has browser/unit/integration qualification seams but no Electron installer, desktop E2E harness, clean-VM fixture or signing pipeline.
- Phase 45 must consume the Phase 41 packaging decision and Phases 42-44 contracts; selecting packaging from scratch here would bypass the proof gate.
- Phase 22 remains independently blocked at 0/3 scheduled greens. Desktop qualification must report that fact without treating it as proof that v1.5 desktop behavior failed or passed.

## Release Candidate Shape

- Produce a deterministic Windows artifact, checksums, bundled-component/version manifest, license/SBOM report and redacted test evidence.
- Enforce single-instance behavior and suppress child-service console windows. Installer/uninstaller must not terminate or delete processes/data it does not own.
- Separate immutable installed resources from `%APPDATA%/NovelMind` data. Uninstall preserves data by default; explicit removal is a separate labelled action.
- Upgrade qualification starts from a previous desktop fixture, verifies backup/migration/data hashes, and tests an intentional migration failure plus recovery.

## Qualification Matrix

- Build-host checks: lockfile reproducibility, signing-ready hooks, checksums, unpacked resource inspection and bundled dependency inventory.
- Clean-VM checks: install, first run, all 13 routes, representative critical workflows, offline local workflows, provider-blocked states, crash/restart and clean shutdown.
- Security checks: renderer privilege negatives, CSP/navigation/window policy, malformed/unknown bridge calls, wrong IPC sender, credential/log redaction and local endpoint auth.
- Data checks: upgrade, failed upgrade recovery, uninstall/reinstall preservation and explicit-delete policy.

## External Gates

- Code-signing certificate acquisition and publication are external writes/costs requiring explicit authorization. The plan may create a signing-ready pipeline and unsigned test artifacts only.
- Clean-VM evidence must identify OS version, fixture hash and artifact checksum. A developer-machine-only run is not release qualification.

## Validation Architecture

| Layer | Proof | Blocking condition |
|---|---|---|
| Package | Reproducible artifact, checksum, inventory/SBOM and resource-path audit | Hidden runtime/download or mutable install resource |
| Clean VM | Install/first-run/routes/workflows without prerequisites | Docker/user runtime required or console/process leak |
| Upgrade/data | Previous fixture upgrade, failure recovery, uninstall/reinstall hashes | Data loss or irreversible partial migration |
| Security | Electron negative suite and credential/log scan | Privilege, navigation, IPC or secret leak |
| Closeout | Evidence index maps REQ-DESK-09/10 and cross-phase checks | Any required evidence missing or represented as passed without execution |

## Official Primary Sources

- https://www.electronjs.org/docs/latest/tutorial/security
- https://www.electronjs.org/docs/latest/tutorial/process-model
- https://www.electronjs.org/docs/latest/api/safe-storage

## RESEARCH COMPLETE
