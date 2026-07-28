import { expect, test } from "@playwright/test";
import { spawnSync } from "child_process";
import path from "path";

import { registerAndLogin, uniqueUser } from "./helpers";

const backend = path.resolve(__dirname, "../../backend");
const python =
  process.platform === "win32" ? "venv\\Scripts\\python.exe" : "venv/bin/python";

function qualificationCommand(args: string[]) {
  const result = spawnSync(python, ["scripts/run_clue_qualification.py", ...args], {
    cwd: backend,
    env: process.env,
    encoding: "utf-8",
    timeout: 180_000,
  });
  if (result.status !== 0) {
    throw new Error(
      `clue qualification helper failed (${result.status})\n${result.stdout}\n${result.stderr}`
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
    version_id: number;
    title?: string;
    logical_clue_id?: string;
  };
}

test("real API clue workspace is spoiler-safe with filters evidence and human actions", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const user = uniqueUser("clue_real");
  await registerAndLogin(page, user);
  const seeded = qualificationCommand(["--e2e-seed-user", user.username]);

  type ClueBody = {
    active: null | {
      clues: Array<{
        logical_clue_id: string;
        title: string;
        derived_state: string;
      }>;
      full_book: boolean;
      counts: { clues: number; by_state?: Record<string, number> };
    };
    running_candidate: null | unknown;
  };
  const clueResponses: ClueBody[] = [];
  page.on("response", async (response) => {
    const url = new URL(response.url());
    if (
      response.request().method() === "GET" &&
      /\/api\/clues\/\d+$/.test(url.pathname)
    ) {
      try {
        clueResponses.push(await response.json());
      } catch {
        // ignore non-json
      }
    }
  });

  await page.goto("/analysis");
  await page.getByLabel("选择小说").selectOption(String(seeded.novel_id));
  await page.getByRole("tab", { name: "线索与伏笔" }).click();
  await expect(page.getByTestId("clue-workspace")).toBeVisible();

  await expect.poll(() => clueResponses.length).toBeGreaterThan(0);
  const first = clueResponses[clueResponses.length - 1];
  expect(first.active?.full_book).toBe(false);
  const defaultStates = first.active?.clues.map((c) => c.derived_state) ?? [];
  expect(defaultStates).not.toContain("paid_off");
  expect(JSON.stringify(first)).not.toContain("SECRET_PAYOFF");
  expect(JSON.stringify(first)).not.toContain("SECRET FUTURE");

  const workspace = page.getByTestId("clue-workspace");
  await expect(workspace).toContainText(/封口信|线索/);
  await expect(workspace).not.toContainText("SECRET_PAYOFF");

  // Status filter remains usable.
  const statusFilter = page.getByLabel("筛选线索状态");
  await expect(statusFilter).toBeVisible();
  const options = await statusFilter.locator("option").allTextContents();
  if (options.length > 1) {
    await statusFilter.selectOption({ index: 1 });
  }

  // Open evidence panel from companion list (role=option, not button).
  const list = page.getByTestId("clue-keyboard-list");
  await expect(list).toBeVisible({ timeout: 15_000 });
  const firstItem = list.getByRole("option").first();
  await expect(firstItem).toBeVisible();
  await firstItem.click();
  const panel = page.getByTestId("clue-evidence-panel");
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await expect(panel).toContainText(/证据|预告|强化|回收|版本溯源/);

  // Four protected human actions. Order: annotate → confirm → link → reject
  // (reject is terminal, so it runs last). force:true avoids mobile nav intercept.
  async function waitActionIdle(target: ReturnType<typeof page.getByTestId>) {
    await expect(target.getByRole("button", { name: "确认" })).toBeEnabled({
      timeout: 20_000,
    });
  }

  let actionPanel = panel;
  await actionPanel.getByLabel("动作原因").fill("e2e annotate reason");
  await actionPanel.getByLabel("注释内容").fill("e2e note text");
  await actionPanel.getByRole("button", { name: "保存注释" }).click({ force: true });
  await waitActionIdle(actionPanel);

  await actionPanel.getByLabel("动作原因").fill("e2e confirm reason");
  await actionPanel.getByRole("button", { name: "确认" }).click({ force: true });
  await waitActionIdle(actionPanel);

  await actionPanel.getByLabel("动作原因").fill("e2e link reason");
  const linkTarget = actionPanel.getByLabel("关联目标值");
  await linkTarget.evaluate((el) => {
    (el as HTMLElement).scrollIntoView({ block: "center" });
  });
  await linkTarget.fill("1");
  // Ensure React state sees both fields (mobile can drop last keystroke).
  await actionPanel.getByLabel("动作原因").fill("e2e link reason");
  await linkTarget.fill("1");
  const submitLink = actionPanel.getByRole("button", { name: "提交关联调整" });
  await expect(submitLink).toBeEnabled({ timeout: 10_000 });
  await submitLink.evaluate((el) => (el as HTMLButtonElement).click());
  await expect(actionPanel.getByText("确认替换/写入该关联")).toBeVisible({
    timeout: 10_000,
  });
  await actionPanel
    .getByRole("button", { name: "确认提交" })
    .evaluate((el) => (el as HTMLButtonElement).click());
  await waitActionIdle(actionPanel);

  // Close panel before full-book (reject is terminal — run after paid_off proof).
  await actionPanel.getByRole("button", { name: "关闭线索证据" }).click({ force: true });
  await expect(page.getByTestId("clue-evidence-panel")).toHaveCount(0);

  // Clear status filter so paid_off is not hidden by prior filter selection.
  await statusFilter.selectOption("");

  // Full-book confirmation reveals paid_off only after existing Phase 08 confirm.
  await page.getByRole("checkbox", { name: "显示全书（可能剧透）" }).click();
  await page.getByRole("button", { name: "确认显示全书" }).click();
  await expect
    .poll(() =>
      clueResponses.some(
        (body) =>
          body.active?.full_book === true &&
          body.active.clues.some((c) => c.derived_state === "paid_off")
      )
    )
    .toBe(true);

  // After full-book, re-open panel and assert payoff chain is server-provided.
  await list.getByRole("option").first().click();
  actionPanel = page.getByTestId("clue-evidence-panel");
  await expect(actionPanel).toBeVisible();
  await expect(page.getByTestId("panel-payoff-chain")).toBeVisible();
  await expect(page.getByTestId("panel-payoff-chain")).toContainText(
    /回收|paid_off|已回收/
  );

  // Terminal reject last so paid_off visibility is proven first.
  await actionPanel.getByLabel("动作原因").fill("e2e reject reason");
  await actionPanel
    .getByRole("button", { name: "驳回" })
    .evaluate((el) => (el as HTMLButtonElement).click());
  await expect(page.getByText("确认驳回该线索")).toBeVisible({ timeout: 10_000 });
  await page
    .getByRole("button", { name: "确认驳回" })
    .evaluate((el) => (el as HTMLButtonElement).click());

  // No horizontal page overflow (desktop and mobile projects).
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth + 1
    )
  ).toBe(true);
});
