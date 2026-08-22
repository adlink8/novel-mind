import { expect, test } from "@playwright/test";
import { spawnSync } from "child_process";
import path from "path";

import {
  apiLogin,
  apiRegister,
  backendPythonBin,
  login,
  registerAndLogin,
  uniqueUser,
} from "./helpers";

const backend = path.resolve(__dirname, "../../backend");
const python = backendPythonBin(backend);
const BACKEND = process.env.BACKEND_URL || "http://127.0.0.1:8010";

function qualificationCommand(args: string[]) {
  const result = spawnSync(
    python,
    ["scripts/run_derivative_visual_review_qualification.py", ...args],
    {
      cwd: backend,
      env: process.env,
      encoding: "utf-8",
      timeout: 180_000,
    }
  );
  if (result.status !== 0) {
    throw new Error(
      `derivative visual review qualification helper failed (${result.status})\n${result.stdout}\n${result.stderr}`
    );
  }
  const marker = result.stdout
    .split(/\r?\n/)
    .find((line) => line.startsWith("E2E_RESULT="));
  if (!marker) {
    throw new Error(
      `qualification helper emitted no result\n${result.stdout}`
    );
  }
  return JSON.parse(marker.slice("E2E_RESULT=".length)) as {
    owner_id: number;
    novel_id: number;
    project_id: number;
    fork_id: number;
    visual_version_id: number;
    approved_asset_ids: string[];
    rejected_asset_ids: string[];
    blocked_asset_ids: string[];
    needs_review_asset_ids: string[];
    approved_candidate_ids: number[];
    rejected_candidate_id: number;
    blocked_candidate_id: number;
    needs_review_candidate_ids: number[];
    store_body: {
      spec: Record<string, unknown>;
      candidate: {
        asset_key: string;
        identity_lineage: Array<{ source_entity_hash: string }>;
      } & Record<string, unknown>;
      payload_base64: string;
    };
  };
}

test("derivative visual review panel: explicit approval, blocked locked, refresh consistent", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const user = uniqueUser("dv_review");
  await registerAndLogin(page, user);
  const seeded = qualificationCommand(["--e2e-seed-user", user.username]);

  // The writing studio hosts the review panel; the single seeded novel is
  // auto-selected and its six candidates (approved/rejected/blocked/
  // needs_review) appear in the owner-scoped queue.
  await page.goto("/writing");
  const panel = page.getByTestId("derivative-visual-review-panel");
  await expect(panel).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId("derivative-review-candidate")).toHaveCount(6);

  const detail = page.getByTestId("derivative-review-detail");
  await expect(detail).toBeVisible();
  // First candidate (ch-1) is approved; envelope exposes refs/namespace/score.
  await expect(detail).toHaveAttribute("data-review-state", "approved");
  await expect(panel.getByTestId("derivative-review-namespace")).toContainText(
    "fanfiction_visual"
  );
  await expect(panel.getByTestId("derivative-review-source-ref").first()).toBeVisible();
  await expect(panel.getByTestId("derivative-review-verdict")).toContainText("pass");

  // Rejected candidate (ch-2, index 1) surfaces its terminal-but-reviewable
  // state in the queue.
  await panel.getByTestId("derivative-review-candidate").nth(1).click();
  await expect(detail).toHaveAttribute("data-review-state", "rejected");

  // needs_review candidate (ch-5, index 4): approve requires an explicit
  // reason — the button stays disabled until one is typed.
  await panel.getByTestId("derivative-review-candidate").nth(4).click();
  await expect(detail).toHaveAttribute("data-review-state", "needs_review");
  const approve = panel.getByTestId("derivative-review-action-approve");
  await expect(approve).toBeDisabled();
  await panel.getByTestId("derivative-review-reason").fill("e2e explicit approval");
  await expect(approve).toBeEnabled();
  await approve.click();
  await expect(detail).toHaveAttribute("data-review-state", "approved", {
    timeout: 20_000,
  });

  // Refresh keeps the server-driven state consistent.
  await page.reload();
  await expect(panel).toBeVisible({ timeout: 30_000 });
  await panel.getByTestId("derivative-review-candidate").nth(4).click();
  await expect(detail).toHaveAttribute("data-review-state", "approved");

  // Blocked candidate (ch-4, index 3, identity drift) is locked: no action
  // bar at all — it can never be published.
  await panel.getByTestId("derivative-review-candidate").nth(3).click();
  await expect(detail).toHaveAttribute("data-review-state", "blocked");
  await expect(panel.getByTestId("derivative-review-locked")).toBeVisible();
  await expect(panel.getByTestId("derivative-review-locked")).toContainText(
    "不可发布"
  );
  await expect(panel.getByTestId("derivative-review-actions")).toHaveCount(0);

  // No horizontal page overflow on desktop.
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth + 1
    )
  ).toBe(true);
});

test("cross-owner review is a uniform 404 and source hash mutation can never store", async ({
  page,
  request,
}) => {
  test.setTimeout(180_000);
  const owner1 = uniqueUser("dv_owner_a");
  const owner2 = uniqueUser("dv_owner_b");
  await apiRegister(request, owner1);
  const login1 = await apiLogin(request, owner1.username, owner1.password);
  const token1 = (await login1.json()).access_token as string;
  const seeded = qualificationCommand(["--e2e-seed-user", owner1.username]);

  await apiRegister(request, owner2);
  const login2 = await apiLogin(request, owner2.username, owner2.password);
  const token2 = (await login2.json()).access_token as string;

  // Owner1 sees their six-candidate queue.
  const own = await request.get(
    `${BACKEND}/api/novels/${seeded.novel_id}/derivative-visual/review`,
    { headers: { Authorization: `Bearer ${token1}` } }
  );
  expect(own.status()).toBe(200);
  expect((await own.json()).total).toBe(6);

  // A foreign owner's request is an identical 404 (no owner enumeration).
  const foreign = await request.get(
    `${BACKEND}/api/novels/${seeded.novel_id}/derivative-visual/review`,
    { headers: { Authorization: `Bearer ${token2}` } }
  );
  expect(foreign.status()).toBe(404);

  // A blocked candidate (identity drift) can never be approved via the review
  // API: the state machine fails closed with a 409.
  const blockedReview = await request.post(
    `${BACKEND}/api/novels/${seeded.novel_id}/derivative-visual/review/${seeded.blocked_candidate_id}/action`,
    {
      headers: { Authorization: `Bearer ${token1}` },
      data: {
        event_key: "e2e-blocked-approve",
        action: "approve",
        actor_source: "human",
        actor: "editor",
        reason: "ignore drift",
        from_review_state: "blocked",
      },
    }
  );
  expect(blockedReview.status()).toBe(409);

  // Source hash mutation: replay the frozen chapter-1 store body with a mutated
  // identity source hash -> the store gate fails closed (409) before any byte
  // or row is written, so a mutated source hash can never become publishable.
  const body = structuredClone(seeded.store_body);
  body.candidate.identity_lineage[0].source_entity_hash = "e".repeat(64);
  const store = await request.post(
    `${BACKEND}/api/novels/${seeded.novel_id}/derivative-visual/assets`,
    {
      headers: { Authorization: `Bearer ${token1}` },
      data: body,
    }
  );
  expect(store.status()).toBe(409);
  expect(await store.text()).toContain("identity_lineage_mismatch");

  // The rejected candidate is absent from the published set (approval only).
  const published = await request.get(
    `${BACKEND}/api/novels/${seeded.novel_id}/derivative-visual/assets`,
    { headers: { Authorization: `Bearer ${token1}` } }
  );
  expect(published.status()).toBe(200);
  const publishedIds = (await published.json()).items.map(
    (item: { asset_id: string }) => item.asset_id
  ) as string[];
  for (const assetId of seeded.approved_asset_ids) {
    expect(publishedIds).toContain(assetId);
  }
  expect(publishedIds).not.toContain(seeded.rejected_asset_ids[0]);
  expect(publishedIds).not.toContain(seeded.blocked_asset_ids[0]);

  // 390px mobile viewport: the review panel remains usable and locked states
  // render without a horizontal overflow.
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, owner1.username, owner1.password);
  await page.goto("/writing");
  const panel = page.getByTestId("derivative-visual-review-panel");
  await expect(panel).toBeVisible({ timeout: 30_000 });
  await panel.getByTestId("derivative-review-candidate").nth(3).click();
  await expect(panel.getByTestId("derivative-review-locked")).toBeVisible();
  expect(
    await page.evaluate(
      () =>
        document.documentElement.scrollWidth <=
        document.documentElement.clientWidth + 1
    )
  ).toBe(true);
});
