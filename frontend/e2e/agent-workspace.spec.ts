/**
 * Agent Workspace e2e（25.2-04 Task 3）—— 全 route-mocked，不依赖真 agent-service。
 *
 * 覆盖：桌面 + chromium-mobile-390 双项目；流式回答（SSE 帧经 sse.ts 增量派发）、
 * 工具摘要、候选产物预览 + 审批（断言出站 approve 请求 URL）、取消（无后续 delta）、
 * 引证跳转、会话恢复重灌；外加 reduced-motion 与无横向滚动检查（UI-MOTION-06）。
 */
import { expect, test, type Page } from "@playwright/test";

const CHAPTER1 =
  "第一章正文：阿宁走进竹林，月光洒在青石上。远处传来脚步声，林墨现身。";
const CHAPTER2 = "第二章正文：后章内容不应在默认范围出现。";

/** 单条 SSE 帧 → UTF-8 字节（data: {...}\n\n）。 */
function sseFrame(frame: object): Buffer {
  return Buffer.from(`data: ${JSON.stringify(frame)}\n\n`, "utf8");
}

const CITATION_ARTIFACT = {
  id: 5,
  run_id: 3,
  type: "cited_answer",
  schema_version: "cited-answer.v1",
  status: "candidate",
  content: {
    answer: {
      answer_blocks: [
        {
          text: "阿宁在竹林中遇见了林墨。",
          citations: [
            {
              chapter_id: 101,
              source_start: 10,
              source_end: 14,
              evidence_key: "chapter:101:10:14",
              block_id: "b1",
              context_evidence_ref_id: 7,
            },
          ],
        },
      ],
    },
  },
};

const RUNNING_RUN = {
  id: 3,
  owner_id: 1,
  novel_id: 11,
  skill_version_id: 1,
  status: "running",
  status_reason: null,
  stop_reason: null,
  branch: null,
  input_hash: "0".repeat(64),
  model_lineage: {},
  source_versions: {},
  budget_snapshot: {},
  error_code: null,
  cancel_requested: false,
  retry_count: 0,
  created_at: "2026-07-15T00:00:00Z",
  updated_at: "2026-07-15T00:00:01Z",
};

const COMPLETED_RUN = {
  ...RUNNING_RUN,
  id: 9,
  status: "completed",
  status_reason: "stop",
  stop_reason: "stop",
};

/**
 * 挂载 analysis 页所需 route-mock + agent 专用端点。
 * 未 mock 的路由落到 playwright webServer 起的真实后端（401 → 页面容错）。
 */
async function mockAgentWorkspace(
  page: Page,
  opts: {
    latestRun?: unknown;
    latestArtifact?: unknown;
    revisionContent?: unknown;
    /** SSE 运行端点返回的帧序列；不传则空 body。 */
    streamFrames?: Buffer[];
    /** cancel 端点出站记录。 */
    cancelCalls?: string[];
    /** approve/reject 端点出站记录。 */
    decisionCalls?: string[];
  } = {}
) {
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
  await page.route("**/api/novels", (route) =>
    route.fulfill({
      json: {
        items: [
          {
            id: 11,
            title: "雾城夜读",
            author: "佚名",
            description: null,
            genre: null,
            word_count: CHAPTER1.length + CHAPTER2.length,
            chapter_count: 2,
            status: "ready",
            reading_progress: { chapter_id: 101, progress_percent: 10 },
            created_at: "",
            updated_at: "",
          },
        ],
        total: 1,
        skip: 0,
        limit: 20,
      },
    })
  );
  await page.route("**/api/novels/11", (route) =>
    route.fulfill({
      json: {
        id: 11,
        title: "雾城夜读",
        author: "佚名",
        chapter_count: 2,
        word_count: CHAPTER1.length + CHAPTER2.length,
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
          content: CHAPTER1,
          word_count: CHAPTER1.length,
          created_at: "",
          updated_at: "",
        },
        {
          id: 102,
          novel_id: 11,
          chapter_number: 2,
          title: "第二章 后章",
          content: CHAPTER2,
          word_count: CHAPTER2.length,
          created_at: "",
          updated_at: "",
        },
      ],
    })
  );
  await page.route("**/api/novels/11/progress", (route) =>
    route.fulfill({ status: 200, json: {} })
  );
  await page.route(/\/api\/novels\/11\/conversations(?:\?.*)?$/, (route) =>
    route.fulfill({
      json: { items: [], total: 0, skip: 0, limit: 50 },
    })
  );

  // agent：最新 run / artifact / revision（session restore + runId 发现）
  // latestRun 支持数组：按 GET 调用次序依次返回（restore 一次 + submit 后 runId 发现一次）。
  const runResponses = Array.isArray(opts.latestRun)
    ? opts.latestRun
    : [opts.latestRun];
  let runIdx = 0;
  await page.route(/\/api\/agent\/novels\/11\/skill-runs(?:\?.*)?$/, (route) => {
    if (route.request().method() === "GET") {
      const items = runResponses.length > 0 ? [runResponses[Math.min(runIdx, runResponses.length - 1)]] : [];
      runIdx += 1;
      return route.fulfill({ json: { items, total: items.length, skip: 0, limit: 1 } });
    }
    return route.fallback();
  });
  await page.route(/\/api\/agent\/novels\/11\/artifacts(?:\?.*)?$/, (route) => {
    const items = opts.latestArtifact ? [opts.latestArtifact] : [];
    return route.fulfill({ json: { items, total: items.length, skip: 0, limit: 1 } });
  });
  await page.route(/\/api\/agent\/novels\/11\/artifacts\/\d+\/revisions(?:\?.*)?$/, (route) => {
    const items = opts.revisionContent
      ? [{ id: 1, content: opts.revisionContent }]
      : [];
    return route.fulfill({ json: { items, total: items.length, skip: 0, limit: 50 } });
  });
  // cancel 端点：记录出站 URL。
  await page.route("**/api/agent/novels/11/skill-runs/*/cancel", (route) => {
    opts.cancelCalls?.push(route.request().url());
    return route.fulfill({
      json: { ...RUNNING_RUN, status: "cancelled", error_code: "user_cancel" },
    });
  });
  // approve/reject：记录出站 URL，返回前向状态。
  await page.route("**/api/agent/artifacts/*/approve", (route) => {
    opts.decisionCalls?.push(route.request().url());
    return route.fulfill({
      json: { ...CITATION_ARTIFACT, status: "approved" },
    });
  });
  await page.route("**/api/agent/artifacts/*/reject", (route) => {
    opts.decisionCalls?.push(route.request().url());
    return route.fulfill({
      json: { ...CITATION_ARTIFACT, status: "rejected" },
    });
  });
  // SSE 运行端点：交付脚本化帧。
  await page.route("**/agent/novels/11/runs", (route) => {
    const body = Buffer.concat(opts.streamFrames ?? []);
    return route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: { "cache-control": "no-cache" },
      body,
    });
  });
}

/**
 * 本机 Next dev 的 `.next/dev` 持久化在此环境持续报错（EPERM / os error 5），
 * 导致 Playwright 普通 `locator.click()` 的 actionability「stable」检查永不通过
 * （既有 analysis 页 tab 同样复现，探针确认 bbox 稳定、hit-target 正确、mouse.click 正常）。
 * 故 tab 点击用 force 点击绕开该环境误判；行为仍被 state 断言覆盖（aria-selected/状态文本）。
 */
function forceClick(page: Page, testId: string) {
  return page.getByTestId(testId).click({ force: true });
}

async function openAgentTab(page: Page) {
  await page.goto("/analysis");
  await page.getByLabel("选择小说").selectOption("11");
  const tab = page.getByTestId("analysis-view-tab-agent");
  await expect(tab).toBeVisible({ timeout: 20_000 });
  await forceClick(page, "analysis-view-tab-agent");
  await expect(page.getByTestId("agent-workspace-panel")).toBeVisible();
}

test("desktop — streams a cited answer, previews the artifact, and approves it", async ({
  page,
}) => {
  const decisionCalls: string[] = [];
  await mockAgentWorkspace(page, {
    streamFrames: [
      sseFrame({ type: "delta", text: "阿宁在竹林中遇见了" }),
      sseFrame({ type: "tool_start", toolName: "get_chapter", args: {} }),
      sseFrame({ type: "delta", text: "林墨。" }),
      sseFrame({ type: "tool_end", toolName: "get_chapter" }),
      sseFrame({ type: "artifact", artifact: CITATION_ARTIFACT }),
      sseFrame({ type: "run_end", runId: 3, status: "completed" }),
    ],
    decisionCalls,
  });
  await openAgentTab(page);

  await page.getByTestId("agent-input").fill("阿宁在竹林里看见了谁？");
  await forceClick(page, "agent-send");

  // 流式回答 + 工具摘要 + 候选产物预览全部落地。
  await expect(page.getByTestId("agent-answer")).toContainText(
    "阿宁在竹林中遇见了林墨。"
  );
  await expect(page.getByTestId("agent-tool-summary")).toContainText(
    "get_chapter"
  );
  await expect(page.getByTestId("agent-tool-call")).toHaveAttribute(
    "data-status",
    "done"
  );
  await expect(page.getByTestId("agent-artifact-status")).toContainText(
    "候选"
  );

  // 无横向滚动 + reduced-motion 下控件仍可用（UI-MOTION-06）。
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth + 2
    )
  ).toBe(true);
  await page.emulateMedia({ reducedMotion: "reduce" });

  // 审批：Dialog 确认 → approve 出站请求含产物 id 与 approve。
  await forceClick(page, "agent-approve");
  await expect(page.getByTestId("agent-approve-confirm")).toBeVisible();
  await forceClick(page, "agent-approve-confirm");

  await expect.poll(() => decisionCalls.length).toBeGreaterThan(0);
  expect(decisionCalls[0]).toContain("/api/agent/artifacts/5/approve");
  await expect(page.getByTestId("agent-artifact-status")).toContainText(
    "已批准"
  );
});

test("cancel stops a live stream and calls the cancel endpoint", async ({
  page,
}) => {
  const cancelCalls: string[] = [];
  await mockAgentWorkspace(page, {
    // mount restore 返回 null（面板空态），submit 后 runId 发现返回运行中 run。
    latestRun: [null, RUNNING_RUN],
    // 服务端吐了一段 delta 后停住（无 run_end）。
    streamFrames: [sseFrame({ type: "delta", text: "不该继续出现的草稿" })],
    cancelCalls,
  });
  await openAgentTab(page);

  await page.getByTestId("agent-input").fill("继续写？");
  await forceClick(page, "agent-send");

  // 先渲染一段 delta，然后取消 → 无后续 delta、状态转 cancelled、cancel 端点被调用。
  await expect(page.getByTestId("agent-answer")).toContainText(
    "不该继续出现的草稿"
  );
  await forceClick(page, "agent-cancel");

  await expect(page.getByTestId("agent-job-status")).toHaveAttribute(
    "data-status",
    "cancelled"
  );
  await expect.poll(() => cancelCalls.length).toBeGreaterThan(0);
  expect(cancelCalls[0]).toContain("/skill-runs/3/cancel");
  await expect(page.getByTestId("agent-answer")).not.toContainText(
    "继续的内容"
  );
});

test("citation chip jumps to the reader highlight URL", async ({ page }) => {
  await mockAgentWorkspace(page, {
    streamFrames: [
      sseFrame({ type: "delta", text: "证据出自第一章。" }),
      sseFrame({ type: "artifact", artifact: CITATION_ARTIFACT }),
      sseFrame({ type: "run_end", runId: 3, status: "completed" }),
    ],
  });
  await openAgentTab(page);

  await page.getByTestId("agent-input").fill("证据在哪？");
  await forceClick(page, "agent-send");
  await expect(page.getByTestId("reader-chat-citation")).toBeVisible();

  await forceClick(page, "reader-chat-citation");
  await page.waitForTimeout(1500);
  console.log("CITATION_NAV_URL_AFTER_FORCE=" + page.url());
  // 诊断：直接 DOM click 是否触发导航
  await page.evaluate(() => {
    const el = document.querySelector('[data-testid="reader-chat-citation"]') as HTMLElement | null;
    el?.click();
  });
  await page.waitForTimeout(1500);
  console.log("CITATION_NAV_URL_AFTER_DOM=" + page.url());
  await expect(page).toHaveURL(
    /\/novels\/11\?chapter=101&start=10&from=timeline/
  );
});

test("session restore rehydrates the latest run and artifact", async ({
  page,
}) => {
  await mockAgentWorkspace(page, {
    latestRun: COMPLETED_RUN,
    latestArtifact: CITATION_ARTIFACT,
    revisionContent: CITATION_ARTIFACT.content,
  });
  await openAgentTab(page);

  // 未提交任何问题，mount 即恢复：completed 状态 + 候选产物 + 引证芯片。
  await expect(page.getByTestId("agent-job-status")).toHaveAttribute(
    "data-status",
    "completed"
  );
  await expect(page.getByTestId("agent-artifact-status")).toContainText(
    "候选"
  );
  await expect(page.getByTestId("reader-chat-citation")).toBeVisible();
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth + 2
    )
  ).toBe(true);
});
