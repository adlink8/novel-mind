/**
 * Playwright config for the runtime unit suites (plan 43-01).
 *
 * The main desktop config (`playwright.config.ts`) is an Electron shell suite
 * whose globalSetup compiles desktop TS and spawns the Next standalone renderer.
 * The runtime contract/state-machine suites are pure unit tests over injected
 * FakeOps — no Electron, no renderer — so they use this self-contained config
 * (testMatch covers the plan's `*.test.ts` naming).
 *
 * Run: npx playwright test --config tests/runtime/playwright.config.ts
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
