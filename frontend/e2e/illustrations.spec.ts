/**
 * Phase 33-04 — Illustration review gallery browser evidence (REQ-VIS-04, D-33-01..D-33-04).
 *
 * Proves the browser-visible consequences of the review workflow:
 *   - desktop + 390px mobile can inspect the generation candidate gallery with
 *     job status, approval state, the fail-closed proposal gate and the
 *     consistency verdict;
 *   - explicit approve/reject/supersede actions update the approval badge and
 *     the state is restored from the server envelope after a reload (no
 *     client-side truth);
 *   - the lineage/compare drawer surfaces job/attempt/budget evidence and the
 *     consistency report;
 *   - provider failure stays explicit (error + reason) with a retry action —
 *     a failure is never an empty success;
 *   - candidate-only: nothing in the UI presents a generated candidate as
 *     reader-visible canon.
 *
 * Routes are mocked (no real backend); the gallery URL is
 * `/novels/{novelId}/illustrations` — the page integration slot that later
 * plans mount IllustrationGallery into. NOTE: on this machine the Next 16
 * canary dev server fails to compile (pre-existing), so this spec is kept
 * structurally valid and executed by the verification sub-agent when the
 * environment allows.
 */
import { expect, test, type Page } from "@playwright/test";

const H = (n: number) => String(n).repeat(64);

interface GalleryItemMock {
  asset: Record<string, unknown>;
  job: Record<string, unknown>;
  consistency: Record<string, unknown> | null;
  review_events: Array<Record<string, unknown>>;
  approval_gate: Record<string, unknown> | null;
}

interface GalleryMock {
  items: GalleryItemMock[];
  total: number;
}

interface MockStore {
  gallery: GalleryMock;
  reviewFailed: boolean;
}

function makeAsset(reviewState = "candidate", over: Record<string, unknown> = {}) {
  return {
    id: 1,
    owner_id: 1,
    novel_id: 11,
    job_id: 1,
    revision_key: "job-arin:rev1",
    revision_number: 1,
    asset_id: "asset-1",
    mime_type: "image/png",
    width: 1024,
    height: 1024,
    size_bytes: 4096,
    bytes_hash: H(1),
    scene_spec_hash: H(2),
    prompt_revision_id: 1,
    prompt_revision_hash: H(3),
    visual_bible_revision_hash: H(4),
    source_snapshot_id: "ss-1",
    source_snapshot_hash: H(5),
    cutoff_chapter: 8,
    provider: "mock",
    provider_model: "mock-img-v1",
    provider_request_id: "mock-req-1",
    rights_status: "cleared",
    approval_state: reviewState,
    ...over,
  };
}

function makeJob(over: Record<string, unknown> = {}) {
  return {
    id: 1,
    owner_id: 1,
    novel_id: 11,
    job_key: "job-arin",
    idempotency_key: H(6),
    status: "succeeded",
    status_reason: "generated",
    error_code: null,
    retry_count: 0,
    scene_spec_hash: H(2),
    prompt_revision_id: 1,
    prompt_revision_hash: H(3),
    visual_bible_revision_hash: H(4),
    source_snapshot_id: "ss-1",
    source_snapshot_hash: H(5),
    cutoff_chapter: 8,
    config_hash: H(7),
    price_snapshot: { provider: "mock", model: "mock-img-v1" },
    ...over,
  };
}

function makeReport() {
  return {
    id: 1,
    owner_id: 1,
    novel_id: 11,
    asset_revision_id: 1,
    report_key: "arin:ch1",
    evaluator_id: "illustration-consistency.fixture.v1",
    evaluator_version: "1.0.0",
    model_lineage: {},
    fixture_set_hash: H(8),
    reference_asset_ids: ["ref-char-arin-1"],
    scores: { identity: 1.0, style: 1.0, negative_constraint_violations: 0 },
    verdict: "pass",
    details: {},
    idempotency_key: H(9),
    schema_version: "illustration-consistency.v1",
    created_at: null,
  };
}

function makeEnvelope(reviewState = "candidate") {
  return {
    asset: makeAsset(reviewState),
    job: makeJob(),
    attempts: [
      {
        id: 1,
        attempt_number: 1,
        status: "succeeded",
        provider_request_id: "mock-req-1",
        request_hash: H(10),
        response_hash: H(1),
        usage: { input_tokens: 120, output_tokens: 1024 },
        cost_usd: "0.04000000",
        latency_ms: 12,
        error_code: null,
      },
    ],
    budget: {
      settled_calls: 1,
      settled_cost_usd: "0.04000000",
      reservation_status: "settled",
      settled_usage: {
        input_tokens: 120,
        output_tokens: 1024,
        cost_usd: "0.04",
        usage_unknown: false,
      },
      price_snapshot: { provider: "mock", model: "mock-img-v1" },
      ledger_max_calls: 10,
      ledger_max_cost_usd: "1.00000000",
    },
    consistency: makeReport(),
    review_events:
      reviewState === "candidate"
        ? []
        : [
            {
              event_key: "ev-approve",
              action: "approve",
              actor_source: "human",
              actor: "owner",
              reason: "人工审查：批准为提案",
              from_approval_state: "candidate",
              to_approval_state: reviewState,
            },
          ],
    approval_gate:
      reviewState === "candidate"
        ? { ok: true, reason_code: null, detail: null }
        : null,
  };
}

function makeGallery(
  reviewState = "candidate",
  over: Partial<GalleryItemMock> = {}
): GalleryMock {
  return {
    items: [
      {
        asset: makeAsset(reviewState),
        job: makeJob(),
        consistency: makeReport(),
        review_events:
          reviewState === "candidate"
            ? []
            : [
                {
                  event_key: "ev-approve",
                  action: "approve",
                  actor_source: "human",
                  actor: "owner",
                  reason: "人工审查：批准为提案",
                  from_approval_state: "candidate",
                  to_approval_state: reviewState,
                },
              ],
        approval_gate:
          reviewState === "candidate"
            ? { ok: true, reason_code: null, detail: null }
            : null,
        ...over,
      },
    ],
    total: 1,
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

  // Candidate gallery + review envelope + explicit approval + retry.
  await page.route("**/api/novels/11/illustrations/gallery", (route) =>
    route.fulfill({ json: store.gallery })
  );
  await page.route("**/api/novels/11/illustrations/assets/1/review", async (route) => {
    const request = route.request();
    if (request.method() === "POST") {
      if (store.reviewFailed) {
        return route.fulfill({ status: 409, json: { detail: "illegal review action" } });
      }
      const body = request.postDataJSON() as { action: string };
      const nextState =
        body.action === "approve"
          ? "proposal_ready"
          : body.action === "reject"
            ? "rejected"
            : body.action === "supersede"
              ? "superseded"
              : "candidate";
      store.gallery = makeGallery(nextState);
      return route.fulfill({
        json: { asset: makeAsset(nextState), envelope: makeEnvelope(nextState) },
      });
    }
    return route.fulfill({ json: makeEnvelope() });
  });
  await page.route("**/api/novels/11/illustrations/jobs/1/retry", async (route) => {
    store.gallery.items[0].job = makeJob({
      status: "queued",
      status_reason: "re-queued",
      error_code: null,
    });
    return route.fulfill({ json: store.gallery.items[0].job });
  });
}

async function openGallery(page: Page) {
  await page.goto("/novels/11/illustrations");
  const gallery = page.getByTestId("illustration-gallery");
  await expect(gallery).toBeVisible({ timeout: 30_000 });
  return gallery;
}

test("desktop: candidate gallery shows status, gate, verdict and actions", async ({
  page,
}) => {
  const store: MockStore = { gallery: makeGallery("candidate"), reviewFailed: false };
  await mockApp(page, store);
  const gallery = await openGallery(page);

  await expect(gallery.getByTestId("illustration-candidate-only")).toBeVisible();
  const card = gallery.getByTestId("illustration-card");
  await expect(card).toHaveAttribute("data-approval-state", "candidate");
  await expect(card).toHaveAttribute("data-job-status", "succeeded");
  await expect(card.getByTestId("illustration-approval-gate")).toContainText(
    "提案门已满足"
  );
  await expect(card.getByTestId("illustration-consistency-verdict")).toContainText(
    "一致"
  );
  await expect(card.getByTestId("illustration-approval-approve")).toBeVisible();
});

test("mobile 390: gallery is reachable and compare/lineage is inspectable", async ({
  page,
}) => {
  const store: MockStore = { gallery: makeGallery("candidate"), reviewFailed: false };
  await mockApp(page, store);
  const gallery = await openGallery(page);

  await expect(gallery.getByTestId("illustration-card")).toBeVisible();
  await gallery.getByTestId("illustration-expand-lineage").click();
  await expect(gallery.getByTestId("illustration-compare")).toBeVisible();
  await expect(gallery.getByTestId("illustration-lineage-drawer")).toBeVisible();
  await expect(gallery.getByTestId("illustration-budget-evidence")).toContainText(
    "已结算成本"
  );
  await expect(gallery.getByTestId("illustration-attempt")).toContainText(
    "尝试 #1 · succeeded"
  );
});

test("explicit approval updates the badge and restores state after reload", async ({
  page,
}) => {
  const store: MockStore = { gallery: makeGallery("candidate"), reviewFailed: false };
  await mockApp(page, store);
  const gallery = await openGallery(page);

  await gallery.getByTestId("illustration-approval-approve").click();
  const card = gallery.getByTestId("illustration-card");
  await expect(card).toHaveAttribute("data-approval-state", "proposal_ready", {
    timeout: 15_000,
  });
  await expect(card.getByTestId("illustration-review-event")).toContainText(
    "candidate → proposal_ready"
  );

  // State is restored from the server envelope, not client memory.
  await page.reload();
  const reloaded = page.getByTestId("illustration-gallery");
  await expect(reloaded.getByTestId("illustration-card")).toHaveAttribute(
    "data-approval-state",
    "proposal_ready"
  );
});

test("proposal_ready offers supersede/reject; supersede locks the card", async ({
  page,
}) => {
  const store: MockStore = {
    gallery: makeGallery("proposal_ready"),
    reviewFailed: false,
  };
  await mockApp(page, store);
  const gallery = await openGallery(page);

  await gallery.getByTestId("illustration-approval-supersede").click();
  await expect(gallery.getByTestId("illustration-card")).toHaveAttribute(
    "data-approval-state",
    "superseded",
    { timeout: 15_000 }
  );
  await expect(gallery.getByTestId("illustration-approval-locked")).toBeVisible();
});

test("provider failure stays explicit with retry, never empty success", async ({
  page,
}) => {
  const failedJob = makeJob({
    status: "failed",
    status_reason: "provider returned an unusable asset",
    error_code: "empty_asset",
  });
  const store: MockStore = {
    gallery: makeGallery("candidate", { job: failedJob }),
    reviewFailed: false,
  };
  await mockApp(page, store);
  const gallery = await openGallery(page);

  const card = gallery.getByTestId("illustration-card");
  await expect(card).toHaveAttribute("data-job-status", "failed");
  await expect(card.getByTestId("illustration-job-error")).toContainText(
    "empty_asset"
  );
  await expect(card.getByTestId("illustration-job-reason")).toContainText(
    "unusable asset"
  );

  // Explicit retry re-queues the job with the original frozen lineage.
  await card.getByTestId("illustration-retry").click();
  await expect(card).toHaveAttribute("data-job-status", "queued", {
    timeout: 15_000,
  });
  await expect(card.getByTestId("illustration-job-reason")).toContainText(
    "re-queued"
  );
});

test("failed review fails closed with a visible error, never empty-success", async ({
  page,
}) => {
  const store: MockStore = {
    gallery: makeGallery("candidate"),
    reviewFailed: true,
  };
  await mockApp(page, store);
  const gallery = await openGallery(page);

  await gallery.getByTestId("illustration-approval-approve").click();
  await expect(page.getByTestId("illustration-error")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByTestId("illustration-error")).toContainText(
    "illegal review action"
  );
});
