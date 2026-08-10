import { defineConfig } from "@playwright/test";

/**
 * Clean-VM qualification suite (Phase 45, plan 45-03).
 *
 * Runs the first-run, critical-workflow, route-parity and offline-recovery
 * specs against the SHIPPED packaged exe (win-unpacked/NovelMind.exe) with the
 * bundled next-standalone renderer served through the packaged exe's embedded
 * Node. NOVELMIND_PACKAGED_EXE is injected by run-qualification.ps1 after the
 * provisioned (tightened-PATH, isolated-user-data) environment.
 *
 * Serial: one shared bundled renderer child owned by the global setup/teardown.
 */
export default defineConfig({
  testDir: "..",
  testMatch: [
    "e2e/first-run.spec.ts",
    "e2e/route-parity.spec.ts",
    "e2e/critical-workflows.spec.ts",
    "e2e/offline-recovery.spec.ts",
    // z-* sorts last: it kills its OWN renderer instance and must run after
    // every spec that needs the shared bundled server.
    "e2e/z-killed-service.spec.ts",
  ],
  fullyParallel: false,
  workers: 1,
  timeout: 180_000,
  expect: { timeout: 20_000 },
  reporter: [["list"]],
  globalSetup: "./qualification-setup.ts",
  globalTeardown: "./qualification-teardown.ts",
  use: {
    viewport: { width: 1440, height: 900 },
  },
});