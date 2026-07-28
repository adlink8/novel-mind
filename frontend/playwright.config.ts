import { defineConfig, devices } from "@playwright/test";
import path from "path";

/**
 * Playwright browser matrix (06-05 / D-12 / D-16):
 * - chromium-desktop (1280×800)
 * - chromium-mobile-390 (390×844)
 * - chromium-tablet-768 (768×1024)
 * - browser timeout 60s; retain trace/screenshots on failure
 *
 * Core success path hits real frontend + backend (no route mock).
 * webServer starts Next; backend is expected on :8010 (started by webServer
 * command when E2E_START_BACKEND=1, or externally).
 */

// Default 3005: some Windows environments block bind on :3000 (EACCES).
const PORT = Number(process.env.E2E_PORT || 3005);
const BASE_URL = process.env.E2E_BASE_URL || `http://127.0.0.1:${PORT}`;
const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8010";
const startBackend = process.env.E2E_START_BACKEND !== "0";

// Cookie CSRF origin gate must allow the frontend origin used by Playwright.
const CORS_ORIGINS = JSON.stringify([
  "http://localhost:3000",
  "http://localhost:3001",
  "http://localhost:3002",
  "http://localhost:3005",
  "http://127.0.0.1:3000",
  "http://127.0.0.1:3001",
  "http://127.0.0.1:3002",
  "http://127.0.0.1:3005",
  `http://127.0.0.1:${PORT}`,
  `http://localhost:${PORT}`,
]);

const backendDir = path.resolve(__dirname, "../backend");
// Prefer PATH-resolved python when running under backend venv activation;
// otherwise call the platform-specific venv binary via shell.
const backendCmd =
  process.platform === "win32"
    ? "venv\\Scripts\\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010"
    : "venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8010";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  timeout: 60_000, // D-16 browser timeout
  expect: { timeout: 15_000 },
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "playwright-report" }],
    ["junit", { outputFile: "test-results/playwright-junit.xml" }],
  ],
  outputDir: "test-results",
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },
  projects: [
    {
      name: "chromium-desktop",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1280, height: 800 },
      },
    },
    {
      name: "chromium-mobile-390",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 390, height: 844 },
        isMobile: true,
        hasTouch: true,
      },
    },
    {
      name: "chromium-tablet-768",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 768, height: 1024 },
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
  webServer: [
    ...(startBackend
      ? [
          {
            command: backendCmd,
            cwd: backendDir,
            url: `${BACKEND_URL}/api/health`,
            reuseExistingServer: !process.env.CI,
            timeout: 120_000,
            stdout: "pipe" as const,
            stderr: "pipe" as const,
            env: {
              ...process.env,
              NOVELMIND_CORS_ORIGINS: CORS_ORIGINS,
              // e2e 后端默认打到 CI 隔离库，防止测试数据污染开发库 novelmind
              NOVELMIND_DATABASE_URL:
                process.env.NOVELMIND_DATABASE_URL ??
                "postgresql+asyncpg://novelmind:novelmind@127.0.0.1:5433/novelmind_ci",
            },
          },
        ]
      : []),
    {
      command: `npx next dev --hostname 127.0.0.1 --port ${PORT}`,
      url: BASE_URL,
      reuseExistingServer: !process.env.CI,
      timeout: 180_000,
      env: {
        ...process.env,
        BACKEND_URL,
        NEXT_PUBLIC_API_URL: "/api",
      },
    },
  ],
});
