/**
 * Playwright config for the Phase 44 integration suites (44-03).
 *
 * Pure-Node suites — no Electron, no renderer — that drive the renderer's SSE
 * and capability-status modules against REAL local sockets (mock agent-service,
 * mock local library, dead provider endpoint). They run in-process, so they use
 * a self-contained config (testMatch covers `tests/integration/*.spec.ts`)
 * instead of the Electron shell harness in `playwright.config.ts`.
 *
 * The frontend modules they import are also covered by the frontend vitest
 * suite and `frontend npx tsc --noEmit`; this config's job is the real-socket
 * integration proof (reconnect/cancel/replay/rotation + offline capability
 * matrix).
 *
 * Run: npx playwright test --config tests/integration/playwright.config.ts
 */
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "**/*.spec.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  reporter: [["list"]],
});
