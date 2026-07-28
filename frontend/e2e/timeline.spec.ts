import { expect, test, type Page } from "@playwright/test";

const events = Array.from({ length: 8 }, (_, index) => ({
  id: index + 1,
  logical_event_id: `event-${index + 1}`,
  title: index === 0 ? "雨夜相遇" : `事件 ${index + 1}`,
  description: `第 ${index + 1} 个证据支持的事件`,
  event_type: "plot",
  narrative_chapter_number: index + 1,
  narrative_index: index,
  story_rank: 8 - index,
  time_precision: index % 2 ? "relative" : "unknown",
  time_expression: index % 2 ? "三天后" : null,
  confidence: 0.9,
  participants: [{ mention: index % 2 ? "林墨" : "苏晚" }],
  provenance: {},
}));

async function mockTimeline(page: Page) {
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: { id: 1, username: "e2e", email: "e2e@example.com", is_active: true } }));
  await page.route("**/api/novels", (route) => route.fulfill({ json: { items: [{ id: 11, title: "雾城", author: "佚名", chapter_count: 12, word_count: 1000, status: "ready", reading_progress: { chapter_id: 8, progress_percent: 50 }, created_at: "", updated_at: "" }], total: 1, skip: 0, limit: 20 } }));
  await page.route("**/api/timeline/11/start-or-resume", (route) => route.fulfill({ json: { id: 3, novel_id: 11, status: "running", progress: { completed_chapters: 8, total_chapters: 12 }, cancel_requested: false, updated_at: "2026-07-13T04:00:00Z" } }));
  await page.route("**/api/timeline/11/status", (route) => route.fulfill({ json: { id: 3, novel_id: 11, status: "running", progress: { completed_chapters: 8, total_chapters: 12 }, cancel_requested: false, updated_at: "2026-07-13T04:00:00Z" } }));
  await page.route(/\/api\/timeline\/11(?:\?.*)?$/, (route) => route.fulfill({ json: { active: { source: "active", version_id: 7, status: "completed", progress: {}, events, causal_edges: [{ source_event_id: 1, target_event_id: 2, edge_type: "causes", confidence: 0.8 }], counts: { events: 8, participants: 2, causal_edges: 1 }, aggregates: { plot: 8 }, previews: [] }, running_candidate: null } }));
}

test("timeline renders a readable drill-down view with inline event detail", async ({ page }, testInfo) => {
  await mockTimeline(page);
  await page.goto("/analysis");
  await page.getByLabel("选择小说").selectOption("11");
    // 阶段窗口下钻（原型页的展开查看已不存在）
    await page.getByRole("button", { name: /阶段 1/ }).click();
  await expect(page.locator('[data-testid="timeline-canvas"] canvas')).toBeVisible();
  await expect(page.locator('[data-testid="timeline-canvas"]')).toHaveAttribute("data-zoom", "inside-slider");
  const firstEvent = page.getByRole("button", { name: /雨夜相遇/ });
  await firstEvent.focus();
  await expect(firstEvent).toBeFocused();
  await firstEvent.press("Enter");
  await expect(page.getByLabel("选中事件详情")).toContainText("雨夜相遇");
  await expect(page.getByRole("link", { name: "检索证据" })).toHaveAttribute("href", /\/search/);
  await expect(page.getByRole("link", { name: "阅读此章" })).toHaveAttribute("href", "/novels/11?chapter=1&from=timeline");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath(`timeline-${testInfo.project.name}.png`), fullPage: true });
});
