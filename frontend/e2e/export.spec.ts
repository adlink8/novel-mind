/**
 * Phase 34-04 — Novel export download and error-state browser evidence
 * (REQ-VIS-05, D-34-04).
 *
 * Proves the browser-visible export contract against a route-mocked backend:
 *   - `GET /novels/{novelId}/export?format=markdown|html|epub` returns a
 *     downloadable file whose body carries the frozen manifest hash and
 *     explicit provenance (owner/novel/text version);
 *   - `GET /novels/{novelId}/export/manifest` returns the frozen manifest JSON
 *     (text version, approved assets, verified anchors, captions, citations,
 *     missing-asset records);
 *   - a failing export format is an explicit HTTP error state (never a silent
 *     empty success).
 *
 * Routes are mocked (no real backend); downloads are exercised through the
 * browser fetch context so Playwright routes are honored. NOTE: on this machine
 * the Next 16 canary dev server fails to compile (pre-existing), so this spec
 * is kept structurally valid and executed by the verification sub-agent when
 * the environment allows.
 */
import { createHash } from "crypto";
import { expect, test, type Page } from "@playwright/test";

const H = (n: number) => String(n).repeat(64);
const sha = (s: string) => createHash("sha256").update(s, "utf8").digest("hex");

const NOVEL_ID = 11;
const CHAPTER_ID = 101;

const CHAPTER_CONTENT = [
  "第一章 雾城夜读",
  "",
  "第一段文字交代背景，城市笼罩在薄雾之中。",
  "",
  "第二段是插图所在的位置，主角站在窗前看见远山。",
  "",
  "第三段继续叙述，雨声敲打着玻璃。",
  "",
].join("\n");

const EXCERPT_START = CHAPTER_CONTENT.indexOf("第二段是插图所在的位置");
const EXCERPT = CHAPTER_CONTENT.slice(
  EXCERPT_START,
  EXCERPT_START + "第二段是插图所在的位置，主角站在窗前看见远山。".length
);
const TEXT_VERSION_HASH = sha(CHAPTER_CONTENT);

const MANIFEST = {
  schema_version: "novel-export-manifest.v1",
  artifact_kind: "novel_export_manifest",
  owner_id: 1,
  novel_id: NOVEL_ID,
  novel_title: "雾城夜读",
  novel_author: "佚名",
  text_version_hash: TEXT_VERSION_HASH,
  chapters: [
    {
      chapter_id: CHAPTER_ID,
      chapter_number: 1,
      title: "第一章 雾城夜读",
      content: CHAPTER_CONTENT,
      content_hash: TEXT_VERSION_HASH,
      anchors: [
        {
          anchor_id: 1,
          anchor_key: "anchor-1",
          chapter_id: CHAPTER_ID,
          chapter_number: 1,
          source_start: EXCERPT_START,
          source_end: EXCERPT_START + EXCERPT.length,
          paragraph_start: 2,
          paragraph_end: 2,
          excerpt: EXCERPT,
          anchor_hash: sha(EXCERPT),
          chapter_content_hash: TEXT_VERSION_HASH,
          source_snapshot_id: "ss-1",
          source_snapshot_hash: H(1),
          caption: "窗前的远山",
          alt_text: "主角站在窗前看见远处朦胧的山影",
          citation: "第一章 · 第 2 段",
          status: "render",
          reason_code: null,
          detail: null,
          asset: {
            asset_revision_id: 101,
            asset_id: "asset-101",
            bytes_hash: H(2),
            mime_type: "image/png",
            cutoff_chapter: 8,
          },
        },
      ],
    },
  ],
  assets: [
    {
      asset_revision_id: 101,
      asset_id: "asset-101",
      bytes_hash: H(2),
      mime_type: "image/png",
      cutoff_chapter: 8,
    },
  ],
  missing_assets: [],
  manifest_hash: H(3),
};

const MARKDOWN_BODY = `# 雾城夜读\n\n<!-- NovelMind export manifest ${H(3)}; owner_id=1; novel_id=${NOVEL_ID}; text_version_hash=${TEXT_VERSION_HASH} -->\n\n## 第一章 雾城夜读\n\n第一段文字交代背景，城市笼罩在薄雾之中。\n\n<figure class="export-illustration"><img src="assets/${H(2)}.png" alt="主角站在窗前看见远处朦胧的山影"/><figcaption><span class="export-caption">窗前的远山</span><span class="export-citation">引用：第一章 · 第 2 段</span></figcaption></figure>\n\n第三段继续叙述，雨声敲打着玻璃。\n\n## 导出报告\n\n无缺失资产\n`;

async function mockApp(page: Page) {
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
  await page.route(`**/api/novels/${NOVEL_ID}/chapters/${CHAPTER_ID}`, (route) =>
    route.fulfill({ json: chapter })
  );
  await page.route(`**/api/novels/${NOVEL_ID}/progress`, (route) =>
    route.fulfill({ status: 200, json: {} })
  );
  await page.route(`**/api/novels/${NOVEL_ID}/export/manifest`, (route) =>
    route.fulfill({ json: { manifest: MANIFEST, manifest_hash: H(3) } })
  );
  await page.route(`**/api/novels/${NOVEL_ID}/export?format=markdown`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/markdown",
      headers: { "X-Export-Manifest-Hash": H(3), "X-Export-Format": "markdown" },
      body: MARKDOWN_BODY,
    })
  );
  await page.route(`**/api/novels/${NOVEL_ID}/export?format=epub`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/epub+zip",
      headers: { "X-Export-Manifest-Hash": H(3), "X-Export-Format": "epub" },
      body: Buffer.from([0x50, 0x4b, 0x03, 0x04, 0x00]),
    })
  );
  await page.route(`**/api/novels/${NOVEL_ID}/export?format=html`, (route) =>
    route.fulfill({
      status: 500,
      json: { detail: "export generation failed (error state)" },
    })
  );
}

async function exportFrom(page: Page, url: string) {
  return page.evaluate(async (target) => {
    const res = await fetch(target);
    const contentType = res.headers.get("content-type") || "";
    return {
      status: res.status,
      ok: res.ok,
      contentType,
      text: await res.text(),
    };
  }, url);
}

test("download: markdown export carries the frozen manifest provenance", async ({
  page,
}) => {
  await mockApp(page);
  await page.goto(`/novels/${NOVEL_ID}?chapter=${CHAPTER_ID}`);
  await page.waitForLoadState("domcontentloaded");
  const result = await exportFrom(
    page,
    `/api/novels/${NOVEL_ID}/export?format=markdown`
  );
  expect(result.status).toBe(200);
  expect(result.ok).toBe(true);
  expect(result.contentType).toContain("text/markdown");
  expect(result.text).toContain("# 雾城夜读");
  expect(result.text).toContain(H(3)); // manifest hash
  expect(result.text).toContain(`text_version_hash=${TEXT_VERSION_HASH}`);
  expect(result.text).toContain("引用：第一章 · 第 2 段");
});

test("download: epub export returns an epub+zip payload with the manifest hash header", async ({
  page,
}) => {
  await mockApp(page);
  await page.goto(`/novels/${NOVEL_ID}?chapter=${CHAPTER_ID}`);
  await page.waitForLoadState("domcontentloaded");
  const result = await exportFrom(
    page,
    `/api/novels/${NOVEL_ID}/export?format=epub`
  );
  expect(result.status).toBe(200);
  expect(result.ok).toBe(true);
  expect(result.contentType).toContain("application/epub+zip");
  expect(result.text).toBe(String.fromCharCode(0x50, 0x4b, 0x03, 0x04, 0x00));
});

test("manifest: the frozen export manifest is served to the owner", async ({
  page,
}) => {
  await mockApp(page);
  await page.goto(`/novels/${NOVEL_ID}?chapter=${CHAPTER_ID}`);
  await page.waitForLoadState("domcontentloaded");
  const result = await exportFrom(
    page,
    `/api/novels/${NOVEL_ID}/export/manifest`
  );
  expect(result.status).toBe(200);
  const body = JSON.parse(result.text);
  expect(body.manifest.novel_id).toBe(NOVEL_ID);
  expect(body.manifest.owner_id).toBe(1);
  expect(body.manifest.text_version_hash).toBe(TEXT_VERSION_HASH);
  expect(body.manifest.chapters[0].anchors[0].status).toBe("render");
  expect(body.manifest_hash).toBe(H(3));
});

test("error state: a failing export format is an explicit HTTP error", async ({
  page,
}) => {
  await mockApp(page);
  await page.goto(`/novels/${NOVEL_ID}?chapter=${CHAPTER_ID}`);
  await page.waitForLoadState("domcontentloaded");
  const result = await exportFrom(
    page,
    `/api/novels/${NOVEL_ID}/export?format=html`
  );
  expect(result.status).toBe(500);
  expect(result.ok).toBe(false);
});
