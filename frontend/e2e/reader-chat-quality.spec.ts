/**
 * Phase 29-03 / REQ-QA-03 — Reader Chat browser UAT (D-06).
 *
 * Mocked browser paths (no real backend) that must pass on every project:
 *   - citation chips render and jump to the exact source text highlight;
 *   - evidence panel stays leaf/offset-valid (defensive filter);
 *   - partial/failure states: running → cancelled / failed → retry affordances;
 *   - desktop vs 390px mobile layout (no permanent overlay, height-bounded);
 *   - accessibility: keyboard focus, citation aria-labels, reduced motion;
 *   - spoiler-safe: future chapter metadata never reaches the DOM;
 *   - reading cutoff: only visible chapter context is offered.
 *
 * Release authority still requires the real-stack path covered by
 * reader-chat-real.spec.ts; this spec is the quality/UAT gate evidence.
 */
import { expect, test, type Page } from "@playwright/test";

const CHAPTER_1 =
  "第一章正文：阿宁走进竹林，月光洒在青石上。远处传来脚步声。";
const CHAPTER_2 =
  "第二章后章隐藏内容SECRET_FUTURE不应在默认对话中出现";

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
  await page.route("**/api/novels/11/chapters/101", (route) =>
    route.fulfill({
      json: {
        id: 101,
        novel_id: 11,
        chapter_number: 1,
        title: "第一章 竹林",
        content: CHAPTER_1,
        word_count: CHAPTER_1.length,
        created_at: "",
        updated_at: "",
      },
    })
  );
  await page.route("**/api/novels/11/progress", (route) =>
    route.fulfill({ status: 200, json: {} })
  );
}

function baseConversation(): {
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
} {
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

function message(id: number, role: "user" | "assistant", body: string) {
  return {
    id,
    conversation_id: 1,
    sequence: id,
    role,
    body,
    client_message_id: role === "user" ? `cm-${id}` : null,
    reply_to_message_id: role === "assistant" ? id - 1 : null,
    selection: null,
    citations: role === "assistant" ? [CITATION] : [],
    generation_job: null,
    created_at: "2026-08-03T00:00:01Z",
  };
}

async function mockConversations(page: Page, messages: unknown[]) {
  const conversations = [baseConversation()];
  await page.route(/\/api\/novels\/11\/conversations(?:\?.*)?$/, async (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        json: { items: conversations, total: 1, skip: 0, limit: 50 },
      });
    }
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as { title?: string };
      const created = {
        ...baseConversation(),
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

async function openReaderChat(page: Page) {
  await page.goto("/novels/11");
  await expect(page.getByTestId("reader-page-text")).toBeVisible();
  // The reader chat panel is a toggle. In a fresh context chatOpen is false,
  // but a second page in the same context inherits localStorage where a
  // previous test may have left open:true — clicking then would CLOSE the
  // panel. Open idempotently: only click when the panel is not yet visible.
  const panel = page.getByTestId("reader-chat-panel");
  if (!(await panel.isVisible().catch(() => false))) {
    await page.getByTestId("reader-chat-open").click();
    // Opening always expands the panel; on narrow desktop the rail may appear first.
    const rail = page.getByTestId("reader-chat-rail");
    if (await rail.isVisible().catch(() => false)) {
      await page.getByTestId("reader-chat-expand").click();
    }
  }
  await expect(panel).toBeVisible();
}

test("reader chat citation jumps to the exact source text and stays spoiler-safe", async ({
  page,
}, testInfo) => {
  await mockAll(page, [message(10, "user", "阿宁在做什么？"), message(11, "assistant", "阿宁走进竹林。")]);
  await openReaderChat(page);

  // Evidence panel: citation chip renders with valid leaf offsets.
  const citation = page.getByTestId("reader-chat-citation").first();
  await expect(citation).toBeVisible();
  await expect(citation).toHaveAttribute("data-source-start", "6");
  await expect(citation).toHaveAttribute("data-chapter-id", "101");
  await expect(citation).toHaveAccessibleName(/跳转到引用原文/);

  // Citation jump lands on the leaf/raw reader text.
  await citation.click();
  await expect(page.getByTestId("reader-citation-highlight")).toBeVisible();

  // Spoiler metadata: future chapter text must never reach the DOM.
  await expect(page.getByText("SECRET_FUTURE")).toHaveCount(0);

  // Desktop: chat column is a sibling (no permanent full-screen overlay).
  if (testInfo.project.name === "chromium-desktop") {
    await expect(page.getByTestId("reader-chat-column")).toBeVisible();
    const textBox = await page.getByTestId("reader-page-text").boundingBox();
    const panelBox = await page.getByTestId("reader-chat-panel").boundingBox();
    expect(textBox && panelBox).toBeTruthy();
    if (textBox && panelBox) {
      expect(panelBox.x + 2).toBeGreaterThanOrEqual(textBox.x + textBox.width - 8);
    }
  }

  // Mobile: panel is a height-bounded bottom sheet, never covers the reader.
  if (testInfo.project.name !== "chromium-desktop") {
    await expect(page.getByTestId("reader-chat-panel")).toHaveAttribute(
      "data-layout",
      "mobile"
    );
    const viewportHeight = page.viewportSize()?.height ?? 844;
    const box = await page.getByTestId("reader-chat-panel").boundingBox();
    expect(box?.height ?? 9999).toBeLessThanOrEqual(viewportHeight * 0.5 + 40);
    await expect(page.getByTestId("reader-page-text")).toBeVisible();
  }
});

test("reader chat running job exposes cancel and failure exposes retry", async ({
  page,
}) => {
  const running = {
    ...message(10, "user", "这一章的主线是什么？"),
    generation_job: JOB_RUNNING,
  };
  await mockAll(page, [running]);
  await openReaderChat(page);

  const status = page.getByTestId("reader-chat-job-status");
  await expect(status).toHaveAttribute("data-status", "running");
  await expect(status).toContainText("生成中");
  await expect(page.getByLabel("取消生成")).toBeVisible();
  await expect(status).toHaveAttribute("aria-live", "polite");

  // Failure path: same conversation replayed with a failed job shows retry.
  const failed = {
    ...running,
    generation_job: {
      ...JOB_RUNNING,
      status: "failed",
      status_reason: "structured_output_rejected",
      error_code: "structured_output_rejected",
    },
  };
  const failedPage = await page.context().newPage();
  await mockAll(failedPage, [failed]);
  await failedPage.goto("/novels/11");
  await expect(failedPage.getByTestId("reader-page-text")).toBeVisible();
  // Idempotent open: this page shares localStorage with `page`, where the
  // running scenario above left chatOpen:true, so the panel may already be
  // open and a blind click would close it.
  const failedPanel = failedPage.getByTestId("reader-chat-panel");
  if (!(await failedPanel.isVisible().catch(() => false))) {
    await failedPage.getByTestId("reader-chat-open").click();
    const rail = failedPage.getByTestId("reader-chat-rail");
    if (await rail.isVisible().catch(() => false)) {
      await failedPage.getByTestId("reader-chat-expand").click();
    }
  }
  await expect(failedPage.getByTestId("reader-chat-job-status")).toHaveAttribute(
    "data-status",
    "failed"
  );
  await expect(failedPage.getByLabel("重试生成")).toBeVisible();
  await failedPage.close();
});

test("reader chat loading/error states are understandable and focusable", async ({
  page,
}) => {
  await mockAll(page, []);
  await openReaderChat(page);

  // Input is focusable by keyboard (accessibility).
  const input = page.getByTestId("reader-chat-input");
  await input.focus();
  await expect(input).toBeFocused();

  // Loading state surfaces for a slow message replay.
  await page.route(
    /\/api\/novels\/11\/conversations\/\d+\/messages(?:\?.*)?$/,
    async (route) => {
      if (route.request().method() === "GET") {
        await new Promise((resolve) => setTimeout(resolve, 500));
        return route.fulfill({
          json: { items: [], total: 0, skip: 0, limit: 200, after_sequence: 0 },
        });
      }
      return route.fallback();
    }
  );
  // Close then reopen the panel so the messages refetch on the delayed route.
  await page.getByTestId("reader-chat-open").click();
  await page.getByTestId("reader-chat-open").click();
  await expect(page.getByText("加载消息…")).toBeVisible();

  // Error state: a failing list request surfaces a readable message.
  const errorPage = await page.context().newPage();
  await mockAll(errorPage, []);
  await errorPage.route(/\/api\/novels\/11\/conversations(?:\?.*)?$/, (route) =>
    route.fulfill({ status: 500, json: { detail: "boom" } })
  );
  await errorPage.goto("/novels/11");
  await expect(errorPage.getByTestId("reader-page-text")).toBeVisible();
  // Idempotent open (shared localStorage may already have open:true).
  const errorPanel = errorPage.getByTestId("reader-chat-panel");
  if (!(await errorPanel.isVisible().catch(() => false))) {
    await errorPage.getByTestId("reader-chat-open").click();
  }
  await expect(errorPage.getByTestId("reader-chat-error")).toBeVisible();
  await expect(errorPage.getByTestId("reader-chat-error")).toContainText(
    "加载会话列表失败"
  );
  await errorPage.close();
});

test("reader chat is usable under reduced motion with keyboard focus", async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await mockAll(page, [message(10, "user", "q"), message(11, "assistant", "阿宁走进竹林。")]);
  await openReaderChat(page);

  const citation = page.getByTestId("reader-chat-citation").first();
  await expect(citation).toBeVisible();
  // Keyboard activation (Enter) triggers the same citation jump as a click.
  await citation.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("reader-citation-highlight")).toBeVisible();
  // No horizontal overflow (mobile clipping guard).
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth + 2
    )
  ).toBe(true);
});
