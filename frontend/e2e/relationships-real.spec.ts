import { expect, test } from "@playwright/test";
import { spawnSync } from "child_process";
import path from "path";

import { backendPythonBin, registerAndLogin, uniqueUser } from "./helpers";

const backend = path.resolve(__dirname, "../../backend");
const python = backendPythonBin(backend);

function qualificationCommand(args: string[]) {
  const result = spawnSync(python, ["scripts/run_relationship_qualification.py", ...args], {
    cwd: backend,
    env: process.env,
    encoding: "utf-8",
    timeout: 180_000,
  });
  if (result.status !== 0) {
    throw new Error(
      `relationship qualification helper failed (${result.status})\n${result.stdout}\n${result.stderr}`
    );
  }
  const marker = result.stdout.split(/\r?\n/).find((line) => line.startsWith("E2E_RESULT="));
  if (!marker) {
    throw new Error(`qualification helper emitted no result\n${result.stdout}`);
  }
  return JSON.parse(marker.slice("E2E_RESULT=".length)) as {
    novel_id: number;
    version_id: number;
    title?: string;
    early_observation_id?: number;
    lin_id?: number;
    gu_id?: number;
    shen_id?: number;
    node_count?: number;
  };
}

test("real API relationship workspace is spoiler-safe with filters evidence and override", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const user = uniqueUser("rel_real");
  await registerAndLogin(page, user);
  const seeded = qualificationCommand(["--e2e-seed-user", user.username]);

  type GraphBody = {
    nodes: Array<{ name: string; character_id: number }>;
    edges: Array<{ observation_id: number; relation_type: string }>;
    counts: { nodes: number; edges: number };
    degradation: { mode: string };
    cutoff_chapter: number;
    full_book: boolean;
  };
  const graphResponses: GraphBody[] = [];
  page.on("response", async (response) => {
    const url = new URL(response.url());
    if (
      response.request().method() === "GET" &&
      /\/api\/relationships\/\d+\/graph/.test(url.pathname)
    ) {
      try {
        graphResponses.push(await response.json());
      } catch {
        // ignore non-json
      }
    }
  });

  await page.goto("/analysis");
  await page.getByLabel("选择小说").selectOption(String(seeded.novel_id));
  await page.getByRole("tab", { name: "人物关系" }).click();
  await expect(page.getByTestId("relationship-workspace")).toBeVisible();

  // Default progress is chapter 1 — future enemy must not appear.
  await expect.poll(() => graphResponses.length).toBeGreaterThan(0);
  const first = graphResponses[graphResponses.length - 1];
  expect(first.full_book).toBe(false);
  expect(first.nodes.some((n) => n.name.includes("Future"))).toBe(false);
  expect(JSON.stringify(first)).not.toContain("沈夜Future");
  expect(JSON.stringify(first)).not.toContain("SECRET_FUTURE");
  const workspace = page.getByTestId("relationship-workspace");
  await expect(workspace).toContainText("林墨");
  await expect(workspace).not.toContainText("沈夜Future");

  // Filters remain interactive.
  await page.getByLabel("筛选关系类型").selectOption("ally");
  await expect
    .poll(() => graphResponses[graphResponses.length - 1]?.edges.every((e) => e.relation_type === "ally"))
    .toBe(true);
  // Clear type filter so future enemy can appear after full-book disclosure.
  await page.getByLabel("筛选关系类型").selectOption("");

  // Zoom controls should remain present for normal mode.
  await page.getByRole("button", { name: "放大" }).click();

  // Full-book confirmation reveals future relation only after explicit confirm.
  await page.getByRole("checkbox", { name: "显示全书（可能剧透）" }).click();
  await page.getByRole("button", { name: "确认显示全书" }).click();
  await expect
    .poll(() =>
      graphResponses.some(
        (body) => body.full_book === true && body.nodes.some((n) => n.name.includes("Future"))
      )
    )
    .toBe(true);

  // Open evidence from keyboard companion list (canvas + list share arrays).
  const companionList = page.getByTestId("relationship-companion-list");
  await expect(companionList).toBeVisible();
  await companionList.getByRole("button").filter({ hasText: "关系" }).first().click();
  const panel = page.getByTestId("relationship-evidence-panel");
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await expect(panel).toContainText(/结盟|机器推断|人工修正|证据|同盟|敌对/);

  // No horizontal page overflow on either viewport project.
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1
    )
  ).toBe(true);
});

test("filters_required over-cap graph shows guidance without cytoscape elements", async ({
  page,
}) => {
  test.setTimeout(240_000);
  const user = uniqueUser("rel_cap");
  await registerAndLogin(page, user);
  const seeded = qualificationCommand(["--e2e-seed-over-cap-user", user.username]);
  expect(seeded.node_count).toBeGreaterThan(500);

  const graphResponses: Array<{
    nodes: unknown[];
    edges: unknown[];
    degradation: { mode: string };
    counts: { nodes: number; edges: number };
  }> = [];
  page.on("response", async (response) => {
    const url = new URL(response.url());
    if (
      response.request().method() === "GET" &&
      /\/api\/relationships\/\d+\/graph/.test(url.pathname)
    ) {
      try {
        graphResponses.push(await response.json());
      } catch {
        // ignore
      }
    }
  });

  await page.goto("/analysis");
  await page.getByLabel("选择小说").selectOption(String(seeded.novel_id));
  await page.getByRole("tab", { name: "人物关系" }).click();
  await expect(page.getByTestId("relationship-workspace")).toBeVisible();

  await expect
    .poll(() => graphResponses.some((b) => b.degradation?.mode === "filters_required"))
    .toBe(true);
  const overCap = graphResponses.find((b) => b.degradation?.mode === "filters_required");
  expect(overCap).toBeTruthy();
  expect(overCap!.nodes).toEqual([]);
  expect(overCap!.edges).toEqual([]);
  expect(overCap!.counts.nodes).toBeGreaterThan(500);

  await expect(page.getByTestId("relationship-filters-required")).toBeVisible();
  await expect(page.getByTestId("relationship-canvas")).toHaveCount(0);
  await expect(page.getByText(/关系规模过大|请缩小筛选/)).toBeVisible();

  // Character filter should restore an interactive graph.
  await page.getByLabel("筛选人物").selectOption({ index: 1 });
  await expect
    .poll(() => {
      const last = graphResponses[graphResponses.length - 1];
      return last && last.degradation?.mode !== "filters_required" && last.nodes.length > 0;
    })
    .toBe(true);
});
