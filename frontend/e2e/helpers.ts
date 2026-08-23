import { expect, type APIRequestContext, type Page } from "@playwright/test";
import fs from "fs";
import path from "path";
import os from "os";

const BACKEND = process.env.BACKEND_URL || "http://127.0.0.1:8010";

// Desktop rail is `hidden md:flex`; 390px renders the mobile bottom nav instead.
// The authenticated-shell assertion must accept whichever variant the viewport shows.
const SHELL_NAV_VISIBLE =
  '[data-testid="app-shell-nav"]:visible, [data-testid="app-shell-nav-mobile"]:visible';

export async function expectAuthenticatedShell(page: Page) {
  await expect(page.locator(SHELL_NAV_VISIBLE).first()).toBeVisible({
    timeout: 30_000,
  });
}

/**
 * Phase 25.1 起 /analysis 默认打开对话视图；可视化用例须先切到「分析」标签。
 * 已在分析视图时为幂等 no-op。
 */
export async function openAnalysisVisualization(page: Page) {
  const tab = page.getByTestId("analysis-view-tab-analysis");
  await expect(tab).toBeVisible({ timeout: 15_000 });
  if ((await tab.getAttribute("aria-selected")) !== "true") {
    await tab.click();
  }
}

/** Backend python for qualification helpers: venv locally, runner python in CI. */
export function backendPythonBin(backendDir: string): string {
  if (process.env.E2E_PYTHON) return process.env.E2E_PYTHON;
  const venv =
    process.platform === "win32" ? "venv\\Scripts\\python.exe" : "venv/bin/python";
  return fs.existsSync(path.join(backendDir, venv)) ? venv : "python";
}

export function uniqueUser(prefix = "e2e") {
  const stamp = `${Date.now()}_${Math.floor(Math.random() * 1e6)}`;
  return {
    username: `${prefix}_${stamp}`.slice(0, 48),
    email: `${prefix}_${stamp}@example.com`,
    password: "Password1!",
  };
}

/** Register + login through the test-only multi-user API, then open the shell. */
export async function registerAndLogin(page: Page, user = uniqueUser()) {
  // page.request shares the browser context cookie jar. The product no longer
  // exposes username/password UI, while qualification still needs distinct
  // owners for isolation tests.
  await apiRegister(page.request, user);
  await apiLogin(page.request, user.username, user.password);
  await page.goto("/");

  // Authenticated shell: desktop rail or mobile bottom nav, per viewport.
  await expectAuthenticatedShell(page);
  // Prefer main content when present (desktop/home); skip if navigated elsewhere.
  const homeHeading = page
    .locator("#main-content")
    .getByRole("heading", { name: /让每一段故事/ });
  if (await homeHeading.isVisible().catch(() => false)) {
    await expect(homeHeading).toBeVisible();
  }
  return user;
}

export async function login(page: Page, username: string, password: string) {
  await apiLogin(page.request, username, password);
  await page.goto("/");
  await expectAuthenticatedShell(page);
}

/** Create a small UTF-8 novel file for upload. */
export function makeNovelFixture(title = "E2E 测试小说"): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "novelmind-e2e-"));
  const filePath = path.join(dir, `${title}.txt`);
  const body = [
    `《${title}》`,
    "",
    "第一章 开端",
    "这是自动化端到端测试用的第一章内容。主角走进图书馆，看见一本发光的古书。",
    "",
    "第二章 发展",
    "古书翻开后，出现了关于命运与选择的段落。主角决定把它带回房间细读。",
    "",
  ].join("\n");
  fs.writeFileSync(filePath, body, "utf-8");
  return filePath;
}

/** Direct API registration (for multi-user isolation tests). */
export async function apiRegister(
  request: APIRequestContext,
  user: { username: string; email: string; password: string }
) {
  const res = await request.post(`${BACKEND}/api/auth/register`, {
    data: user,
  });
  if (!res.ok()) {
    throw new Error(`register failed: ${res.status()} ${await res.text()}`);
  }
  return res;
}

export async function apiLogin(
  request: APIRequestContext,
  username: string,
  password: string
) {
  let failure = "login failed";
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    const res = await request.post(`${BACKEND}/api/auth/login`, {
      data: { username, password },
    });
    if (res.ok()) return res;

    failure = `login failed: ${res.status()} ${await res.text()}`;
    // Registration commits in the request dependency finalizer. A login sent
    // immediately after 201 can briefly race that commit on CI runners.
    if (res.status() !== 401 || attempt === 3) break;
    await new Promise((resolve) => setTimeout(resolve, attempt * 100));
  }
  throw new Error(failure);
}
