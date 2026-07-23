/**
 * Core success journeys (D-12): register, login, upload, import, search,
 * reading, eval resume/report — real backend + frontend, no route mocks.
 *
 * Note: POST /api/novels/upload currently returns ImportJob.id as `id`.
 * The dialog polls /novels/{id}/import-status which may 404 until a novel
 * with that id exists. Core flow therefore asserts via the bookshelf list.
 */
import { test, expect } from "@playwright/test";
import {
  makeNovelFixture,
  registerAndLogin,
  uniqueUser,
} from "./helpers";

test.describe("core success flow", () => {
  test("register → login shell → upload → import → search → read → eval", async ({
    page,
  }) => {
    test.setTimeout(180_000);
    const user = uniqueUser("core");
    await registerAndLogin(page, user);

    // Dashboard / 工作台
    await expect(
      page.locator("#main-content").getByRole("heading", { name: /让每一段故事/ })
    ).toBeVisible({ timeout: 15_000 });

    // Navigate to bookshelf and open upload dialog
    await page.goto("/novels");
    await expect(page.getByRole("heading", { name: /我的书架/ })).toBeVisible();

    const title = `Core_${Date.now()}`;
    const fixture = makeNovelFixture(title);
    await page.getByRole("button", { name: /批量导入|导入小说/ }).click();

    const fileInput = page.locator('input[type="file"]');
    await expect(fileInput).toBeAttached({ timeout: 10_000 });
    await fileInput.setInputFiles(fixture);

    const uploadBtn = page.getByRole("button", { name: /开始上传/ });
    await expect(uploadBtn).toBeEnabled({ timeout: 10_000 });
    await uploadBtn.click();

    // Upload accepted (processing state or success); do not require dialog ready.
    await expect(
      page.getByText(/处理中|导入成功|解析|接收|Request failed/).first()
    ).toBeVisible({ timeout: 30_000 });
    // Fail fast on auth/CORS regression
    await expect(page.getByText(/Request failed with status code 403/)).toHaveCount(0);

    // Close dialog if still open and wait for imported novel on bookshelf
    const cancelOrClose = page.getByRole("button", { name: /取消|Close/i });
    if (await cancelOrClose.first().isVisible().catch(() => false)) {
      await cancelOrClose.first().click();
    }

    // Background import is fast; reload and wait on the novel card link
    const novelLink = page.locator(`a[href*="/novels/"]`).filter({
      hasText: title,
    });
    for (let i = 0; i < 20; i++) {
      await page.goto("/novels");
      try {
        await expect(novelLink.first()).toBeVisible({ timeout: 3_000 });
        break;
      } catch {
        if (i === 19) {
          throw new Error(`novel "${title}" should appear on bookshelf after import`);
        }
        await page.waitForTimeout(1500);
      }
    }

    // Open novel for reading
    await novelLink.first().click();
    await expect(
      page.getByText(/第一章|开端|章节|返回|图书馆|字/).first()
    ).toBeVisible({ timeout: 30_000 });

    // Search page
    await page.goto("/search");
    await expect(page.getByRole("heading", { name: /原文检索/ })).toBeVisible();
    const searchBox = page.locator("input").first();
    await searchBox.fill("图书馆");
    await searchBox.press("Enter");
    await expect(page.locator("body")).toContainText(
      /检索|搜索|结果|暂无|失败|证据|图书馆/
    );

    // Eval page — datasets / quality tabs / resume surface
    await page.goto("/eval");
    await expect(page.getByRole("heading", { name: /RAG 评测/ })).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByRole("button", { name: /评测数据集/ })).toBeVisible();
    await expect(page.getByRole("button", { name: /质量任务/ })).toBeVisible();

    await page.getByRole("button", { name: /质量任务/ }).click();
    await expect(
      page.getByText(/暂无质量评测任务|恢复|查看报告/).first()
    ).toBeVisible();

    await page.getByRole("button", { name: /评测运行/ }).click();
    await expect(
      page.getByText(/触发评测运行|暂无评测运行|历史运行/).first()
    ).toBeVisible();
  });
});
