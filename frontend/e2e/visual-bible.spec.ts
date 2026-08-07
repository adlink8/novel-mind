/**
 * Phase 30-03 — Visual Bible workspace browser evidence (REQ-VIS-01, D-30-01..D-30-04).
 *
 * Proves the browser-visible consequences of the candidate workspace:
 *   - desktop + 390px mobile can inspect a Visual Bible candidate with
 *     authority labels, evidence refs and review state;
 *   - evidence jump lands on the reader chapter range;
 *   - reference asset rights/approval status is visible and never silently
 *     presented as canon;
 *   - an explicit review action updates the review badge and the state is
 *     restored from the server envelope after a reload (no client-side truth);
 *   - failed review / unresolved states fail closed instead of empty-success.
 *
 * Routes are mocked (no real backend); the workspace URL is
 * `/novels/{novelId}/visual-bible` — the page integration slot that later
 * plans mount VisualBibleEntitySheet into. NOTE: on this machine the Next
 * 16 canary dev server fails to compile (pre-existing), so this spec is kept
 * structurally valid and executed by the verification sub-agent when the
 * environment allows.
 */
import { expect, test, type Page } from "@playwright/test";

const H = (n: number) => String(n).repeat(64);

interface MockStore {
  version: Record<string, unknown>;
  reviewFailed: boolean;
}

function evidenceRef() {
  return {
    evidence_key: "ev-1",
    source_snapshot_id: "snap-1",
    source_snapshot_hash: H(2),
    chapter_id: 101,
    chapter_number: 1,
    source_start: 6,
    source_end: 10,
    content_hash: H(3),
    excerpt: "走进竹林",
    cutoff_chapter: 2,
  };
}

function makeVersion(reviewState = "candidate", over: Record<string, unknown> = {}) {
  return {
    id: 1,
    owner_id: 1,
    novel_id: 11,
    version_key: "vb-v1",
    revision_number: 1,
    parent_version_id: null,
    source_snapshot_id: "snap-1",
    source_snapshot_hash: H(2),
    cutoff_chapter: 2,
    schema_version: "visual-bible.v1",
    schema_hash: H(6),
    policy_hash: H(7),
    manifest_hash: H(8),
    review_state: reviewState,
    style_profile: null,
    constraints: null,
    entities: [
      {
        stable_id: "lin-an",
        entity_key: "林安",
        entity_type: "character",
        description: "银发青年，常年披深青色斗篷。",
        authority: "canon_fact",
        disclosure_cutoff: 2,
        claims: [
          {
            claim_key: "cl-canon",
            entity_stable_id: "lin-an",
            authority: "canon_fact",
            description: "林安是临安城主",
            author: null,
            rationale: null,
            cutoff_chapter: 2,
            claim_hash: H(4),
            evidence_refs: [evidenceRef()],
          },
        ],
      },
    ],
    reference_assets: [
      {
        asset_key: "ref-lin-an",
        asset_id: "asset-1",
        mime_type: "image/png",
        bytes_hash: H(5),
        rights_status: "unreviewed",
        approved: false,
      },
    ],
    review_events:
      reviewState === "candidate"
        ? []
        : [
            {
              action: "approve",
              actor_source: "human",
              actor: "owner",
              reason: "人工审查：批准",
              event_key: "ev-approve",
              from_review_state: "candidate",
              to_review_state: reviewState,
            },
          ],
    ...over,
  };
}

async function mockApp(page: Page, store: MockStore) {
  await page.route("**/api/**", (route) =>
    route.fulfill({ status: 500, json: { detail: "unmocked e2e endpoint" } })
  );

  await page.route("**/api/auth/me", (route) =>
    route.fulfill({
      json: { id: 1, username: "e2e", email: "e2e@example.com", is_active: true },
    })
  );
  const novel = {
    id: 11,
    title: "雾城夜读",
    author: "佚名",
    description: null,
    genre: null,
    word_count: 120,
    chapter_count: 2,
    status: "ready",
    reading_progress: { chapter_id: 101, progress_percent: 10 },
    created_at: "",
    updated_at: "",
  };
  await page.route("**/api/novels", (route) =>
    route.fulfill({ json: { items: [novel], total: 1 } })
  );
  await page.route("**/api/novels/11", (route) =>
    route.fulfill({ json: novel })
  );
  await page.route("**/api/novels/11/chapters", (route) =>
    route.fulfill({ json: [] })
  );
  await page.route("**/api/novels/11/progress", (route) =>
    route.fulfill({ status: 200, json: {} })
  );

  // Visual Bible candidate envelope + explicit review action.
  await page.route("**/api/novels/11/visual-bible", (route) =>
    route.fulfill({
      json: { items: [store.version], total: 1 },
    })
  );
  await page.route("**/api/novels/11/visual-bible/1", (route) =>
    route.fulfill({ json: store.version })
  );
  await page.route("**/api/novels/11/visual-bible/1/review", async (route) => {
    if (store.reviewFailed) {
      return route.fulfill({ status: 409, json: { detail: "illegal review action" } });
    }
    const body = route.request().postDataJSON() as { action: string };
    store.version = makeVersion(
      body.action === "approve" ? "approved" : "rejected"
    );
    return route.fulfill({ json: store.version });
  });
}

async function openWorkspace(page: Page) {
  await page.goto("/novels/11/visual-bible");
  const sheet = page.getByTestId("visual-bible-entity-sheet");
  await expect(sheet).toBeVisible({ timeout: 30_000 });
  return sheet;
}

test("desktop: candidate review shows authority, evidence and rights gates", async ({
  page,
}) => {
  const store: MockStore = { version: makeVersion("candidate"), reviewFailed: false };
  await mockApp(page, store);
  const sheet = await openWorkspace(page);

  await expect(sheet).toHaveAttribute("data-review-state", "candidate");
  await expect(sheet.getByTestId("visual-bible-candidate-only")).toBeVisible();
  await expect(sheet.getByTestId("visual-bible-entity")).toContainText("林安");
  await expect(sheet.getByTestId("visual-bible-authority-badge").first()).toContainText(
    "正典事实"
  );
  await expect(sheet.getByTestId("visual-bible-evidence-panel")).toContainText(
    "第 1 章 · 范围 6–10"
  );
  await expect(sheet.getByTestId("visual-bible-asset-not-approved")).toBeVisible();
});

test("mobile 390: candidate review is reachable and evidence is inspectable", async ({
  page,
}) => {
  const store: MockStore = { version: makeVersion("candidate"), reviewFailed: false };
  await mockApp(page, store);
  const sheet = await openWorkspace(page);

  await expect(sheet.getByTestId("visual-bible-entity")).toBeVisible();
  await expect(sheet.getByTestId("visual-bible-evidence-jump")).toBeVisible();
  await expect(sheet.getByTestId("visual-bible-review-actions")).toBeVisible();
});

test("evidence jump lands on the reader chapter range", async ({ page }) => {
  const store: MockStore = { version: makeVersion("candidate"), reviewFailed: false };
  await mockApp(page, store);
  const sheet = await openWorkspace(page);

  await sheet.getByTestId("visual-bible-evidence-jump").click();
  await expect(page).toHaveURL(/chapter=101&start=6/);
});

test("explicit approval updates the badge and restores state after reload", async ({
  page,
}) => {
  const store: MockStore = { version: makeVersion("candidate"), reviewFailed: false };
  await mockApp(page, store);
  const sheet = await openWorkspace(page);

  await sheet.getByTestId("visual-bible-review-action-approve").click();
  await expect(sheet).toHaveAttribute("data-review-state", "approved", {
    timeout: 15_000,
  });
  await expect(sheet.getByTestId("visual-bible-review-state-badge")).toHaveText(
    "已批准"
  );
  // Candidate-only banner disappears only for approved (D-30-01).
  await expect(
    sheet.getByTestId("visual-bible-candidate-only")
  ).toHaveCount(0);

  // State is restored from the server envelope, not client memory.
  await page.reload();
  const reloaded = page.getByTestId("visual-bible-entity-sheet");
  await expect(reloaded).toHaveAttribute("data-review-state", "approved");
});

test("failed review fails closed with a visible error, never empty-success", async ({
  page,
}) => {
  const store: MockStore = {
    version: makeVersion("candidate"),
    reviewFailed: true,
  };
  await mockApp(page, store);
  const sheet = await openWorkspace(page);

  await sheet.getByTestId("visual-bible-review-action-approve").click();
  // On review failure the sheet renders its error state in place of the
  // entity sheet, so the error lives at page level, not inside the sheet.
  await expect(page.getByTestId("visual-bible-error")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("visual-bible-error")).toContainText(
    "illegal review action"
  );
});

test("needs_relink is visible and never presented as approved", async ({ page }) => {
  const store: MockStore = {
    version: makeVersion("needs_relink"),
    reviewFailed: false,
  };
  await mockApp(page, store);
  const sheet = await openWorkspace(page);

  await expect(sheet.getByTestId("visual-bible-review-state-badge")).toHaveText(
    "需要重新关联"
  );
  await expect(sheet.getByTestId("visual-bible-candidate-only")).toBeVisible();
  await expect(sheet.getByTestId("visual-bible-asset-not-approved")).toBeVisible();
});
