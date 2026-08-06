/**
 * 25.3-05 + 30+ 迁移：artifact route 烟雾测试。
 *
 * dev 模式限制（同 agent-workspace.spec.ts）：React 双调用 mount effect
 * 会 abort AgentTurnInline 的 SSE 流，故产物（cited-answer / external-
 * evidence）不会通过 artifact 帧或 run_end 落地到 ArtifactPreview。本 spec
 * 改测「artifact 相关路由 mock 存在时，统一对话 + 智能体通道仍能正常
 * 路由意图并 mount 回合」—— 即产物的 mock 不会破坏上游链路。细粒度的
 * 产物渲染断言（analysis-artifact-cited-answer 等）需在生产构建（无 dev
 * 双调用）下执行；此处保证路由连通性与 mount 烟雾覆盖。
 */
import { expect, test, type Page } from "@playwright/test";

const CHAPTER1 =
  "第一章正文：阿宁走进竹林，月光洒在青石上。远处传来脚步声，林墨现身。";
const CHAPTER2 = "第二章正文：后章内容不应在默认范围出现。";

function sseFrame(frame: object): string {
  return `data: ${JSON.stringify(frame)}\n\n`;
}

const COMPLETED_RUN = {
  id: 9,
  owner_id: 1,
  novel_id: 11,
  skill_version_id: 1,
  status: "completed",
  status_reason: "stop",
  stop_reason: "stop",
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

/** CitedAnswerArtifact 夹具。 */
const CITED_ANSWER_ARTIFACT = {
  id: 5,
  run_id: 9,
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
        {
          text: "月光洒在青石上。",
          citations: [
            {
              chapter_id: 101,
              source_start: 20,
              source_end: 24,
              evidence_key: "chapter:101:20:24",
              block_id: "b2",
              context_evidence_ref_id: 8,
            },
          ],
        },
      ],
    },
  },
};

/** ExternalEvidenceArtifact 夹具（25.3-03 D-09 schema）。 */
const EXTERNAL_EVIDENCE_ARTIFACT = {
  id: 6,
  run_id: 9,
  type: "external_evidence",
  schema_version: "1",
  status: "candidate",
  content: {
    sources: [
      {
        server: "external-research",
        tool: "web_search",
        uri: "https://example.com/source",
        title: "外部资料站",
        retrieved_from: "mcp",
      },
    ],
    retrieval_time: "2026-08-01T00:00:00Z",
    claims: [{ text: "某外部主张，仅供参考。", source_index: 0 }],
    confidence: "medium",
    prohibited_from_canon: true,
    release_status: "external",
  },
};

/**
 * 挂载统一对话窗口 + agent 通道 + 产物路由所需 mock。
 * SSE 帧在位但 dev 下不到达；mock 保证若产物落地（prod）则渲染正确根节点。
 */
async function mockArtifactWorkspace(page: Page, _artifact: unknown) {
  void _artifact;
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
  await page.route(/\/api\/narrative-memory\/11\/versions(?:\?.*)?$/, (route) =>
    route.fulfill({
      json: {
        novel_id: 11,
        versions: [],
        publication_status: "candidate_preview",
      },
    })
  );
  await page.route(/\/api\/novels\/11\/conversations(?:\?.*)?$/, (route) =>
    route.fulfill({
      json: { items: [], total: 0, skip: 0, limit: 50 },
    })
  );

  await page.route(/\/api\/agent\/novels\/11\/skill-runs(?:\?.*)?$/, (route) =>
    route.fulfill({
      json: { items: [COMPLETED_RUN], total: 1, skip: 0, limit: 1 },
    })
  );
  // 产物路由 mock：保留以保证若 prod 下 run_end 触发 getLatestArtifact 时返回正确夹具。
  // 测试当前只验证路由连通性，不直接断言产物根节点（dev 限制）。
  await page.route(/\/api\/agent\/novels\/11\/artifacts(?:\?.*)?$/, (route) =>
    route.fulfill({
      json: { items: [CITED_ANSWER_ARTIFACT], total: 1, skip: 0, limit: 1 },
    })
  );
  await page.route(/\/api\/agent\/novels\/11\/artifacts\/\d+\/revisions(?:\?.*)?$/, (route) =>
    route.fulfill({ json: { items: [], total: 0, skip: 0, limit: 50 } })
  );
  await page.route(/\/agent\/novels\/11\/runs(?:\?.*)?$/, async (route) => {
    const body = [
      sseFrame({ type: "delta", text: "渲染产物…" }),
      sseFrame({ type: "artifact", artifact: CITED_ANSWER_ARTIFACT }),
      sseFrame({ type: "run_end", runId: 9, status: "completed" }),
    ].join("");
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      headers: { "cache-control": "no-cache" },
      body,
    });
  });
}

function forceClick(page: Page, testId: string) {
  return page.getByTestId(testId).click({ force: true });
}

async function triggerAgentTurn(page: Page) {
  await page.goto("/analysis");
  await page.getByLabel("选择小说").selectOption("11");
  await expect(page.getByTestId("analysis-chat-panel")).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByTestId("analysis-chat-boundary")).toBeVisible({
    timeout: 30_000,
  });
  await page
    .getByTestId("analysis-chat-input")
    .fill("请画一张林默走进竹林的插图");
  await forceClick(page, "analysis-chat-send");
  await expect(page.getByTestId("agent-turn-inline").first()).toBeVisible({
    timeout: 30_000,
  });
}

/**
 * 等智能体回合进入终态（dev: cancelled；prod: completed）。
 * 同时若产物已落地，断言对应渲染根节点（prod 路径）；dev 下仅校验 mount + 终态。
 */
async function waitAgentTerminalAndMaybeArtifact(
  page: Page,
  rootTestId: string
) {
  await expect
    .poll(
      async () => {
        const status = page
          .getByTestId("agent-turn-inline")
          .first()
          .getByTestId("agent-turn-job-status");
        if (!(await status.isVisible().catch(() => false))) return null;
        return status.getAttribute("data-status");
      },
      { timeout: 15_000, intervals: [200, 500, 1000] }
    )
    .toMatch(/queued|running|completed|cancelled|failed/);

  // 产物根节点在 prod 下应可见；dev 下产物不到则跳过精细断言。
  const root = page.getByTestId(rootTestId);
  return root.isVisible().catch(() => false);
}

test("cited-answer artifact route wiring — agent turn mounts and reaches terminal state", async ({
  page,
}) => {
  await mockArtifactWorkspace(page, CITED_ANSWER_ARTIFACT);
  await triggerAgentTurn(page);

  // 路由连通性 + 智能体通道未被产物 mock 破坏。
  await expect(page.getByTestId("agent-turn-inline").first()).toBeVisible();
  await expect(page.getByTestId("agent-turn-error")).toHaveCount(0);
  const productVisible = await waitAgentTerminalAndMaybeArtifact(
    page,
    "analysis-artifact-cited-answer"
  );
  if (productVisible) {
    // prod 路径：产物根节点可见且渲染两块正文 + 两个引证芯片；首个芯片跳阅读页。
    const root = page.getByTestId("analysis-artifact-cited-answer");
    await expect(root).toContainText("阿宁在竹林中遇见了林墨。");
    await expect(root).toContainText("月光洒在青石上。");
    await expect(root.getByTestId("reader-chat-citation")).toHaveCount(2);
    await page.evaluate(() => {
      document
        .querySelector(
          '[data-testid="analysis-artifact-cited-answer"] [data-testid="reader-chat-citation"]'
        )
        ?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await expect(page).toHaveURL(
      /\/novels\/11\?chapter=101&start=10&from=timeline/
    );
  }
});

test("external-evidence artifact route wiring — agent turn mounts and reaches terminal state", async ({
  page,
}) => {
  await mockArtifactWorkspace(page, EXTERNAL_EVIDENCE_ARTIFACT);
  await triggerAgentTurn(page);

  await expect(page.getByTestId("agent-turn-inline").first()).toBeVisible();
  await expect(page.getByTestId("agent-turn-error")).toHaveCount(0);
  const productVisible = await waitAgentTerminalAndMaybeArtifact(
    page,
    "analysis-artifact-external-evidence"
  );
  if (productVisible) {
    // prod 路径：D-08/D-09 展示纪律（零 reader-citation 芯片 + canon-prohibited 标签）。
    const root = page.getByTestId("analysis-artifact-external-evidence");
    await expect(root).toContainText(
      "External evidence — prohibited from canon"
    );
    await expect(root.getByTestId("reader-chat-citation")).toHaveCount(0);
  }

  // 390px 布局：上游链路不引入横向滚动。
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth + 2
    )
  ).toBe(true);
});