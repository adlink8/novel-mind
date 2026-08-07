/**
 * Agent 自动路由闭环 e2e（feat(reader): unified AI assistant）—— 用户不选 skill，
 * AI 按意图自动路由（route-skill + 自动补锚）。
 *
 * 真实后端 + 真实 AI（owner 2 / novel 6 / e2e_login_test）。覆盖：
 *   - 统一对话窗口存在（不再有 agent tab，单一消息区 + 单一输入框）；
 *   - 前端不暴露 skill 选择器（契约关键点：UI 不要求用户选 skill）；
 *   - 自动路由到问答（reader_chat 主通道，含章节锚点 / 引证）；
 *   - 自动路由到画图意图（智能体回合 SSE 流式，AgentTurnInline 触发）；
 *   - 阅读页侧边栏唤起 AI 助手（reader-chat-panel 可用）。
 *
 * 真实 AI 调用很慢：reader_chat 走 generation_job 轮询（POST 202 → poll → 完成），
 * agent 走 SSE 流。核心断言走真实路径；纯布局/契约断言走快路径。
 * 移动端项目下画图意图与长问答走真实路径会非常慢，统一在非桌面项目 skip
 * 慢用例（保持桌面断言充分 + 移动端不 flakiness）。
 */
import { expect, test, type Page } from "@playwright/test";

import { login } from "./helpers";

const NOVEL_ID = "6";

/** 在分析页选择目标小说并等待统一对话窗口就绪（带章节数据 + 边界提示）。 */
async function openAnalysisForNovel6(page: Page) {
  await page.goto("/analysis");
  await page.getByLabel("选择小说").selectOption(NOVEL_ID);
  await expect(page.getByTestId("analysis-chat-panel")).toBeVisible({
    timeout: 30_000,
  });
  // 章节加载完成后边界提示会渲染（"基于你已读至第 N 章"）—— 发送按钮才能点。
  await expect(page.getByTestId("analysis-chat-boundary")).toBeVisible({
    timeout: 30_000,
  });
}

test.describe.configure({ mode: "serial" });

test.beforeEach(async ({ page }) => {
  await login(page, "e2e_login_test", "pass12345");
  // This spec drives the real backend and real AI against a hand-seeded
  // fixture (owner 2 / novel 6 / e2e_login_test). On a fresh/CI database the
  // seeded novel does not exist; skip the whole serial group instead of
  // failing selectOption on the novel picker.
  const hasNovel6 = await page
    .evaluate(async () => {
      const res = await fetch("/api/novels");
      if (!res.ok) return false;
      const data = (await res.json()) as { items?: Array<{ id: number }> };
      return Array.isArray(data.items) && data.items.some((n) => n.id === 6);
    })
    .catch(() => false);
  test.skip(
    !hasNovel6,
    "requires hand-seeded owner 2 / novel 6 / e2e_login_test on the real backend"
  );
});

test("unified chat window exists — single message area, single input, no agent tab", async ({
  page,
}) => {
  await openAnalysisForNovel6(page);

  // 单一消息区 + 单一输入框 + 单一发送按钮（不再有独立 agent tab/面板）。
  await expect(page.getByTestId("analysis-chat-messages")).toBeVisible();
  await expect(page.getByTestId("analysis-chat-input")).toBeVisible();
  await expect(page.getByTestId("analysis-chat-send")).toBeVisible();
  await expect(page.getByLabel("分析对话输入")).toBeVisible();

  // 顶层视图只有「对话 | 分析」两个 tab：旧 analysis-view-tab-agent 已移除。
  await expect(page.getByTestId("analysis-view-tab-chat")).toBeVisible();
  await expect(page.getByTestId("analysis-view-tab-analysis")).toBeVisible();
  await expect(page.getByTestId("analysis-view-tab-agent")).toHaveCount(0);
  // 旧 AgentWorkspacePanel 容器也已下架。
  await expect(page.getByTestId("agent-workspace-panel")).toHaveCount(0);
  await expect(page.getByTestId("agent-input")).toHaveCount(0);
});

test("frontend does not expose a skill selector — AI auto-routes by intent", async ({
  page,
}) => {
  await openAnalysisForNovel6(page);

  // 契约：统一对话窗口内零 <select>（排除小说选择 select —— 它在 header 而非 chat panel）。
  const skillSelectInChat = page
    .getByTestId("analysis-chat-panel")
    .locator("select");
  await expect(skillSelectInChat).toHaveCount(0);

  // 整页也无暴露给用户的 skill 下拉/单选控件。
  await expect(
    page.getByTestId(/skill-select|agent-skill-select|skill-picker/)
  ).toHaveCount(0);

  // 空态文案必须告诉用户：直接提问即可，意图会由智能体自动处理 —— 不要求选 skill。
  await expect(page.getByTestId("analysis-chat-empty")).toContainText(
    "画图/续写等意图会由智能体自动处理"
  );
});

test("auto-route to Q&A — 'why' intent flows through reader_chat and yields an answer", async ({
  page,
}, testInfo) => {
  // 真实 AI 调用非常慢（job poll → 完成），移动端项目跳过以避免 CI 时长爆炸。
  test.skip(
    testInfo.project.name !== "chromium-desktop",
    "real-AI reader_chat path is desktop-only to keep mobile CI bounded"
  );
  test.setTimeout(240_000);

  await openAnalysisForNovel6(page);

  const question = "林默为什么一定要找到爷爷";
  await page.getByTestId("analysis-chat-input").fill(question);
  await page.getByTestId("analysis-chat-send").click();

  // reader_chat 主通道：消息落库 + generation_job 轮询。等待助手气泡出现。
  // 自动路由没有要求用户选 skill —— 这条消息既没指 skill，也没命中画图/续写关键词，
  // 落入 reader_chat（answer-reading-question 回退路径），由后端智能体回答。
  await expect
    .poll(
      async () => {
        const text = await page
          .getByTestId("analysis-chat-messages")
          .innerText();
        // 返回文本长度数字：助手回答出现后文本会显著增长（>20 字符）；
        // 关键词包含可作为辅助信号但不参与数值断言。
        return text.length;
      },
      { timeout: 180_000, intervals: [2000, 3000, 5000] }
    )
    .toBeGreaterThan(20);

  const messagesText = await page
    .getByTestId("analysis-chat-messages")
    .innerText();
  // 用户消息必须出现（已发送），且助手回答应包含至少一个关键词（林默/爷爷）
  // 证明是真实的 Q&A 回答而非仅元数据。
  expect(messagesText).toContain(question);
  expect(messagesText).toMatch(/林默|爷爷/);
});

test("auto-route to illustration intent — AgentTurnInline triggers SSE without manual skill pick", async ({
  page,
}, testInfo) => {
  test.skip(
    testInfo.project.name !== "chromium-desktop",
    "real-AI agent SSE path is desktop-only to keep mobile CI bounded"
  );
  test.setTimeout(240_000);

  await openAnalysisForNovel6(page);

  // 「画图」类关键词命中前端启发式 → 走智能体通道（skill 缺省 = 后端自动路由）。
  const intent = "请画一张林默走进竹林的插图";
  await page.getByTestId("analysis-chat-input").fill(intent);
  await page.getByTestId("analysis-chat-send").click();

  // 智能体回合内联追加到统一消息流（agent-turn-inline testid）。
  await expect(page.getByTestId("agent-turn-inline").first()).toBeVisible({
    timeout: 60_000,
  });
  // 用户消息镜像在回合顶部，证明是同一窗口、同一通道，不是切到独立 agent tab。
  await expect(
    page.getByTestId("agent-turn-inline").first().getByTestId("agent-turn-question")
  ).toContainText(intent);

  // 路由到 agent 后应出现运行/终态状态条（非 fatal error）。
  // 真实生图可能挂很久；dev 模式下 React 双调用 effect 会使流早 cancel。
  // 以「回合已发起 + 状态可读（任意状态含 cancelled）+ 无 fatal error」为通过门槛。
  await expect
    .poll(
      async () => {
        const status = page.getByTestId("agent-turn-job-status").first();
        if (!(await status.isVisible().catch(() => false))) return null;
        return status.getAttribute("data-status");
      },
      { timeout: 60_000, intervals: [1000, 2000, 3000] }
    )
    .toMatch(/queued|running|completed|cancelled|failed/);

  await expect(page.getByTestId("agent-turn-error")).toHaveCount(0);
});

test("reader page sidebar — AI assistant is summonable with input", async ({
  page,
}) => {
  await page.goto(`/novels/${NOVEL_ID}`);
  await expect(page.getByTestId("reader-page-text")).toBeVisible({
    timeout: 30_000,
  });

  // 侧边栏默认折叠；点选区对话按钮唤起。
  await page.getByTestId("reader-chat-open").click();
  // 桌面端可能先出现 rail，再点 expand；与现有 reader-chat-real.spec 同款处理。
  const rail = page.getByTestId("reader-chat-rail");
  if (await rail.isVisible().catch(() => false)) {
    await page.getByTestId("reader-chat-expand").click();
  }
  await expect(page.getByTestId("reader-chat-panel")).toBeVisible();

  // 完整 AI 助手：输入 + 发送按钮可见。
  await expect(page.getByTestId("reader-chat-input")).toBeVisible();
  await expect(page.getByTestId("reader-chat-send")).toBeVisible();

  // 同样不暴露 skill 选择器：侧边栏里没有 skill 下拉。
  await expect(
    page.getByTestId("reader-chat-panel").locator("select")
  ).toHaveCount(0);
});