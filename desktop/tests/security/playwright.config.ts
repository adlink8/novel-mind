/**
 * Playwright config for the security unit suites (plan 44-02).
 *
 * Mirrors `tests/runtime/playwright.config.ts`: these suites are pure Node
 * (no Electron, no renderer) and run in-process, so they use a self-contained
 * config (testMatch covers `*.test.ts`) instead of the Electron shell harness.
 *
 * Run: npx playwright test --config tests/security/playwright.config.ts
 */
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "**/*.test.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  reporter: [["list"]],
});
