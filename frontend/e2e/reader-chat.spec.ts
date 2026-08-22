/**
 * Mocked reader-chat browser coverage (visual/layout states).
 * Release authority requires reader-chat-real.spec.ts (real stack).
 */
import { expect, test, type Page } from "@playwright/test";

const CHAPTER_CONTENT =
  "第一章正文：阿宁走进竹林，月光洒在青石上。远处传来脚步声。";

async function mockReaderChat(page: Page) {
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      json: {
        id: 1,
        username: "e2e",
        email: "e2e@example.com",
        is_active: true,
      },
    })
  );
  await page.route("**/api/novels/11", (route) =>
    route.fulfill({
      json: {
        id: 11,
        title: "雾城夜读",
        author: "佚名",
        description: null,
        genre: null,
        word_count: CHAPTER_CONTENT.length,
        chapter_count: 2,
        status: "ready",
        reading_progress: { chapter_id: 101, progress_percent: 10 },
        created_at: "",
        updated_at: "",
      },
    })
  );
  await page.route("**/api/novels/11/chapters", (route) =>
    route.fulfill({
      json: [
        {
          id: 101,
          novel_id: 11,
          chapter_number: 1,
          title: "第一章 竹林",
          content: CHAPTER_CONTENT,
          word_count: CHAPTER_CONTENT.length,
          created_at: "",
          updated_at: "",
        },
        {
          id: 102,
          novel_id: 11,
          chapter_number: 2,
          title: "第二章 后章（未来）",
          content: "后章隐藏内容SECRET_FUTURE不应在默认对话中出现",
          word_count: 20,
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
        content: CHAPTER_CONTENT,
        word_count: CHAPTER_CONTENT.length,
        created_at: "",
        updated_at: "",
      },
    })
  );
  await page.route("**/api/novels/11/chapters/102", (route) =>
    route.fulfill({
      json: {
        id: 102,
        novel_id: 11,
        chapter_number: 2,
        title: "第二章 后章（未来）",
        content: "后章隐藏内容SECRET_FUTURE不应在默认对话中出现",
        word_count: 20,
        created_at: "",
        updated_at: "",
      },
    })
  );
  await page.route("**/api/novels/11/progress", (route) =>
    route.fulfill({ status: 200, json: {} })
  );

  type MockConversation = {
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
  };

  const conversations: MockConversation[] = [
    {
      id: 1,
      novel_id: 11,
      title: "默认会话",
      status: "active",
      next_sequence: 2,
      last_opened_at: null,
      created_at: "2026-07-15T00:00:00Z",
      updated_at: "2026-07-15T00:00:00Z",
      last_message_sequence: 1,
      last_message_role: "assistant",
      last_message_at: "2026-07-15T00:00:01Z",
    },
  ];

  await page.route(/\/api\/novels\/11\/conversations(?:\?.*)?$/, async (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        json: { items: conversations, total: conversations.length, skip: 0, limit: 50 },
      });
    }
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as { title?: string };
      const created = {
        id: 99,
        novel_id: 11,
        title: body.title || "New chat",
        status: "active",
        next_sequence: 0,
        last_opened_at: null,
        created_at: "2026-07-15T00:00:00Z",
        updated_at: "2026-07-15T00:00:00Z",
        last_message_sequence: null,
        last_message_role: null,
        last_message_at: null,
      };
      conversations.push(created);
      return route.fulfill({ status: 201, json: created });
    }
    return route.fallback();
  });

  await page.route(/\/api\/novels\/11\/conversations\/\d+\/messages(?:\?.*)?$/, async (route) => {
    if (route.request().method() === "GET") {
      return route.fulfill({
        json: {
          items: [
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
                created_at: "2026-07-15T00:00:00Z",
                updated_at: "2026-07-15T00:00:01Z",
              },
              created_at: "2026-07-15T00:00:00Z",
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
              citations: [
                {
                  block_id: "b1",
                  evidence_key: "selection:101:6:10",
                  context_evidence_ref_id: 1,
                  chapter_id: 101,
                  source_start: 6,
                  source_end: 10,
                },
              ],
              generation_job: null,
              created_at: "2026-07-15T00:00:01Z",
            },
          ],
          total: 2,
          skip: 0,
          limit: 200,
          after_sequence: 0,
        },
      });
    }
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as {
        body: string;
        selection: { source_start: number; source_end: number; selection_text: string };
      };
      return route.fulfill({
        status: 202,
        json: {
          message: {
            id: 20,
            conversation_id: 1,
            sequence: 2,
            role: "user",
            body: body.body,
            client_message_id: "cm-new",
            reply_to_message_id: null,
            selection: {
              chapter_id: 101,
              source_start: body.selection.source_start,
              source_end: body.selection.source_end,
              selection_text_hash: "c".repeat(64),
              chapter_content_hash: "d".repeat(64),
            },
            citations: [],
            generation_job: {
              id: 6,
              user_message_id: 20,
              status: "queued",
              status_reason: null,
              cancel_requested: false,
              retry_count: 0,
              error_code: null,
              created_at: "2026-07-15T00:00:02Z",
              updated_at: "2026-07-15T00:00:02Z",
            },
            created_at: "2026-07-15T00:00:02Z",
          },
          job: {
            id: 6,
            user_message_id: 20,
            status: "queued",
            status_reason: null,
            cancel_requested: false,
            retry_count: 0,
            error_code: null,
            created_at: "2026-07-15T00:00:02Z",
            updated_at: "2026-07-15T00:00:02Z",
          },
        },
      });
    }
    return route.fallback();
  });
}

test("reader chat panel is collapsible and does not replace the reader", async ({
  page,
}, testInfo) => {
  await mockReaderChat(page);
  await page.goto("/novels/11");
  await expect(page.getByRole("heading", { name: "第一章 竹林" })).toBeVisible();

  await page.getByTestId("reader-chat-open").click();
  await expect(page.getByTestId("reader-chat-panel")).toBeVisible();

  // Reading column remains present and scrollable
  const scrollCol = page.getByTestId("reader-scroll-column");
  await expect(scrollCol).toBeVisible();
  await expect(page.getByTestId("reader-page-text")).toContainText("阿宁走进竹林");

  if (testInfo.project.name === "chromium-desktop") {
    await expect(page.getByTestId("reader-chat-column")).toBeVisible();
    // Desktop chat column is a sibling — main text not covered by fixed full-screen chat
    const panelBox = await page.getByTestId("reader-chat-panel").boundingBox();
    const textBox = await page.getByTestId("reader-page-text").boundingBox();
    expect(panelBox && textBox).toBeTruthy();
    if (panelBox && textBox) {
      // Panel is to the right of text (non-overlapping x ranges)
      expect(panelBox.x).toBeGreaterThanOrEqual(textBox.x + textBox.width - 4);
    }
  }

  if (testInfo.project.name !== "chromium-desktop") {
    await expect(page.getByTestId("reader-chat-panel")).toHaveAttribute(
      "data-layout",
      "mobile"
    );
    const viewportHeight = page.viewportSize()?.height ?? 844;
    const panel = page.getByTestId("reader-chat-panel");
    const box = await panel.boundingBox();
    expect(box).toBeTruthy();
    if (box) {
      expect(box.height).toBeLessThanOrEqual(viewportHeight * 0.5 + 20);
    }
    await page.getByLabel("收起对话").click();
    await expect(page.getByTestId("reader-chat-chip")).toBeVisible();
    // After collapse, reader text still scrollable
    await expect(page.getByTestId("reader-page-text")).toBeVisible();
  }

  // Citation jump — 先确保面板处于打开状态（桌面端上一步未收起，移动端刚收起）。
  // 注意 reader-chat-open 是开关：collapsed 时 chatOpen 仍为 true，点它会整体关闭；
  // 移动端必须通过 chip 展开，只有面板与 chip 都不存在时才用 open 按钮。
  if (await page.getByTestId("reader-chat-chip").isVisible().catch(() => false)) {
    await page.getByTestId("reader-chat-chip").click();
  } else if (
    !(await page.getByTestId("reader-chat-panel").isVisible().catch(() => false))
  ) {
    await page.getByTestId("reader-chat-open").click().catch(() => {});
  }
  await expect(page.getByTestId("reader-chat-citation")).toBeVisible();
  await page.getByTestId("reader-chat-citation").click();
  await expect(page.getByTestId("reader-citation-highlight")).toBeVisible();

  // No full-screen chat replacement of reader shell
  await expect(page.getByTestId("reader-scroll-column")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 2
    )
  ).toBe(true);

  // No Phase 11 clue product UI
  await expect(page.getByText(/线索追踪|伏笔/)).toHaveCount(0);
});

test("selection action binds offsets and opens chat", async ({ page }) => {
  await mockReaderChat(page);
  await page.goto("/novels/11");
  await expect(page.getByTestId("reader-page-text")).toBeVisible();

  // Programmatically select text inside reader page
  await page.evaluate(() => {
    const root = document.querySelector('[data-testid="reader-page-text"]');
    if (!root) return;
    // Paragraph blocks are wrapped in divs; walk the DOM to the first text
    // node that actually contains the selection target.
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let text: Text | null = null;
    while (walker.nextNode()) {
      const node = walker.currentNode as Text;
      if (node.data.includes("阿宁走进")) {
        text = node;
        break;
      }
    }
    if (!text) return;
    const idx = text.data.indexOf("阿宁走进");
    const range = document.createRange();
    range.setStart(text, idx);
    range.setEnd(text, idx + 4);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(range);
    document.dispatchEvent(new Event("selectionchange"));
    document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  });

  await expect(page.getByTestId("reader-selection-action")).toBeVisible({
    timeout: 5000,
  });
  await page.getByTestId("reader-selection-action").click();
  await expect(page.getByTestId("reader-chat-panel")).toBeVisible();
  await expect(page.getByTestId("reader-chat-selection-preview")).toContainText(
    "阿宁"
  );
});
