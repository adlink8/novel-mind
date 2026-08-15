---
phase: 45-windows-packaging-migration-and-desktop-qualification
plan: "04"
subsystem: desktop-security-sbom-closeout
tags: [desktop, security-negative-audit, electron-checklist, sbom, evidence-integrity, checksum-bound, fail-closed, clean-vm-gap, signing-external-gate, phase22-independent, v1.5-closeout, release-blocked]
requires:
  - "45-01 (reproducible win-unpacked + NSIS artifact, CHECKSUMS.SHA256, bundled-inventory.json)"
  - "45-02 (upgrade/recovery/uninstall policy + prior-version fixture)"
  - "45-03 (qualification-manifest.json clean_vm=false + 32/32 packaged UAT on this machine)"
  - "42-02 (security boundary: CSP/IPC sender/navigation/permissions)"
  - "41-03 (runtime-manifest.json NO-GO evidence, hash cb8fa6c9…)"
  - "44-02/44-03 (credential store + local-auth + SSE wiring)"
provides:
  - "desktop/tests/security/release-security.spec.ts (17 tests) + setup/teardown/state/config — packaged security negative suite against the SHIPPED exe (webPreferences read-back, privilege negatives, CSP, navigation/window, IPC sender/channel/payload, local-auth replay, redaction, external-loading)"
  - "desktop/scripts/generate-sbom.ps1 -Verify — checksum-bound SBOM/component-provenance gate (proof-manifest hash, artifact hashes vs manifest, staged re-hash, secret scan, unsigned claim); output desktop/dist/release-sbom.json"
  - "desktop/scripts/verify-release-evidence.ps1 -RequireAll — REQ-DESK-01..10 evidence matrix -> fail-closed verdict; deleting any required evidence flips to FAIL; writes verification.json + 45-VERIFICATION.md"
  - ".planning/phases/45-windows-packaging-migration-and-desktop-qualification/45-SECURITY.md — packaged security audit record (Electron checklist + negatives + SBOM + open gates)"
  - ".planning/phases/45-windows-packaging-migration-and-desktop-qualification/45-VERIFICATION.md — v1.5 evidence-backed closeout verdict (release-blocked)"
  - "IMPLEMENTATION-STATUS.md — honest v1.5 desktop closeout snapshot"
  - "desktop/tests/clean-vm/results/verification.json — machine verdict evidence"
affects:
  - "Post-45 prerequisites: bundled Python/FastAPI, PostgreSQL/pgvector, vector store (41-DECISION PREREQ-2/3/4); PackagedProcessAdapter main-process wiring; clean-VM execution; signing/publication (D-45-06)"
tech-stack:
  added:
    - "No new npm/PowerShell dependencies — node:crypto/node:fs, Playwright, existing electron-builder artifacts"
  patterns:
    - "Release evidence gate as a checksum-bound matrix: required files + per-file hash vs manifest + content markers + fail-closed exit (T-45-04-01)"
    - "Packaged security re-run of dev-mode negatives through the SAME launch seam (launchShell / NOVELMIND_PACKAGED_EXE) against the shipped exe"
    - "Honest two-dimensional verdict: evidence integrity (must PASS) vs disclosed external gates (clean-VM/signing/Phase 22) that keep release-not-ready"
    - "SBOM reproducibility: run twice, prior-vs-fresh drift detection on artifact + staged aggregate hashes"
key-files:
  created:
    - desktop/tests/security/release-security.spec.ts (17 tests)
    - desktop/tests/security/release-security-setup.ts
    - desktop/tests/security/release-security-teardown.ts
    - desktop/tests/security/release-security-state.ts
    - desktop/tests/security/release-security.config.ts
    - desktop/scripts/generate-sbom.ps1
    - desktop/scripts/verify-release-evidence.ps1
    - .planning/phases/45-windows-packaging-migration-and-desktop-qualification/45-SECURITY.md
    - .planning/phases/45-windows-packaging-migration-and-desktop-qualification/45-VERIFICATION.md
    - desktop/tests/clean-vm/results/verification.json
    - desktop/dist/release-sbom.json (gitignored build evidence)
  modified:
    - IMPLEMENTATION-STATUS.md (v1.5 desktop closeout snapshot)
decisions:
  - "Verdict is release-blocked at the evidence-supported level: REQ-DESK-01..09 verified (some only on this machine), REQ-DESK-10 partial — pristine clean-VM execution is missing and remains a blocking gap (D-45-07/D-45-09)."
  - "Signing/publication stay external gates (D-45-06): the artifact is unsigned (signAndEditExecutable=false) and every evidence surface records unsigned=true; no claim of signing or public trust is made."
  - "Phase 22 is reported as an independent 0/3 blocked fact and is never altered by the desktop verdict (nor the reverse)."
  - "41-DECISION NO-GO remains unchanged: only Electron 43.3.0 / embedded Node v24.18.1 / Next standalone are packaged; Python/PG/vector stay post-45 prerequisites (PREREQ-2/3/4)."
metrics:
  started: "2026-08-11"
  completed: "2026-08-11"
  typecheck: "PASS"
  packaged_security: "17 passed / 0 failed (release-security.spec.ts against shipped win-unpacked exe)"
  dev_security_regression: "21 passed (policy+ipc) + 16 passed (credential/local-auth) = 37"
  total_security: "54 passed / 0 failed"
  sbom: "12/12 checks PASS twice, no drift; runtime-manifest hash cb8fa6c9… matches 41-DECISION (untampered); staged 1440 files / 34,019,789 bytes re-hashed"
  checksums: "CHECKSUMS.SHA256 3/3 verified (CRLF-normalized parse)"
  evidence_gate: "verify-release-evidence.ps1 -RequireAll PASS twice (overall release-blocked, exit 0); deleting required evidence flips to FAIL (exit 1)"
  verdict: "release-blocked (releaseReady=false, clean_vm=false)"
  uncommitted: true
---

# Phase 45 Plan 04: Packaged Security Audit, SBOM and v1.5 Honest Closeout — Summary

## Objective (one-liner)

Run the Electron security negative audit against the SHIPPED Windows artifact,
generate checksum-bound SBOM/evidence-integrity proof, and derive the v1.5
closeout verdict strictly from executed evidence — with clean-VM, signing and
Phase 22 reported as honest external/blocked facts, never fabricated.

## Machine Boundary (honest)

Same boundary as 45-UAT.md: **no pristine clean Windows VM was available or
authorized**. The packaged security suite and the evidence gate ran on the
developer workstation with isolated `NOVELMIND_USER_DATA` and the bundled
renderer served through the **shipped packaged exe's** embedded Node
(`ELECTRON_RUN_AS_NODE`). Every evidence surface records `clean_vm=false`;
missing clean-VM execution keeps REQ-DESK-10 **release-blocked** (D-45-07/
D-45-09) and is never overridden.

## Deliverables

### Task 1 — Packaged security negatives + SBOM

- `release-security.spec.ts` (17 tests) launches the shipped `win-unpacked`
  `NovelMind.exe` and asserts the 42-02 boundary on the packaged binary:
  live `getLastWebPreferences` (sandbox/contextIsolation/webSecurity true,
  nodeIntegration false), renderer privilege negatives (no require/process/
  module/global), CSP header deny-by-default + nosniff, navigation/window/popup/
  webview/javascript-file negatives, permission denial, forged-sender rejection
  on every known channel, unknown channel, malformed/oversized payload,
  local-auth replay null, redacted status/bootstrap, loopback-only requests,
  and a no-source-map/no-secret scan of packaged resources.
- `generate-sbom.ps1 -Verify` computes a checksum-bound SBOM over the shipped
  artifact: `runtime-manifest.json` hash vs the 41-DECISION record, packaged
  server.js/installer/exe/asar vs the qualification manifest, per-file re-hash
  of the staged inventory (1440 files / 34,019,789 bytes), component inventory
  vs staged pins + 41 NO-GO notBundled boundary, packaged node_modules presence,
  secret scan, and an explicit `unsigned=true` claim. **12/12 PASS, run twice,
  no drift**; output `desktop/dist/release-sbom.json`.
- `45-SECURITY.md` records the Electron security-checklist verdict, the negative
  injection results table, SBOM numbers and the open external gates.

### Task 2 — v1.5 release verdict from complete evidence

- `verify-release-evidence.ps1 -RequireAll` maps REQ-DESK-01..10 to Phase 41/42/
  43/44/45 evidence, verifies required files (presence + content markers +
  artifact hashes), and computes the verdict. Deleting any required evidence
  (tested: `CHECKSUMS.SHA256`) flips the run to FAIL/exit 1 (T-45-04-01).
- Writes `45-VERIFICATION.md` (the closeout verdict) and
  `desktop/tests/clean-vm/results/verification.json`.
- Verdict: **release-blocked** — REQ-DESK-01..09 verified (01/07/08 only on this
  machine), REQ-DESK-10 partial because clean-VM is missing. Signing/publication
  are external gates (D-45-06). Phase 22 stays an independent 0/3 blocked fact.

### Task 3 — IMPLEMENTATION-STATUS.md snapshot

- Appended a v1.5 desktop closeout snapshot: verified items (packaging,
  upgrade/uninstall, local-approximation UAT, security negatives, SBOM) plus the
  explicit non-release-ready facts (clean-VM, signing, 41 NO-GO un-reversed,
  PackagedProcessAdapter wiring, Phase 22 0/3).

## Verification Evidence

| Check | Result |
|---|---|
| `cd desktop && npm run typecheck` | PASS |
| `npx playwright test --config tests/security/release-security.config.ts` | **17 passed / 0 failed** against the shipped win-unpacked exe |
| `npx playwright test tests/security/policy.spec.ts tests/security/ipc.spec.ts` (dev regression) | 21 passed |
| `npx playwright test --config tests/security/playwright.config.ts` (credential/local-auth) | 16 passed |
| `powershell -File desktop/scripts/generate-sbom.ps1 -Verify` | **12/12 PASS**, run twice, prior-vs-fresh no drift |
| runtime-manifest anti-tamper | sha256 `cb8fa6c9…` == 41-DECISION recorded hash (NO-GO evidence untampered) |
| CHECKSUMS.SHA256 | 3/3 verified (installer/exe/asar) via CRLF-normalized parse |
| `powershell -File desktop/scripts/verify-release-evidence.ps1 -RequireAll` | PASS twice: overall release-blocked, exit 0, clean-vm blocker disclosed |
| Tamper test (delete CHECKSUMS.SHA256) | FAIL / exit 1 — evidence integrity gate is fail-closed |
| `git status` | only planned files + pre-existing user modifications (none touched) |

## Deviations from Plan

### Auto-fixed Issues (all Rule 1/3, in-scope)

1. **[Rule 1 - Bug] `Test-Path` treated staged `[id]` routes as wildcards.**
   The staged manifest contains `.next/.../[id]/...`; `Test-Path` without
   `-LiteralPath` failed a file that exists. Fix: `-LiteralPath` in the SBOM
   staged re-hash loop.
2. **[Rule 1 - Bug] PS 5.1 lacks `SHA256::HashData`.** Replaced with
   `SHA256.Create()` + `ComputeHash(MemoryStream)`.
3. **[Rule 1 - Bug] PS 5.1 rejects `if` as a value expression.** The SBOM check
   details used `if (...) {...} else {...}` inline; fixed to `$(if ...)`.
4. **[Rule 3 - Blocking] PS 5.1 misread non-ASCII chars.** Em-dashes/bullets in
   the two ps1 scripts were decoded as GBK mojibake into the generated evidence.
   Fix: scripts are pure ASCII now; 45-VERIFICATION.md/SBOM regenerated clean.
5. **[Rule 1 - Bug] Timestamp literal in 45-VERIFICATION.md.** `$(Get-Date)…`
   inside a double-quoted string was emitted as literal text. Fix: computed
   `$generatedAt` first, then interpolated.
6. **[Rule 1 - Bug] Verifier's Phase 22 negation matched "must not be marked
   complete".** The not-complete regex caught STATE.md's override sentences.
   Fix: match only positive completion claims ("is/was/has been … passed").
7. **[Rule 1 - Bug] `Playwright exit:` marker regex.** `**Playwright exit:** 0`
   markdown emphasis broke the regex; relaxed to `Playwright exit:.*0 \(PASS\)`.
8. **[Rule 1 - Bug] `getLastWebPreferences` not in Electron 43 public typings +
   `nodeIntegrationInWorker` reported only when non-default.** Typechecked via an
   untyped runtime probe; assertion changed to `not.toBe(true)` (deny-by-default).
9. **[Rule 3 - Blocking] Setup/spec desktop-root path.** Files live in
   `tests/security/`, so `path.resolve(__dirname, "..", "..")` is required;
   the first run pointed at `tests/dist/win-unpacked`.
10. **[Rule 1 - Bug] `getLocalAuthToken` null vs undefined.** The packaged
    session-less token returns `null` (fail-closed), matching the contract.

## Known Stubs

None introduced. The following are documented, non-stub boundaries (each is
reported honestly, never as passed):
- Packaged main-process `PackagedProcessAdapter` auto-wiring is a **post-45
  prerequisite**; the suite uses the `NOVELMIND_RENDERER_URL` seam with the
  bundled renderer served through the shipped exe's embedded Node.
- Pristine clean-VM execution (fresh OS, no developer profile, no repo
  toolchain) is **missing** and keeps REQ-DESK-10 release-blocked.
- Code signing and publication are **external gates** (D-45-06); the artifact is
  unsigned and never described as publicly trusted/signed.

## Threat Flags

None beyond the plan's threat model. New surfaces: the packaged security suite
launches the existing packaged exe over loopback (no new network/auth/file
surface); the SBOM/verifier scripts are read-only evidence gates (T-45-04-01/02/03
mitigations). No renderer-visible secret, endpoint or file-access path was added.

## Self-Check: PASSED

- `desktop/tests/security/release-security.{spec,config}.ts` + setup/teardown/
  state (5 files), `desktop/scripts/generate-sbom.ps1`,
  `desktop/scripts/verify-release-evidence.ps1`, `45-SECURITY.md`,
  `45-VERIFICATION.md`, `desktop/tests/clean-vm/results/verification.json`,
  `desktop/dist/release-sbom.json` — all exist.
- `npm run typecheck` PASS; packaged security 17/17; dev security 37/37
  (21+16); SBOM 12/12 twice no drift; verifier PASS twice (exit 0) and FAIL on
  evidence deletion (exit 1); CHECKSUMS 3/3; `git status` shows only planned
  files plus pre-existing user modifications (none touched); no commit made.
