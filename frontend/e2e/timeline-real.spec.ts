import { expect, test } from "@playwright/test";
import { spawnSync } from "child_process";
import path from "path";

import { backendPythonBin, registerAndLogin, uniqueUser } from "./helpers";

const backend = path.resolve(__dirname, "../../backend");
const python = backendPythonBin(backend);

function qualificationCommand(args: string[]) {
  const result = spawnSync(python, ["scripts/run_timeline_qualification.py", ...args], {
    cwd: backend,
    env: process.env,
    encoding: "utf-8",
    timeout: 60_000,
  });
  if (result.status !== 0) {
    throw new Error(`qualification helper failed (${result.status})\n${result.stdout}\n${result.stderr}`);
  }
  const marker = result.stdout.split(/\r?\n/).find((line) => line.startsWith("E2E_RESULT="));
  if (!marker) throw new Error(`qualification helper emitted no result\n${result.stdout}`);
  return JSON.parse(marker.slice("E2E_RESULT=".length)) as { novel_id: number; run_id: number; title?: string; version_id?: number };
}

test("real API progresses partial candidate to spoiler-safe active timeline", async ({ page }) => {
  test.setTimeout(180_000);
  const user = uniqueUser("timeline_real");
  await registerAndLogin(page, user);
  const seeded = qualificationCommand(["--e2e-seed-user", user.username]);
  const timelineResponses: Array<{ active: null | { events: Array<{ title: string }> }; running_candidate: null | { events: Array<{ title: string }> } }> = [];
  page.on("response", async (response) => {
    const url = new URL(response.url());
    if (response.request().method() === "GET" && /^\/api\/timeline\/\d+$/.test(url.pathname)) {
      timelineResponses.push(await response.json());
    }
  });

  await page.goto("/analysis");
  await page.getByLabel("选择小说").selectOption(String(seeded.novel_id));
  await expect(page.getByRole("tab", { name: /正在生成|候选结果/ })).toBeVisible();
  await page.getByRole("button", { name: /阶段 1/ }).click();
  await expect(page.getByRole("heading", { name: "第一批事件" }).first()).toBeVisible();
  await expect(page.getByText("后章隐藏事件")).toHaveCount(0);
  await expect.poll(() => timelineResponses.some((body) => body.active === null && body.running_candidate?.events.length === 1)).toBe(true);

  const completed = qualificationCommand(["--e2e-resume-run", String(seeded.run_id)]);
  expect(completed.version_id).toBeTruthy();
  await page.reload();
  await page.getByLabel("选择小说").selectOption(String(seeded.novel_id));
  await expect(page.getByRole("tab", { name: /当前版本/ })).toBeVisible();
  await page.getByRole("button", { name: /阶段 1/ }).click();
  await expect(page.getByRole("heading", { name: "第一批事件" }).first()).toBeVisible();
  await expect(page.getByText("后章隐藏事件")).toHaveCount(0);
  await expect.poll(() => timelineResponses.some((body) => body.active?.events.length === 1 && body.running_candidate === null)).toBe(true);

  await page.getByRole("checkbox", { name: "显示全书（可能剧透）" }).click();
  await page.getByRole("button", { name: "确认显示全书" }).click();
  await expect(page.getByRole("heading", { name: "后章隐藏事件" }).first()).toBeVisible();
  await expect.poll(() => timelineResponses.some((body) => body.active?.events.length === 2)).toBe(true);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
});
