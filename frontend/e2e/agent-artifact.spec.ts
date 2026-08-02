/**
 * 25.3-05 e2e —— pi-web-ui 渲染器注册表（模式借用，零 import）浏览器证明。
 *
 * 全 route-mocked（不依赖真 agent-service）：会话恢复路径直接喂一个
 * CitedAnswerArtifact（两个证据引用）和一个 ExternalEvidenceArtifact，
 * 在 desktop + chromium-mobile-390 上断言：
 * - cited-answer 渲染两块正文 + 两个引证芯片，点击首个芯片跳转
 *   /novels/{id}?chapter=..&start=..&from=timeline（analysis-chat-panel 约定）；
 * - external-evidence 渲染持久标签且零引证芯片（D-08/D-09 展示纪律）；
 * - 390px 布局无横向滚动。
 */
import { expect, test, type Page } from "@playwright/test";

const CHAPTER1 =
  "第一章正文：阿宁走进竹林，月光洒在青石上。远处传来脚步声，林墨现身。";
const CHAPTER2 = "第二章正文：后章内容不应在默认范围出现。";

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

/** CitedAnswerArtifact 夹具：两块正文，每块一个证据引用 → 恰好 2 个引证芯片。 */
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

/** 挂载 analysis 页所需 route-mock + agent 专用端点（镜像 agent-workspace.spec.ts）。 */
async function mockArtifactWorkspace(
  page: Page,
  artifact: unknown
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

  // agent：最新 run（completed）+ 最新产物（夹具）。内容已随产物返回，无需再读 revisions。
  await page.route(/\/api\/agent\/novels\/11\/skill-runs(?:\?.*)?$/, (route) =>
    route.fulfill({
      json: { items: [COMPLETED_RUN], total: 1, skip: 0, limit: 1 },
    })
  );
  await page.route(/\/api\/agent\/novels\/11\/artifacts(?:\?.*)?$/, (route) =>
    route.fulfill({
      json: { items: [artifact], total: 1, skip: 0, limit: 1 },
    })
  );
  await page.route(/\/api\/agent\/novels\/11\/artifacts\/\d+$/, (route) =>
    route.fulfill({ json: artifact })
  );
  await page.route(/\/api\/agent\/novels\/11\/artifacts\/\d+\/revisions(?:\?.*)?$/, (route) =>
    route.fulfill({ json: { items: [], total: 0, skip: 0, limit: 50 } })
  );
}

/**
 * 本机 Next dev `.next/dev` 持久化报 EPERM，导致 actionability「stable」检查不通过；
 * agent-workspace.spec.ts 已用 force 点击绕开（行为仍由 state 断言覆盖）。
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

test("cited-answer artifact renders blocks + two citation chips; chip jump navigates to source", async ({
  page,
}) => {
  await mockArtifactWorkspace(page, CITED_ANSWER_ARTIFACT);
  await openAgentTab(page);

  const root = page.getByTestId("analysis-artifact-cited-answer");
  await expect(root).toBeVisible();
  await expect(root).toContainText("阿宁在竹林中遇见了林墨。");
  await expect(root).toContainText("月光洒在青石上。");

  // 两个证据引用 → 两个引证芯片（每引用一个）。
  await expect(root.getByTestId("reader-chat-citation")).toHaveCount(2);

  // 点击首个芯片 → 跳转到阅读页对应章节（from=timeline 不覆盖真实进度）。
  await forceClick(page, "reader-chat-citation");
  await expect(page).toHaveURL(
    /\/novels\/11\?chapter=101&start=10&from=timeline/
  );
});

test("external-evidence artifact shows the canon-prohibited label and zero citation chips", async ({
  page,
}) => {
  await mockArtifactWorkspace(page, EXTERNAL_EVIDENCE_ARTIFACT);
  await openAgentTab(page);

  const root = page.getByTestId("analysis-artifact-external-evidence");
  await expect(root).toBeVisible();
  await expect(root).toContainText("External evidence — prohibited from canon");
  await expect(root).toContainText("外部资料站");
  await expect(root).toContainText("某外部主张，仅供参考。");

  // D-08/D-09：外部证据内部零引证芯片——UI 无法把外部主张伪装成正典引用。
  await expect(root.getByTestId("reader-chat-citation")).toHaveCount(0);

  // 390px 布局：产物可读且无横向滚动。
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth + 2
    )
  ).toBe(true);
});
