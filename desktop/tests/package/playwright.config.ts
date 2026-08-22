/**
 * Playwright config for the Phase 45 package qualification suites (plan 45-01).
 *
 * Pure unit-style suites over the staged packaging resources, the
 * electron-builder contract and (on Windows, after a build) the real packaged
 * executables. No global renderer is started here — the process-behavior suite
 * starts its own tiny loopback smoke server per run.
 *
 * Run: npx playwright test --config tests/package/playwright.config.ts
 */
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "**/*.test.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  reporter: [["list"]],
});
