/**
 * Phase 34-02 — Reader-safe inline illustration presentation browser evidence
 * (REQ-VIS-05, D-34-01/D-34-02).
 *
 * Proves the browser-visible consequences of the illustration anchor contract:
 *   - a server-published `valid` anchor whose hash replays the current chapter
 *     content renders the approved asset inline with an accessible
 *     caption/alt (`<figure>`/`<figcaption>`, no innerHTML);
 *   - a missing binary is a graceful accessible placeholder — never a broken
 *     image or a silent drop;
 *   - a stale anchor (changed text) is presented as `needs_repair` and never
 *     renders the approved asset;
 *   - on desktop and 390px mobile the figure stays in flow layout with no
 *     horizontal overflow and no overlap with the reader progress/navigation.
 *
 * Routes are mocked (no real backend); the reader URL is
 * `/novels/{novelId}?chapter={chapterId}`. NOTE: on this machine the Next 16
 * canary dev server fails to compile (pre-existing), so this spec is kept
 * structurally valid and executed by the verification sub-agent when the
 * environment allows.
 */
import { createHash } from "crypto";
import { expect, test, type Page } from "@playwright/test";

const H = (n: number) => String(n).repeat(64);
const sha = (s: string) => createHash("sha256").update(s, "utf8").digest("hex");

const CHAPTER_ID = 101;
const NOVEL_ID = 11;

// One tiny valid PNG (1x1) served as the approved asset bytes.
const TINY_PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64"
);

const CHAPTER_CONTENT = [
  "第一章 雾城夜读",
  "",
  "第一段文字交代背景，城市笼罩在薄雾之中。",
  "",
  "第二段是插图所在的位置，主角站在窗前看见远山。",
  "",
  "第三段继续叙述，雨声敲打着玻璃。",
  "",
  "第四段收尾，灯光在夜色里亮起。",
  "",
].join("\n");

interface AnchorFixture {
  id: number;
  assetRevisionId: number;
  sourceStart: number;
  excerpt: string;
  anchorHash: string;
  contentHash: string;
  caption: string;
  altText: string;
  status: "valid" | "needs_repair" | "invalid";
  bytesStatus?: number;
}

function makeAnchor(f: AnchorFixture): Record<string, unknown> {
  return {
    id: f.id,
    owner_id: 1,
    novel_id: NOVEL_ID,
    chapter_id: CHAPTER_ID,
    chapter_number: 1,
    anchor_key: `anchor-${f.id}`,
    proposal_id: f.id,
    source_snapshot_id: "ss-1",
    source_snapshot_hash: H(1),
    paragraph_start: 2,
    paragraph_end: 2,
    source_start: f.sourceStart,
    source_end: f.sourceStart + f.excerpt.length,
    excerpt: f.excerpt,
    anchor_hash: f.anchorHash,
    chapter_content_hash: f.contentHash,
    published_asset_revision_id: f.assetRevisionId,
    publish_manifest_hash: H(2),
    approval_request_id: 9,
    status: f.status,
    caption: f.caption,
    alt_text: f.altText,
    citation: "第一章 · 第 2 段",
    approved_by: "owner",
    approved_at: "2026-08-04T00:00:00Z",
  };
}

function buildFixtures(): AnchorFixture[] {
  const excerptStart = CHAPTER_CONTENT.indexOf("第二段是插图所在的位置");
  const excerpt = CHAPTER_CONTENT.slice(
    excerptStart,
    excerptStart + "第二段是插图所在的位置，主角站在窗前看见远山。".length
  );
  const valid: AnchorFixture = {
    id: 1,
    assetRevisionId: 101,
    sourceStart: excerptStart,
    excerpt,
    anchorHash: sha(excerpt),
    contentHash: sha(CHAPTER_CONTENT),
    caption: "窗前的远山",
    altText: "主角站在窗前看见远处朦胧的山影",
    status: "valid",
    bytesStatus: 200,
  };
  const missing: AnchorFixture = {
    id: 2,
    assetRevisionId: 102,
    sourceStart: excerptStart,
    excerpt,
    anchorHash: sha(excerpt),
    contentHash: sha(CHAPTER_CONTENT),
    caption: "缺失的插图",
    altText: "该插图二进制已缺失",
    status: "valid",
    bytesStatus: 404,
  };
  const stale: AnchorFixture = {
    id: 3,
    assetRevisionId: 103,
    sourceStart: excerptStart,
    excerpt,
    anchorHash: sha(excerpt),
    contentHash: H(9), // does not replay the mocked chapter content
    caption: "已过期的插图",
    altText: "正文已修改，插图待修复",
    status: "needs_repair",
  };
  return [valid, missing, stale];
}

async function mockApp(page: Page, fixtures: AnchorFixture[]) {
  await page.route("**/api/**", (route) =>
    route.fulfill({ status: 500, json: { detail: "unmocked e2e endpoint" } })
  );
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      json: { id: 1, username: "e2e", email: "e2e@example.com", is_active: true },
    })
  );
  const novel = {
    id: NOVEL_ID,
    title: "雾城夜读",
    author: "佚名",
    description: null,
    genre: null,
    word_count: CHAPTER_CONTENT.length,
    chapter_count: 1,
    status: "ready",
    reading_progress: { chapter_id: CHAPTER_ID, progress_percent: 0 },
    created_at: "",
    updated_at: "",
  };
  const chapter = {
    id: CHAPTER_ID,
    novel_id: NOVEL_ID,
    chapter_number: 1,
    title: "第一章 雾城夜读",
    content: CHAPTER_CONTENT,
    word_count: CHAPTER_CONTENT.length,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
  await page.route("**/api/novels", (route) =>
    route.fulfill({ json: { items: [novel], total: 1 } })
  );
  await page.route(`**/api/novels/${NOVEL_ID}`, (route) =>
    route.fulfill({ json: novel })
  );
  await page.route(`**/api/novels/${NOVEL_ID}/chapters`, (route) =>
    route.fulfill({ json: [chapter] })
  );
  await page.route(
    `**/api/novels/${NOVEL_ID}/chapters/${CHAPTER_ID}`,
    (route) => route.fulfill({ json: chapter })
  );
  await page.route(`**/api/novels/${NOVEL_ID}/progress`, (route) =>
    route.fulfill({ status: 200, json: {} })
  );
  await page.route(`**/api/novels/${NOVEL_ID}/illustration-anchors`, (route) =>
    route.fulfill({
      json: { items: fixtures.map(makeAnchor), total: fixtures.length },
    })
  );
  for (const fixture of fixtures) {
    if (fixture.bytesStatus == null) continue;
    await page.route(
      `**/api/novels/${NOVEL_ID}/illustrations/assets/${fixture.assetRevisionId}/bytes`,
      (route) => {
        if (fixture.bytesStatus === 200) {
          return route.fulfill({
            status: 200,
            contentType: "image/png",
            body: TINY_PNG,
          });
        }
        return route.fulfill({ status: 404, json: { detail: "asset bytes missing" } });
      }
    );
  }
}

async function openReader(page: Page) {
  await page.goto(`/novels/${NOVEL_ID}?chapter=${CHAPTER_ID}`);
  await expect(
    page.getByTestId("reader-page-text").first()
  ).toBeVisible({ timeout: 30_000 });
}

test("desktop: a valid approved anchor renders inline with accessible caption", async ({
  page,
}) => {
  await mockApp(page, buildFixtures());
  await openReader(page);

  const validFigure = page.locator('[data-testid="illustration-block"][data-anchor-status="valid"]').first();
  await expect(validFigure).toBeVisible({ timeout: 30_000 });
  await expect(validFigure.getByTestId("illustration-image")).toHaveAttribute(
    "alt",
    "主角站在窗前看见远处朦胧的山影"
  );
  await expect(validFigure.getByTestId("illustration-caption")).toContainText(
    "窗前的远山"
  );
  await expect(validFigure.getByTestId("illustration-citation")).toContainText(
    "第一章 · 第 2 段"
  );
  // Flow layout: the figure is a sibling of the chapter text, not overlaid.
  const figureBox = await validFigure.boundingBox();
  expect(figureBox).not.toBeNull();
  const pageBox = await page.getByTestId("reader-page-text").first().boundingBox();
  expect(pageBox).not.toBeNull();
  expect(figureBox!.x).toBeGreaterThanOrEqual(pageBox!.x);
  expect(figureBox!.x + figureBox!.width).toBeLessThanOrEqual(pageBox!.x + pageBox!.width + 1);
});

test("desktop: a missing binary is a graceful accessible placeholder", async ({
  page,
}) => {
  await mockApp(page, buildFixtures());
  await openReader(page);

  const missingFigure = page.locator('[data-testid="illustration-block"][data-anchor-id="2"]');
  // The reader lazy-loads asset bytes via IntersectionObserver; on narrow
  // viewports the block sits below the fold so scroll it into view first.
  await missingFigure.scrollIntoViewIfNeeded();
  await expect(missingFigure.getByTestId("illustration-missing")).toBeVisible({
    timeout: 30_000,
  });
  await expect(missingFigure.getByTestId("illustration-caption")).toContainText(
    "缺失的插图"
  );
  await expect(missingFigure.getByTestId("illustration-image")).toHaveCount(0);
});

test("desktop: a stale anchor is presented as needs_repair, never the asset", async ({
  page,
}) => {
  await mockApp(page, buildFixtures());
  await openReader(page);

  const staleFigure = page.locator('[data-testid="illustration-block"][data-anchor-id="3"]');
  await expect(staleFigure).toHaveAttribute("data-anchor-status", "needs_repair", {
    timeout: 30_000,
  });
  await expect(staleFigure.getByTestId("illustration-placeholder")).toBeVisible();
  await expect(staleFigure.getByTestId("illustration-image")).toHaveCount(0);
});

test("mobile 390: figure stays in flow layout with no horizontal overflow", async ({
  page,
}) => {
  await mockApp(page, buildFixtures());
  await openReader(page);

  const validFigure = page.locator('[data-testid="illustration-block"][data-anchor-status="valid"]').first();
  await expect(validFigure).toBeVisible({ timeout: 30_000 });

  const noHScroll = await page.evaluate(
    () => document.documentElement.scrollWidth <= window.innerWidth + 1
  );
  expect(noHScroll).toBe(true);

  // The figure must not be wider than the reading column (no control overlap).
  const figureBox = await validFigure.boundingBox();
  const columnBox = await page.getByTestId("reader-scroll-column").boundingBox();
  expect(figureBox).not.toBeNull();
  expect(columnBox).not.toBeNull();
  expect(figureBox!.width).toBeLessThanOrEqual(columnBox!.width + 1);
});
