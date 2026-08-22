/**
 * Playwright config for the data-lifecycle unit suites (plan 43-03).
 *
 * Mirrors `tests/runtime/playwright.config.ts`: self-contained, pure unit
 * tests over an injected in-memory `FakeDataFs` — no Electron, no real
 * filesystem, no renderer. `testDir` is "." so the `tests/data/*.test.ts`
 * suites are discoverable (the desktop root config's globalSetup would spawn
 * the Next renderer, which these suites do not need).
 *
 * Run: npx playwright test --config tests/data/playwright.config.ts
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
