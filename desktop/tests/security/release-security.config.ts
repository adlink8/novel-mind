/**
 * Playwright config for the packaged release-security suite (Phase 45, plan
 * 45-04, Task 1).
 *
 * Runs the security negative suite against the SHIPPED win-unpacked artifact
 * (packaged NovelMind.exe) with the bundled next-standalone renderer served
 * through the packaged exe's embedded Node. The setup enforces the
 * checksum-bound artifact gate, isolates NOVELMIND_USER_DATA to a fresh temp
 * dir, and the specs launch the packaged exe via the shared e2e `launchShell`
 * helper (NOVELMIND_PACKAGED_EXE seam). Serial: one shared bundled renderer
 * owned by the setup/teardown.
 *
 * Run: npx playwright test --config tests/security/release-security.config.ts
 * (the plan verify also runs the config with `-RequireAll` semantics built in:
 * every test must pass for the suite to exit 0).
 */
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "release-security.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  reporter: [["list"]],
  globalSetup: "./release-security-setup.ts",
  globalTeardown: "./release-security-teardown.ts",
  use: {
    viewport: { width: 1440, height: 900 },
  },
});
