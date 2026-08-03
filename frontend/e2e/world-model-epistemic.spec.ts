/**
 * Phase 27-04 — World-model epistemic authority / disclosure / evidence gate.
 *
 * Proves the browser-visible consequences of REQ-WM-04 (D-01/D-02/D-05/D-06):
 *   - the analysis chat renders the world projection panel with four distinct
 *     authority labels (no silent upgrade to canon_fact);
 *   - disclosure timing (已知于第 N 章 · 第 N 章后披露) is visible;
 *   - approved claims carry a leaf evidence jump; candidate-only claims do not;
 *   - user interpretation is isolated into the override section and never
 *     merged with the candidate items;
 *   - an unavailable projection is explicit (弃权), never empty-success;
 *   - no active-pointer / promotion UI exists.
 *
 * Note: mocked (no real backend). Runs under the desktop + mobile browser
 * matrix via playwright.config.ts.
 */
import { expect, test, type Page } from "@playwright/test";

const CHAPTER_1 = "第一章正文：林安走进竹林，月光洒在青石上。远处传来脚步声。";

const LEAF_EVIDENCE_KEY = "qp:101:6:10:abcd1234";
const LEAF_HASH = "abcd1234";

function worldProjectionPayload(over: Record<string, unknown> = {}) {
  return {
    schema_version: "world-model-projection.v1",
    available: true,
    status: "available",
    cutoff: 2,
    authorities: ["canon_fact", "probable_inference"],
    items: [
      {
        claim_key: "k-canon",
        kind: "character",
        subject: "lin-an",
        aspect: "knowledge",
        proposition: "林安是临安城主",
        authority: "canon_fact",
        known_at: 1,
        disclosure_cutoff: 2,
        pov: "omniscient",
        gate_status: "passed",
        approved: true,
        is_override: false,
        evidence_key: LEAF_EVIDENCE_KEY,
        chapter_id: 101,
        chapter_number: 1,
        source_start: 6,
        source_end: 10,
        content_hash: LEAF_HASH,
        source_snapshot_hash: "c".repeat(64),
        lineage: ["k-canon"],
      },
      {
        claim_key: "k-candidate",
        kind: "character",
        subject: "lin-an",
        aspect: "motivation",
        proposition: "林安谋求城主之位",
        authority: "probable_inference",
        known_at: 2,
        disclosure_cutoff: 2,
        pov: "lin-an",
        gate_status: "pending",
        approved: false,
        is_override: false,
        evidence_key: LEAF_EVIDENCE_KEY,
        chapter_id: 101,
        chapter_number: 1,
        source_start: 6,
        source_end: 10,
        content_hash: LEAF_HASH,
        source_snapshot_hash: "c".repeat(64),
        lineage: ["k-candidate"],
      },
    ],
    overrides: [
      {
        claim_key: "k-user-read",
        kind: "character",
        subject: "lin-an",
        aspect: "motivation",
        proposition: "读者认为林安另有隐情",
        authority: "user_interpretation",
        known_at: 1,
        disclosure_cutoff: 1,
        pov: "lin-an",
        gate_status: "passed",
        approved: true,
        is_override: true,
        evidence_key: LEAF_EVIDENCE_KEY,
        chapter_id: 101,
        chapter_number: 1,
        source_start: 6,
        source_end: 10,
        content_hash: LEAF_HASH,
        source_snapshot_hash: "c".repeat(64),
        lineage: ["k-user-read"],
      },
    ],
    manifest_checksum: "c".repeat(64),
    snapshot_hash: "c".repeat(64),
    ...over,
  };
}

function queryplanWithWorld(over: Record<string, unknown> = {}) {
  return {
    trace_id: "a".repeat(32),
    plan_hash: "b".repeat(64),
    intent: "analysis",
    anchor_kind: "chapter_range",
    cutoff_mode: "reading_progress",
    through_chapter: 2,
    full_book_authorized: false,
    availability: [
      { dimension: "world_projection", status: "available", reason: "reader_ok", provenance: "world_projection_reader_v1" },
    ],
    fallback: { chain: ["exact_reader", "deterministic_heuristic", "stable_unavailable"] },
    manifest_checksum: "c".repeat(64),
    allowed_evidence_ids: [LEAF_EVIDENCE_KEY],
    citation_jump: [
      {
        evidence_key: LEAF_EVIDENCE_KEY,
        chapter_id: 101,
        chapter_number: 1,
        source_start: 6,
        source_end: 10,
        excerpt: "走进竹林",
      },
    ],
    abstained: false,
    world_projection: worldProjectionPayload(over),
  };
}

async function mockAuthAndNovel(page: Page) {
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      json: { id: 1, username: "e2e", email: "e2e@example.com", is_active: true },
    })
  );
  const novel = {
    id: 11,
    title: "雾城夜读",
    author: "佚名",
    description: null,
    genre: null,
    word_count: CHAPTER_1.length,
    chapter_count: 2,
    status: "ready",
    reading_progress: { chapter_id: 101, progress_percent: 10 },
    created_at: "",
    updated_at: "",
  };
  await page.route("**/api/novels", (route) =>
    route.fulfill({ json: { items: [novel], total: 1 } })
  );
  await page.route("**/api/novels/11", (route) =>
    route.fulfill({ json: novel })
  );
  await page.route("**/api/novels/11/chapters", (route) =>
    route.fulfill({
      json: [
        { id: 101, novel_id: 11, chapter_number: 1, title: "第一章 竹林", content: CHAPTER_1, word_count: CHAPTER_1.length, created_at: "", updated_at: "" },
      ],
    })
  );
  await page.route("**/api/novels/11/chapters/101", (route) =>
    route.fulfill({ json: { id: 101, novel_id: 11, chapter_number: 1, title: "第一章 竹林", content: CHAPTER_1, word_count: CHAPTER_1.length, created_at: "", updated_at: "" } })
  );
  await page.route("**/api/novels/11/progress", (route) =>
    route.fulfill({ status: 200, json: {} })
  );
}

async function mockAnalysisMessages(page: Page, messages: unknown[]) {
  const conversations = [
    {
      id: 1,
      novel_id: 11,
      title: "默认会话",
      status: "active",
      next_sequence: 2,
      last_opened_at: null,
      created_at: "2026-08-03T00:00:00Z",
      updated_at: "2026-08-03T00:00:00Z",
      last_message_sequence: 1,
      last_message_role: "assistant",
      last_message_at: "2026-08-03T00:00:01Z",
    },
  ];
  await page.route(/\/api\/novels\/11\/conversations(?:\?.*)?$/, async (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({ json: { items: conversations, total: 1, skip: 0, limit: 50 } });
    }
    return route.fallback();
  });
  await page.route(/\/api\/novels\/11\/conversations\/\d+\/messages(?:\?.*)?$/, (route) =>
    route.fulfill({
      json: { items: messages, total: messages.length, skip: 0, limit: 200, after_sequence: 0 },
    })
  );
}

async function mockAll(page: Page, messages: unknown[]) {
  await page.route("**/api/**", (route) =>
    route.fulfill({ status: 500, json: { detail: "unmocked e2e endpoint" } })
  );
  await mockAuthAndNovel(page);
  await mockAnalysisMessages(page, messages);
}

function assistantMessages(over: Record<string, unknown> = {}) {
  return [
    {
      id: 10,
      conversation_id: 1,
      sequence: 0,
      role: "user",
      body: "林安知道什么？",
      client_message_id: "cm-1",
      reply_to_message_id: null,
      selection: null,
      anchor: { kind: "chapter_range", chapter_start: 1, chapter_end: 2 },
      citations: [],
      generation_job: null,
      created_at: "2026-08-03T00:00:00Z",
    },
    {
      id: 11,
      conversation_id: 1,
      sequence: 1,
      role: "assistant",
      body: "主线围绕临安城主展开。",
      client_message_id: null,
      reply_to_message_id: 10,
      selection: null,
      citations: [],
      generation_job: null,
      queryplan: queryplanWithWorld(over),
      created_at: "2026-08-03T00:00:01Z",
    },
  ];
}

test("analysis chat shows authority labels, disclosure and evidence jump", async ({
  page,
}) => {
  await mockAll(page, assistantMessages());
  await page.goto("/analysis?novel=11");

  await expect(page.getByTestId("analysis-chat-panel")).toBeVisible();
  const panel = page.getByTestId("world-model-evidence-panel");
  await expect(panel).toBeVisible();
  await expect(panel).toHaveAttribute("data-status", "available");

  // Four distinct authority labels never collapse into canon_fact (D-01).
  const authorities = await panel
    .locator('[data-testid="world-model-authority-badge"]')
    .evaluateAll((els) =>
      els.map((el) => el.getAttribute("data-authority"))
    );
  expect(new Set(authorities)).toEqual(
    new Set(["canon_fact", "probable_inference", "user_interpretation"])
  );

  // Disclosure timing (D-05).
  await expect(panel).toContainText("披露截止：第 2 章");
  await expect(panel).toContainText("已知于第 1 章 · 第 2 章后披露");

  // Approved claim has a leaf jump; candidate-only does not (D-08/D-02).
  await expect(panel.getByTestId("reader-chat-citation")).toHaveCount(1);
  await expect(panel.getByTestId("world-model-candidate-only")).toHaveCount(1);
});

test("world-model evidence jump lands on the reader chapter", async ({ page }) => {
  await mockAll(page, assistantMessages());
  await page.goto("/analysis?novel=11");

  const panel = page.getByTestId("world-model-evidence-panel");
  await expect(panel).toBeVisible();
  await panel.getByTestId("reader-chat-citation").click();
  await expect(page).toHaveURL(/chapter=101&start=6/);
});

test("user interpretation is isolated from the candidate projection", async ({
  page,
}) => {
  await mockAll(page, assistantMessages());
  await page.goto("/analysis?novel=11");

  const panel = page.getByTestId("world-model-evidence-panel");
  await expect(panel).toBeVisible();
  const overrides = panel.getByTestId("world-model-overrides");
  await expect(overrides).toBeVisible();
  await expect(overrides).toContainText("用户解读");
  await expect(overrides).toContainText("读者认为林安另有隐情");
  // The override is not in the candidate list.
  await expect(panel.getByTestId("world-model-candidate-items")).not.toContainText(
    "读者认为林安另有隐情"
  );
});

test("unavailable world projection abstains, never empty-success", async ({
  page,
}) => {
  const messages = assistantMessages({
    available: false,
    status: "unavailable",
    authorities: [],
    items: [],
    overrides: [],
  });
  await mockAll(page, messages);
  await page.goto("/analysis?novel=11");

  const panel = page.getByTestId("world-model-evidence-panel");
  await expect(panel).toHaveAttribute("data-status", "unavailable");
  await expect(panel.getByTestId("world-model-empty-abstained")).toContainText(
    "未编造任何内容"
  );
});
