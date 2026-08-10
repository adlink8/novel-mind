import { defineConfig } from "@playwright/test";

/**
 * Desktop shell smoke suite (Phase 42, Plan 42-01).
 *
 * globalSetup builds the desktop package (tsc emit) and starts the existing
 * Next standalone renderer on an OS-allocated loopback port via the
 * Electron-embedded Node (ELECTRON_RUN_AS_NODE=1 — Phase 41 prerequisite #1,
 * proven in desktop/proof/bundled-node-evidence.json). The suite then launches
 * the real Electron app against that loopback origin and asserts the security
 * boundary with privilege-negative tests.
 *
 * Serial (workers: 1) so the single owned server instance is deterministic and
 * process cleanup is unambiguous.
 */
export default defineConfig({
  testDir: "./tests",
  testMatch: "**/shell-smoke.spec.ts",
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
