import { defineConfig } from "@playwright/test";

/**
 * Phase 41 route-parity suite (Plan 41-02). Runs against the Next standalone server
 * started from .next/standalone/server.js on an OS-allocated loopback port.
 *
 * Serial (workers: 1) so the single owned server instance is deterministic and process
 * cleanup is unambiguous.
 */
export default defineConfig({
  testDir: "./tests",
  testMatch: "**/route-parity.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  reporter: [["list"]],
  use: {
    viewport: { width: 1440, height: 900 },
    // Fresh per-test browser context: no service-worker caches, no shared storage.
    serviceWorkers: "block",
    launchOptions: { headless: true },
  },
});
