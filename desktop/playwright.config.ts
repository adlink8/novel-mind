import { defineConfig } from "@playwright/test";

/**
 * Desktop shell test harness (Phase 42).
 *
 * globalSetup builds the desktop package (tsc emit) and starts the existing
 * Next standalone renderer on an OS-allocated loopback port via the
 * Electron-embedded Node (ELECTRON_RUN_AS_NODE=1 — Phase 41 prerequisite #1,
 * proven in desktop/proof/bundled-node-evidence.json). The suites then launch
 * the real Electron app against that loopback origin and assert the security
 * boundary with privilege-negative tests.
 *
 * Suites (all under ./tests):
 * - shell-smoke.spec.ts  — 42-01 shell boundary negatives
 * - security/policy.spec.ts — 42-02 CSP/navigation/window/permission negatives
 * - security/ipc.spec.ts — 42-02 sender/schema/payload negatives
 *
 * Serial (workers: 1) so the single owned server instance is deterministic and
 * process cleanup is unambiguous. A concrete spec file may be targeted on the
 * CLI; `testMatch` is open so the full suite is the default gate.
 */
export default defineConfig({
  testDir: "./tests",
  testMatch: "**/*.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  reporter: [["list"]],
  globalSetup: "./tests/global-setup.ts",
  globalTeardown: "./tests/global-teardown.ts",
  use: {
    viewport: { width: 1440, height: 900 },
  },
});
