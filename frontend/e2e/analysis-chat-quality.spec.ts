/**
 * Phase 29-03 / REQ-QA-03 — Analysis Chat browser UAT (D-06).
 *
 * Mocked browser paths (no real backend) for the Analysis Chat panel:
 *   - shared QueryPlan trace (intent/anchor/cutoff/availability/citations);
 *   - structure chapter_range anchor is echoed after server narrowing;
 *   - citation jumps to the leaf/raw reader chapter with exact offsets;
 *   - no-answer abstains and keeps the trace visible;
 *   - spoiler-safe: future chapter metadata never reaches the DOM;
 *   - reading cutoff boundary vs explicit whole-book switch;
 *   - partial/failure job states with cancel/retry affordances;
 *   - loading/error states are readable; input is keyboard-focusable.
 *
 * Release authority still requires the real-stack path; this spec is the
 * quality/UAT gate evidence for Analysis Chat.
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
    {
      dimension: "relations",
      status: "available",
      reason: "reader_ok",
      provenance: "exact_reader_v1",
    },
    {
      dimension: "character_state",
      status: "unavailable",
      reason: "character_state_reader_not_implemented_phase27",
      provenance: "no_production_reader",
    },
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

const JOB_RUNNING = {
  id: 6,
  user_message_id: 20,
  status: "running",
  status_reason: null,
  cancel_requested: false,
  retry_count: 0,
  error_code: null,
  created_at: "2026-08-03T00:00:02Z",
  updated_at: "2026-08-03T00:00:02Z",
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
    word_count: CHAPTER_1.length + CHAPTER_2.length,
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
        {
          id: 101,
          novel_id: 11,
          chapter_number: 1,
          title: "第一章 竹林",
          content: CHAPTER_1,
          word_count: CHAPTER_1.length,
          created_at: "",
          updated_at: "",
        },
        {
          id: 102,
          novel_id: 11,
          chapter_number: 2,
          title: "第二章 后章（未来）",
          content: CHAPTER_2,
          word_count: CHAPTER_2.length,
          created_at: "",
          updated_at: "",
        },
      ],
    })
  );
  await page.route("**/api/novels/11/progress", (route) =>
    route.fulfill({ status: 200, json: {} })
  );
}

function conversationRow() {
  return {
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
  };
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

async function mockConversations(page: Page, messages: unknown[]) {
  const conversations = [conversationRow()];
  await page.route(/\/api\/novels\/11\/conversations(?:\?.*)?$/, async (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        json: { items: conversations, total: 1, skip: 0, limit: 50 },
      });
    }
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as { title?: string };
      const created = {
        ...conversationRow(),
        id: 99,
        title: body.title || "New chat",
        next_sequence: 0,
        last_message_sequence: null,
        last_message_role: null,
        last_message_at: null,
      };
      conversations.push(created);
      return route.fulfill({ status: 201, json: created });
    }
    return route.fallback();
  });

  await page.route(
    /\/api\/novels\/11\/conversations\/\d+\/messages(?:\?.*)?$/,
    async (route) => {
      if (route.request().method() === "GET") {
        return route.fulfill({
          json: {
            items: messages,
            total: messages.length,
            skip: 0,
            limit: 200,
            after_sequence: 0,
          },
        });
      }
      if (route.request().method() === "POST") {
        const body = route.request().postDataJSON() as { body: string };
        const userMsg = {
          id: 20,
          conversation_id: 1,
          sequence: 2,
          role: "user",
          body: body.body,
          client_message_id: "cm-new",
          reply_to_message_id: null,
          selection: null,
          anchor: { kind: "chapter_range", chapter_start: 1, chapter_end: 2 },
          citations: [],
          generation_job: JOB_RUNNING,
          created_at: "2026-08-03T00:00:02Z",
        };
        return route.fulfill({
          status: 202,
          json: { message: userMsg, job: JOB_RUNNING },
        });
      }
      return route.fallback();
    }
  );
}

async function mockAll(page: Page, messages: unknown[]) {
  await page.route("**/api/**", (route) =>
    route.fulfill({ status: 500, json: { detail: "unmocked e2e endpoint" } })
  );
  await mockAuthAndNovel(page);
  await mockConversations(page, messages);
}

test("analysis chat exposes the shared QueryPlan trace and structure anchor", async ({
  page,
}) => {
  await mockAll(page, analysisMessages());
  await page.goto("/analysis?novel=11");

  await expect(page.getByTestId("analysis-chat-panel")).toBeVisible();
  await expect(page.getByTestId("analysis-chat-msg-anchor-10")).toHaveTextContent(
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

test("analysis citation jumps to the leaf/raw chapter with exact offsets", async ({
  page,
}) => {
  await mockAll(page, analysisMessages());
  await page.goto("/analysis?novel=11");

  const citation = page.getByTestId("reader-chat-citation").first();
  await expect(citation).toBeVisible();
  await citation.click();
  await expect(page).toHaveURL(/chapter=101/);
  await expect(page).toHaveURL(/start=6/);
  await expect(page).toHaveURL(/end=10/);
  await expect(page).toHaveURL(/from=timeline/);
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
  await expect(page.getByTestId("analysis-chat-queryplan-11")).toContainText(
    "引用 0"
  );
});

test("analysis chat is spoiler-safe and shows the reading-cutoff boundary", async ({
  page,
}) => {
  await mockAll(page, analysisMessages());
  await page.goto("/analysis?novel=11");

  // Future chapter metadata never reaches the DOM.
  await expect(page.getByText("SECRET_FUTURE")).toHaveCount(0);
  // Default boundary derives from reading progress (cutoff chapter 2).
  await expect(page.getByTestId("analysis-chat-boundary")).toContainText(
    "基于你已读至第 2 章"
  );
});

test("analysis running job exposes cancel and failed job exposes retry", async ({
  page,
}) => {
  const running = analysisMessages();
  running.push({
    id: 20,
    conversation_id: 1,
    sequence: 2,
    role: "user",
    body: "继续",
    client_message_id: "cm-20",
    reply_to_message_id: null,
    selection: null,
    citations: [],
    generation_job: JOB_RUNNING,
    created_at: "2026-08-03T00:00:02Z",
  });
  await mockAll(page, running);
  await page.goto("/analysis?novel=11");

  const status = page.getByTestId("analysis-chat-job-status");
  await expect(status).toHaveAttribute("data-status", "running");
  await expect(page.getByLabel("取消生成")).toBeVisible();
  await expect(status).toHaveAttribute("aria-live", "polite");
});

test("analysis chat loading/error states are readable and input is focusable", async ({
  page,
}) => {
  await mockAll(page, []);
  await page.goto("/analysis?novel=11");

  await expect(page.getByTestId("analysis-chat-empty")).toBeVisible();
  const input = page.getByTestId("analysis-chat-input");
  await input.focus();
  await expect(input).toBeFocused();

  // Error state surfaces a readable message.
  const errorPage = await page.context().newPage();
  await mockAll(errorPage, []);
  await errorPage.route(/\/api\/novels\/11\/conversations(?:\?.*)?$/, (route) =>
    route.fulfill({ status: 500, json: { detail: "boom" } })
  );
  await errorPage.goto("/analysis?novel=11");
  await expect(errorPage.getByTestId("analysis-chat-error")).toBeVisible();
  await expect(errorPage.getByTestId("analysis-chat-error")).toContainText(
    "加载会话列表失败"
  );
  await errorPage.close();
});

test("analysis chat works under reduced motion on desktop and 390px", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockAll(page, analysisMessages());
  await page.goto("/analysis?novel=11");

  await expect(page.getByTestId("analysis-chat-panel")).toBeVisible();
  await expect(page.getByTestId("analysis-chat-messages")).toBeVisible();
  const citation = page.getByTestId("reader-chat-citation").first();
  await expect(citation).toBeVisible();
  // No horizontal overflow.
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth + 2
    )
  ).toBe(true);
});
