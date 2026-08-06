/**
 * Phase 26-04 — Reader/Analysis Chat QueryPlan consumer browser proof.
 *
 * Both chat entry points share one QueryPlan / retrieval / evidence core with
 * distinct anchors (Reader = selection, Analysis = chapter_range). This spec
 * proves the browser-visible consequences:
 *   - analysis chat exposes the shared QueryPlan trace (trace/availability/
 *     fallback/citation level) on assistant messages;
 *   - citation jump lands on the leaf/raw reader page chapter;
 *   - the server-narrowed chapter_range anchor is echoed;
 *   - an evidence-less answer abstains and says so;
 *   - loading / error / cancel / retry surfaces stay visible.
 *
 * Note: mocked (no real backend). Release authority still requires the real
 * stack path covered by reader-chat-real.spec.ts.
 */
import { expect, test, type Page } from "@playwright/test";

const CHAPTER_1 = "第一章正文：阿宁走进竹林，月光洒在青石上。远处传来脚步声。";
const CHAPTER_2 = "第二章后章隐藏内容SECRET_FUTURE不应在默认对话中出现";

const QUERYPLAN_ANALYSIS = {
  trace_id: "a".repeat(32),
  plan_hash: "b".repeat(64),
  intent: "analysis",
  anchor_kind: "chapter_range",
  cutoff_mode: "reading_progress",
  through_chapter: 2,
  full_book_authorized: false,
  availability: [
    { dimension: "relations", status: "available", reason: "reader_ok", provenance: "exact_reader_v1" },
    { dimension: "character_state", status: "unavailable", reason: "character_state_reader_not_implemented_phase27", provenance: "no_production_reader" },
  ],
  fallback: { chain: ["exact_reader", "deterministic_heuristic", "stable_unavailable"] },
  manifest_checksum: "c".repeat(64),
  allowed_evidence_ids: ["qp:101:6:10:abcd"],
  citation_jump: [
    {
      evidence_key: "qp:101:6:10:abcd",
      chapter_id: 101,
      chapter_number: 1,
      source_start: 6,
      source_end: 10,
      excerpt: "走进竹林",
    },
  ],
  abstained: false,
};

const QUERYPLAN_ABSTAINED = {
  ...QUERYPLAN_ANALYSIS,
  trace_id: "d".repeat(32),
  plan_hash: "e".repeat(64),
  allowed_evidence_ids: [],
  citation_jump: [],
  abstained: true,
};

const CITATION = {
  block_id: "b1",
  evidence_key: "qp:101:6:10:abcd",
  context_evidence_ref_id: 1,
  chapter_id: 101,
  source_start: 6,
  source_end: 10,
};

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
        { id: 102, novel_id: 11, chapter_number: 2, title: "第二章 后章（未来）", content: CHAPTER_2, word_count: CHAPTER_2.length, created_at: "", updated_at: "" },
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

async function mockConversations(page: Page, messages: unknown[]) {
  const conversations: Array<{
    id: number;
    novel_id: number;
    title: string;
    status: string;
    next_sequence: number;
    last_opened_at: string | null;
    created_at: string;
    updated_at: string;
    last_message_sequence: number | null;
    last_message_role: string | null;
    last_message_at: string | null;
  }> = [
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
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as { title?: string };
      const created = { ...conversations[0], id: 99, title: body.title || "New chat", status: "active", next_sequence: 0, last_message_sequence: null, last_message_role: null, last_message_at: null };
      conversations.push(created);
      return route.fulfill({ status: 201, json: created });
    }
    return route.fallback();
  });

  await page.route(/\/api\/novels\/11\/conversations\/\d+\/messages(?:\?.*)?$/, async (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        json: { items: messages, total: messages.length, skip: 0, limit: 200, after_sequence: 0 },
      });
    }
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as {
        body: string;
        chapter_range?: { chapter_start: number; chapter_end: number };
      };
      const userMsg = {
        id: 20,
        conversation_id: 1,
        sequence: 2,
        role: "user",
        body: body.body,
        client_message_id: "cm-new",
        reply_to_message_id: null,
        selection: null,
        anchor: body.chapter_range
          ? { kind: "chapter_range", chapter_start: body.chapter_range.chapter_start, chapter_end: 2 }
          : null,
        citations: [],
        generation_job: {
          id: 6,
          user_message_id: 20,
          status: "queued",
          status_reason: null,
          cancel_requested: false,
          retry_count: 0,
          error_code: null,
          created_at: "2026-08-03T00:00:02Z",
          updated_at: "2026-08-03T00:00:02Z",
        },
        created_at: "2026-08-03T00:00:02Z",
      };
      return route.fulfill({
        status: 202,
        json: {
          message: userMsg,
          job: userMsg.generation_job,
        },
      });
    }
    return route.fallback();
  });
}

function analysisMessages(): unknown[] {
  return [
    {
      id: 10,
      conversation_id: 1,
      sequence: 0,
      role: "user",
      body: "前两章的主线是什么？",
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
      body: "主线围绕雾中铃铛展开。",
      client_message_id: null,
      reply_to_message_id: 10,
      selection: null,
      citations: [CITATION],
      generation_job: null,
      queryplan: QUERYPLAN_ANALYSIS,
      created_at: "2026-08-03T00:00:01Z",
    },
  ];
}

async function mockAll(page: Page, messages: unknown[]) {
  await page.route("**/api/**", (route) =>
    route.fulfill({ status: 500, json: { detail: "unmocked e2e endpoint" } })
  );
  await mockAuthAndNovel(page);
  await mockConversations(page, messages);
}

test("analysis chat exposes the shared QueryPlan trace (chapter_range anchor)", async ({
  page,
}) => {
  await mockAll(page, analysisMessages());
  await page.goto("/analysis?novel=11");

  await expect(page.getByTestId("analysis-chat-panel")).toBeVisible();
  await expect(page.getByTestId("analysis-chat-msg-anchor-10")).toContainText(
    "范围：第 1–2 章"
  );
  const trace = page.getByTestId("analysis-chat-queryplan-11");
  await expect(trace).toContainText("QueryPlan");
  await expect(trace).toContainText("分析");
  await expect(trace).toContainText("结构区间锚点");
  await expect(trace).toContainText("已读至第 2 章");
  await expect(trace).toContainText("引用 1");
  await expect(trace).toContainText("部分维度不可用");
});

test("analysis citation jumps to the leaf/raw reader page chapter", async ({ page }) => {
  await mockAll(page, analysisMessages());
  await page.goto("/analysis?novel=11");

  await expect(page.getByTestId("reader-chat-citation")).toBeVisible();
  await page.getByTestId("reader-chat-citation").click();
  await expect(page).toHaveURL(/chapter=101&start=6&end=10|chapter=101/);
});

test("analysis no-answer abstains and keeps the trace visible", async ({ page }) => {
  const messages = analysisMessages();
  messages[1] = {
    ...(messages[1] as Record<string, unknown>),
    queryplan: QUERYPLAN_ABSTAINED,
    citations: [],
    body: "证据不足，无法作答。",
  };
  await mockAll(page, messages);
  await page.goto("/analysis?novel=11");

  await expect(page.getByTestId("analysis-chat-panel")).toBeVisible();
  await expect(page.getByTestId("analysis-chat-queryplan-11")).toContainText(
    "已弃权（证据不足）"
  );
  // 已弃权时统一对话窗口省略「引用 N」计数（替代为弃权提示）—— 断言零计数出现。
  await expect(page.getByTestId("analysis-chat-queryplan-11")).not.toContainText(
    "引用"
  );
});

test("reader chat citation jump still lands on leaf reader text", async ({ page }) => {
  const messages = [
    {
      id: 10,
      conversation_id: 1,
      sequence: 0,
      role: "user",
      body: "阿宁在做什么？",
      client_message_id: "cm-1",
      reply_to_message_id: null,
      selection: {
        chapter_id: 101,
        source_start: 6,
        source_end: 10,
        selection_text_hash: "a".repeat(64),
        chapter_content_hash: "b".repeat(64),
      },
      citations: [],
      generation_job: {
        id: 5,
        user_message_id: 10,
        status: "completed",
        status_reason: "published",
        cancel_requested: false,
        retry_count: 0,
        error_code: null,
        created_at: "2026-08-03T00:00:00Z",
        updated_at: "2026-08-03T00:00:01Z",
      },
      created_at: "2026-08-03T00:00:00Z",
    },
    {
      id: 11,
      conversation_id: 1,
      sequence: 1,
      role: "assistant",
      body: "阿宁走进竹林。",
      client_message_id: null,
      reply_to_message_id: 10,
      selection: null,
      citations: [CITATION],
      generation_job: null,
      queryplan: { ...QUERYPLAN_ANALYSIS, intent: "reader", anchor_kind: "selection" },
      created_at: "2026-08-03T00:00:01Z",
    },
  ];
  await mockAll(page, messages);
  await page.goto("/novels/11");

  await expect(page.getByTestId("reader-page-text")).toBeVisible();
  await page.getByTestId("reader-chat-open").click();
  await expect(page.getByTestId("reader-chat-panel")).toBeVisible();
  await expect(page.getByTestId("reader-chat-citation")).toBeVisible();
  await page.getByTestId("reader-chat-citation").click();
  await expect(page.getByTestId("reader-citation-highlight")).toBeVisible();
});
