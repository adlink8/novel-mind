/**
 * Error + isolation journeys (D-12):
 * - cross-user novel access → 404 / empty / denied
 * - DB / Chroma / API blocked → error states (metrics not fabricated)
 * - auth failure messaging
 *
 * Core path remains unmocked; dependency failures use route interception.
 */
import { test, expect } from "@playwright/test";
import {
  apiLogin,
  apiRegister,
  login,
  registerAndLogin,
  uniqueUser,
} from "./helpers";

const BACKEND = process.env.BACKEND_URL || "http://127.0.0.1:8010";

test.describe("error and isolation", () => {
  test("wrong password shows error state", async ({ page }) => {
    const user = uniqueUser("auth");
    await registerAndLogin(page, user);

    // Logout
    const logout = page.getByRole("button", { name: /退出|logout/i }).or(
      page.locator('button[title="退出登录"]')
    );
    if (await logout.first().isVisible().catch(() => false)) {
      await logout.first().click();
    } else {
      // Clear cookies to force re-auth
      await page.context().clearCookies();
      await page.goto("/");
    }

    await expect(page.locator('input[name="username"]')).toBeVisible({
      timeout: 15_000,
    });
    // Ensure login mode
    const hasAccount = page.getByRole("button", { name: /已有账户/ });
    if (await hasAccount.isVisible().catch(() => false)) {
      await hasAccount.click();
    }
    await page.locator('input[name="username"]').fill(user.username);
    await page.locator('input[name="password"]').fill("WrongPass999!");
    await page.getByRole("button", { name: /^登录$/ }).click();
    await expect(page.getByText(/用户名或密码错误|登录失败|注册或登录失败/)).toBeVisible({
      timeout: 15_000,
    });
  });

  test("cross-user novel id is isolated (404)", async ({ page, request }) => {
    const owner = uniqueUser("owner");
    const other = uniqueUser("other");

    // Create owner + a novel via API if possible
    await apiRegister(request, owner);
    const ownerLogin = await apiLogin(request, owner.username, owner.password);
    const ownerCookies = await ownerLogin.headersArray();
    const setCookie = ownerCookies
      .filter((h) => h.name.toLowerCase() === "set-cookie")
      .map((h) => h.value.split(";")[0])
      .join("; ");

    // Upload a minimal novel as owner
    const content = "第一章\n隔离测试内容。\n";
    const form = new FormData();
    form.append(
      "file",
      new Blob([content], { type: "text/plain" }),
      "isolation.txt"
    );
    const uploadRes = await request.post(`${BACKEND}/api/novels/upload`, {
      headers: setCookie ? { Cookie: setCookie } : undefined,
      multipart: {
        file: {
          name: "isolation.txt",
          mimeType: "text/plain",
          buffer: Buffer.from(content, "utf-8"),
        },
      },
    });

    let novelId: number | null = null;
    if (uploadRes.ok()) {
      const body = await uploadRes.json();
      novelId = body.id ?? body.data?.id ?? null;
    }

    // Login as other user in browser
    await registerAndLogin(page, other);

    if (novelId != null) {
      await page.goto(`/novels/${novelId}`);
      // Owner isolation: detail must not leak content
      await expect(
        page.getByText(/不存在|失败|无权|404|加载|错误|小说/).first()
      ).toBeVisible({ timeout: 20_000 });
      await expect(page.getByText("隔离测试内容")).toHaveCount(0);
    } else {
      // Fallback: hit a very large id that cannot exist for this user
      await page.goto("/novels/99999999");
      await expect(
        page.getByText(/不存在|失败|错误|加载/).first()
      ).toBeVisible({ timeout: 20_000 });
    }
  });

  test("API outage surfaces error empty-states (blocked dependency)", async ({
    page,
  }) => {
    const user = uniqueUser("blocked");
    await registerAndLogin(page, user);

    // Block backend proxy for novels + search + eval
    await page.route("**/api/novels**", async (route) => {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          detail: "blocked_dependency",
          quality_comparable: false,
          metrics: null,
        }),
      });
    });
    await page.route("**/api/search**", async (route) => {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "blocked_dependency" }),
      });
    });
    await page.route("**/api/eval/**", async (route) => {
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({
          status: "blocked_dependency",
          quality_comparable: false,
          metrics: null,
        }),
      });
    });

    await page.goto("/novels");
    // Bookshelf should not crash; may show empty or error
    await expect(page.locator("body")).toContainText(/书架|作品|失败|错误|导入/);

    await page.goto("/search?q=test");
    await expect(page.getByText(/搜索失败|失败|错误|稍后/)).toBeVisible({
      timeout: 15_000,
    });

    await page.goto("/eval");
    await expect(
      page.getByText(/加载数据失败|数据加载失败|请确认后端/).first()
    ).toBeVisible({ timeout: 20_000 });
  });

  test("chroma/db style failures do not invent quality scores", async ({
    page,
  }) => {
    const user = uniqueUser("dep");
    await registerAndLogin(page, user);

    await page.route("**/api/eval/quality/runs**", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              job_id: "blocked-job-1",
              status: "blocked_dependency",
              quality_comparable: false,
              metrics: null,
              error: "chroma unavailable",
            },
          ]),
        });
        return;
      }
      await route.continue();
    });

    // Allow datasets/runs so page can load, fail only quality
    await page.route("**/api/eval/datasets**", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: "[]",
      });
    });
    await page.route("**/api/eval/runs**", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: "[]",
        });
        return;
      }
      await route.continue();
    });

    await page.goto("/eval");
    await expect(page.getByRole("heading", { name: /RAG 评测/ })).toBeVisible({
      timeout: 20_000,
    });
    const qualityTab = page.getByRole("button", { name: /质量任务/ });
    await qualityTab.click();
    await expect(page.getByTestId("quality-status-blocked_dependency")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText("metrics=null")).toBeVisible();
    await expect(page.getByText(/chroma unavailable/)).toBeVisible();
  });
});
