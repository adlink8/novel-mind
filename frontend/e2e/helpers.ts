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

async function waitForAuthGate(page: Page) {
  await page.goto("/");
  // Brand h1 + form h2 both exist; target the form heading specifically.
  await expect(
    page.getByRole("heading", { name: /建立你的故事库|回到你的故事里/ })
  ).toBeVisible({ timeout: 30_000 });
}

async function fillAuthForm(
  page: Page,
  fields: { username: string; password: string; email?: string }
) {
  await page.locator('input[name="username"]').fill(fields.username);
  if (fields.email != null) {
    await page.locator('input[name="email"]').fill(fields.email);
  }
  await page.locator('input[name="password"]').fill(fields.password);
}

/** Register + login through the AuthGate UI. */
export async function registerAndLogin(page: Page, user = uniqueUser()) {
  await waitForAuthGate(page);

  // Switch to register mode if needed
  const createAccount = page.getByRole("button", { name: /创建账户/ });
  if (await createAccount.isVisible().catch(() => false)) {
    await createAccount.click();
  }
  await expect(
    page.getByRole("heading", { name: /建立你的故事库/ })
  ).toBeVisible({ timeout: 10_000 });

  await fillAuthForm(page, {
    username: user.username,
    email: user.email,
    password: user.password,
  });
  await page.getByRole("button", { name: /注册并登录/ }).click();

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
  await waitForAuthGate(page);
  const hasAccount = page.getByRole("button", { name: /已有账户/ });
  if (await hasAccount.isVisible().catch(() => false)) {
    await hasAccount.click();
  }
  await fillAuthForm(page, { username, password });
  await page.getByRole("button", { name: /^登录$/ }).click();
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
  if (!res.ok() && res.status() !== 400) {
    throw new Error(`register failed: ${res.status()} ${await res.text()}`);
  }
  return res;
}

export async function apiLogin(
  request: APIRequestContext,
  username: string,
  password: string
) {
  const res = await request.post(`${BACKEND}/api/auth/login`, {
    data: { username, password },
  });
  if (!res.ok()) {
    throw new Error(`login failed: ${res.status()} ${await res.text()}`);
  }
  return res;
}
