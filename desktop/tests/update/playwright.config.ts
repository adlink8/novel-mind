/**
 * Playwright config for the Phase 45 update qualification suites (plan 45-02).
 *
 * Pure unit-style suites over the backup-first upgrade coordinator, the
 * uninstall/data-removal policy and the checksum-pinned prior-version fixture.
 * Self-contained: no Electron, no renderer, no global renderer server — the
 * suites use the real filesystem DataFs over temp app-data trees plus the
 * injected in-memory FakeDataFs for fault injection.
 *
 * Run: npx playwright test --config tests/update/playwright.config.ts
 */
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "**/*.{test,spec}.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  reporter: [["list"]],
});
