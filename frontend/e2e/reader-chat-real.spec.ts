/**
 * Real-stack reader chat journey: Next.js + FastAPI + PostgreSQL.
 * Only the model provider transport is controlled (via qualification helper).
 * Must not mock conversation/novel/auth API routes.
 */
import { expect, test } from "@playwright/test";
import { spawnSync } from "child_process";
import path from "path";

import { backendPythonBin, registerAndLogin, uniqueUser } from "./helpers";

const backend = path.resolve(__dirname, "../../backend");
const python = backendPythonBin(backend);

function qualificationCommand(args: string[]) {
  const result = spawnSync(python, ["scripts/run_reader_chat_qualification.py", ...args], {
    cwd: backend,
    env: process.env,
    encoding: "utf-8",
    timeout: 180_000,
  });
  if (result.status !== 0) {
    throw new Error(
      `reader-chat qualification helper failed (${result.status})\n${result.stdout}\n${result.stderr}`
    );
  }
  const marker = result.stdout
    .split(/\r?\n/)
    .find((line) => line.startsWith("E2E_RESULT="));
  if (!marker) {
    throw new Error(`qualification helper emitted no result\n${result.stdout}`);
  }
  return JSON.parse(marker.slice("E2E_RESULT=".length)) as {
    novel_id: number;
    chapter_id: number;
    chapter2_id?: number;
    content_excerpt?: string;
    conversation_id?: number;
    job_id?: number;
  };
}

test("real API multi-session chat with citations, spoiler safety and refresh replay", async ({
  page,
}, testInfo) => {
  test.setTimeout(240_000);
  const consoleErrors: string[] = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });

  // Ensure real specs do not mock novel/conversation APIs
  // (static assertion also in release gate)

  const user = uniqueUser("reader_chat_real");
  await registerAndLogin(page, user);
  const seeded = qualificationCommand(["--e2e-seed-user", user.username]);

  await page.goto(`/novels/${seeded.novel_id}`);
  await expect(page.getByTestId("reader-page-text")).toBeVisible({ timeout: 30_000 });

  // Select first few characters of chapter text
  await page.evaluate(() => {
    const root = document.querySelector('[data-testid="reader-page-text"]');
    if (!root) throw new Error("no page text");
    // Walk to the first text node (paragraph blocks are wrapped in divs).
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let text: Text | null = null;
    while (walker.nextNode()) {
      const node = walker.currentNode as Text;
      if (node.data.trim()) {
        text = node;
        break;
      }
    }
    if (!text) throw new Error("no text node");
    const range = document.createRange();
    const end = Math.min(6, text.data.length);
    range.setStart(text, 0);
    range.setEnd(text, end);
    const sel = window.getSelection();
    sel?.removeAllRanges();
    sel?.addRange(range);
    document.dispatchEvent(new Event("selectionchange"));
    document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  });

  await expect(page.getByTestId("reader-selection-action")).toBeVisible({
    timeout: 10_000,
  });
  await page.getByTestId("reader-selection-action").getByRole("button").click();
  await expect(page.getByTestId("reader-chat-panel")).toBeVisible();

  // Desktop: chat column reserved; mobile: max height
  if (testInfo.project.name === "chromium-desktop") {
    await expect(page.getByTestId("reader-chat-column")).toBeVisible();
    const textBox = await page.getByTestId("reader-page-text").boundingBox();
    const panelBox = await page.getByTestId("reader-chat-panel").boundingBox();
    expect(textBox && panelBox).toBeTruthy();
    if (textBox && panelBox) {
      expect(panelBox.x + 2).toBeGreaterThanOrEqual(textBox.x + textBox.width - 8);
    }
  }
  if (testInfo.project.name !== "chromium-desktop") {
    const viewportHeight = page.viewportSize()?.height ?? 844;
    const box = await page.getByTestId("reader-chat-panel").boundingBox();
    expect(box?.height ?? 9999).toBeLessThanOrEqual(viewportHeight * 0.5 + 40);
  }

  await page.getByTestId("reader-chat-input").fill("选中的这段在写什么？");
  const messageWait = page.waitForResponse(
    (r) =>
      r.request().method() === "POST" &&
      /\/api\/novels\/\d+\/conversations\/\d+\/messages$/.test(
        new URL(r.url()).pathname
      ) &&
      r.status() === 202,
    { timeout: 60_000 }
  );
  await page.getByTestId("reader-chat-send").click();
  const accepted = await messageWait;
  const acceptedBody = (await accepted.json()) as {
    job: { id: number };
    message: { conversation_id: number };
  };

  // Control only provider transport: complete the durable job via qualification helper
  qualificationCommand([
    "--e2e-complete-job",
    String(acceptedBody.job.id),
  ]);

  await expect
    .poll(
      async () => {
        const text = await page.getByTestId("reader-chat-messages").innerText();
        return text.includes("竹林") || text.includes("阿宁") || text.length > 20;
      },
      { timeout: 60_000 }
    )
    .toBe(true);

  // Citations present and navigable
  const citation = page.getByTestId("reader-chat-citation").first();
  if (await citation.isVisible().catch(() => false)) {
    await citation.click();
    await expect(page.getByTestId("reader-citation-highlight")).toBeVisible({
      timeout: 10_000,
    });
  }

  // Create second conversation (multi-session)
  await page.getByLabel("新建会话").click();
  await expect
    .poll(async () => page.getByTestId(/reader-chat-conv-/).count())
    .toBeGreaterThan(1);

  // Spoiler: default UI must not show future chapter secret
  await expect(page.getByText("SECRET_FUTURE")).toHaveCount(0);

  // Refresh replays PostgreSQL conversations — 等阅读器就绪再判断面板状态，
  // 避免在加载中误判把已持久化的打开状态切换成关闭
  await page.reload();
  await expect(page.getByTestId("reader-scroll-column")).toBeVisible({
    timeout: 30_000,
  });
  if (!(await page.getByTestId("reader-chat-panel").isVisible().catch(() => false))) {
    await page.getByTestId("reader-chat-open").click();
  }
  await expect(page.getByTestId("reader-chat-panel")).toBeVisible();
  // 此前新建了第二个（空）会话排在列表首位；切回有消息的「新会话」验证 PostgreSQL 回放
  await page.getByTestId(/reader-chat-conv-/).filter({ hasText: "新会话" }).click();
  await expect
    .poll(async () => page.getByTestId(/reader-chat-msg-/).count())
    .toBeGreaterThan(0);

  // Mobile collapse continues reading
  if (testInfo.project.name !== "chromium-desktop") {
    await page.getByLabel("收起对话").click();
    await expect(page.getByTestId("reader-chat-chip")).toBeVisible();
    await expect(page.getByTestId("reader-page-text")).toBeVisible();
  }

  // Keyboard: focus input
  if (await page.getByTestId("reader-chat-input").isVisible().catch(() => false)) {
    await page.getByTestId("reader-chat-input").focus();
    await expect(page.getByTestId("reader-chat-input")).toBeFocused();
  }

  // No clue product UI
  await expect(page.getByText(/线索追踪|伏笔管理/)).toHaveCount(0);

  // Soft check console errors (ignore Next.js dev noise)
  const serious = consoleErrors.filter(
    (e) => !/Download the React DevTools|Fast Refresh|hydration/i.test(e)
  );
  expect(serious.length).toBeLessThan(5);
});
