/**
 * Phase 31-03 — Key Scene human review browser evidence (REQ-VIS-02, D-31-01..D-31-05).
 *
 * Proves the browser-visible consequences of the candidate review workspace:
 *   - desktop + 390px mobile can review a key-scene candidate set with
 *     evidence ranges, salience/diversity reasons, coordinates and cutoff;
 *   - evidence jump lands on the reader chapter range;
 *   - explicit approve/reject actions update the candidate state and the
 *     frozen set only ever surfaces approved candidates;
 *   - spoiler safety: only server-provided envelope fields are rendered — no
 *     future-chapter thumbnail or cover fields exist in the workspace;
 *   - failed review fails closed with a visible error (no empty-success).
 *
 * Routes are mocked (no real backend); the workspace URL is
 * `/novels/{novelId}/key-scenes/{setId}` — the page integration slot that
 * later plans mount KeySceneReviewWorkspace into. NOTE: on this machine the
 * Next 16 canary dev server fails to compile (pre-existing), so this spec is
 * kept structurally valid and executed by the verification sub-agent when the
 * environment allows.
 */
import { expect, test, type Page } from "@playwright/test";

const H = (n: number) => String(n).repeat(64);

interface MockStore {
  set: Record<string, unknown>;
  reviewFailed: boolean;
  freezeFailed: boolean;
}

function evidenceRef(over: Record<string, unknown> = {}) {
  return {
    evidence_key: "ev-1",
    source_snapshot_id: "ss-1",
    source_snapshot_hash: H(2),
    chapter_id: 101,
    chapter_number: 1,
    source_start: 6,
    source_end: 10,
    content_hash: H(3),
    excerpt: "Arin drew his sword as the rain fell hard across the courtyard.",
    cutoff_chapter: 2,
    ...over,
  };
}

function makeCandidate(key: string, over: Record<string, unknown> = {}) {
  return {
    candidate_key: key,
    candidate_order: Number(key.split("-").pop() ?? 0),
    scene_id: `scene-${key}`,
    chapter_id: 101 + Number(key.split("-").pop() ?? 0),
    chapter_number: 1 + Number(key.split("-").pop() ?? 0),
    source_start: 6,
    source_end: 10,
    source_hash: H(4),
    coordinates: { cast: ["arin"], place: "courtyard", time: "night", pov: "arin" },
    spoiler_cutoff: 2,
    salience_reasons: [
      { reason_code: "plot_turn", detail: "黎明进攻", score: 0.9 },
      { reason_code: "dialogue_turn", detail: "对话转折", score: 0.6 },
    ],
    score_total: 0.82,
    score_breakdown: { plot_turn: 0.9, dialogue_turn: 0.6 },
    diversity_key: H(7),
    detector_id: "key-scene.v1",
    detector_version: "1.0.0",
    policy_hash: H(8),
    evidence_ranges: [evidenceRef()],
    heuristic_signal: {
      availability: "available",
      speaker_offsets: [{ offset_start: 7, offset_end: 8, speaker_key: "arin" }],
      dialogue_offsets: [{ offset_start: 8, offset_end: 9 }],
      confidence: 0.9,
      warnings: [],
      detector_id: "key-scene.v1",
      detector_version: "1.0.0",
    },
    review_state: "candidate",
    ...over,
  };
}

function makeSet(reviewState = "candidate", over: Record<string, unknown> = {}) {
  const candidates = [
    makeCandidate("ks-v1-0"),
    makeCandidate("ks-v1-1", {
      chapter_number: 2,
      scene_id: "scene-ks-v1-1",
      coordinates: { cast: ["arin"], place: "harbor", time: "night", pov: "arin" },
      salience_reasons: [{ reason_code: "quiet_emotional", detail: "安静情感", score: 0.7 }],
      heuristic_signal: {
        availability: "unavailable",
        speaker_offsets: [],
        dialogue_offsets: [],
        confidence: null,
        warnings: ["no_dialogue_detected"],
        detector_id: "key-scene.v1",
        detector_version: "1.0.0",
      },
    }),
  ];
  return {
    id: 1,
    owner_id: 1,
    novel_id: 11,
    version_key: "ks-main",
    revision_number: 1,
    parent_set_id: null,
    source_snapshot_id: "ss-1",
    source_snapshot_hash: H(2),
    cutoff_chapter: 2,
    schema_version: "key-scene.v1",
    schema_hash: H(6),
    policy_hash: H(8),
    detector_id: "key-scene.v1",
    detector_version: "1.0.0",
    manifest_hash: H(10),
    approved_visual_bible_revision_id: null,
    approved_visual_bible_revision_hash: null,
    review_state: reviewState,
    candidates,
    review_decisions:
      reviewState === "candidate"
        ? []
        : [
            {
              decision_key: "ds-approve",
              action: "approve",
              actor_source: "human",
              actor: "owner",
              reason: "人工审查：批准",
              from_review_state: "candidate",
              to_review_state: "approved",
              candidate_key: "ks-v1-0",
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

  await page.route("**/api/novels/11/key-scenes", (route) =>
    route.fulfill({
      json: { items: [store.set], total: 1 },
    })
  );
  await page.route("**/api/novels/11/key-scenes/1", (route) =>
    route.fulfill({ json: store.set })
  );
  await page.route("**/api/novels/11/key-scenes/1/review", async (route) => {
    if (store.reviewFailed) {
      return route.fulfill({ status: 409, json: { detail: "illegal review action" } });
    }
    const body = route.request().postDataJSON() as {
      action: string;
      candidate_key: string;
    };
    const candidateKey = body.candidate_key;
    const candidates = (store.set.candidates as Array<Record<string, unknown>>).map(
      (c) =>
        c.candidate_key === candidateKey
          ? { ...c, review_state: body.action === "approve" ? "approved" : "rejected" }
          : c
    );
    store.set = {
      ...store.set,
      candidates,
      review_decisions: [
        ...((store.set.review_decisions as Array<Record<string, unknown>> | undefined) ?? []),
        {
          decision_key: `ds-${body.action}-${Date.now()}`,
          action: body.action,
          actor_source: "human",
          actor: "owner",
          from_review_state: "candidate",
          to_review_state: body.action === "approve" ? "approved" : "rejected",
          candidate_key: candidateKey,
          reason: `人工审查：${body.action}`,
        },
      ],
    };
    return route.fulfill({ json: { set: store.set } });
  });
  await page.route("**/api/novels/11/key-scenes/1/freeze", async (route) => {
    if (store.freezeFailed) {
      return route.fulfill({ status: 409, json: { detail: "no_approved_candidates" } });
    }
    const approved = (store.set.candidates as Array<Record<string, unknown>>).filter(
      (c) => c.review_state === "approved"
    );
    const frozen = {
      ...store.set,
      review_state: "approved",
      manifest_hash: H(11),
      candidates: approved,
    };
    store.set = { ...store.set, review_state: "approved" };
    return route.fulfill({ json: { set: store.set, frozen } });
  });
  await page.route("**/api/novels/11/key-scenes/1/frozen", (route) =>
    route.fulfill({
      json: {
        ...store.set,
        manifest_hash: H(11),
        candidates: (store.set.candidates as Array<Record<string, unknown>>).filter(
          (c) => c.review_state === "approved"
        ),
      },
    })
  );
}

async function openWorkspace(page: Page) {
  await page.goto("/novels/11/key-scenes/1");
  const workspace = page.getByTestId("key-scene-workspace");
  await expect(workspace).toBeVisible({ timeout: 30_000 });
  return workspace;
}

test("desktop: candidate review shows evidence, reasons, coordinates and gates", async ({
  page,
}) => {
  const store: MockStore = {
    set: makeSet("candidate"),
    reviewFailed: false,
    freezeFailed: false,
  };
  await mockApp(page, store);
  const workspace = await openWorkspace(page);

  await expect(workspace).toHaveAttribute("data-review-state", "candidate");
  await expect(workspace.getByTestId("key-scene-candidate-only")).toBeVisible();
  await expect(workspace.getByTestId("key-scene-candidate")).toHaveCount(2);
  await expect(workspace.getByTestId("key-scene-evidence-panel").first()).toContainText(
    "第 1 章 · 范围 6–10"
  );
  await expect(workspace.getByTestId("key-scene-evidence-panel").first()).toContainText(
    "截止第 2 章"
  );
  await expect(workspace.getByText("剧情转折")).toBeVisible();
  await expect(workspace.getByText("安静情感").first()).toBeVisible();
  // Freeze stays gated until a candidate is approved.
  await expect(workspace.getByTestId("key-scene-freeze")).toBeDisabled();
});

test("mobile 390: candidate review is reachable and evidence is inspectable", async ({
  page,
}) => {
  const store: MockStore = {
    set: makeSet("candidate"),
    reviewFailed: false,
    freezeFailed: false,
  };
  await mockApp(page, store);
  const workspace = await openWorkspace(page);

  await expect(workspace.getByTestId("key-scene-candidate").first()).toBeVisible();
  await expect(workspace.getByTestId("key-scene-evidence-jump").first()).toBeVisible();
  await expect(workspace.getByTestId("key-scene-review-approve").first()).toBeVisible();
  await expect(workspace.getByTestId("key-scene-freeze")).toBeVisible();
});

test("evidence jump lands on the reader chapter range", async ({ page }) => {
  const store: MockStore = {
    set: makeSet("candidate"),
    reviewFailed: false,
    freezeFailed: false,
  };
  await mockApp(page, store);
  const workspace = await openWorkspace(page);

  await workspace.getByTestId("key-scene-evidence-jump").first().click();
  await expect(page).toHaveURL(/chapter=101&start=6/);
});

test("approve and freeze produce an approved-only frozen set", async ({ page }) => {
  const store: MockStore = {
    set: makeSet("candidate"),
    reviewFailed: false,
    freezeFailed: false,
  };
  await mockApp(page, store);
  const workspace = await openWorkspace(page);

  await workspace.getByTestId("key-scene-review-approve").first().click();
  await expect(
    workspace.getByTestId("key-scene-candidate").first()
  ).toHaveAttribute("data-review-state", "approved", { timeout: 15_000 });
  await expect(workspace.getByTestId("key-scene-freeze")).toBeEnabled();

  await workspace.getByTestId("key-scene-freeze").click();
  await expect(workspace).toHaveAttribute("data-review-state", "approved", {
    timeout: 15_000,
  });
  await expect(workspace.getByTestId("key-scene-frozen-manifest")).toBeVisible();
  await expect(workspace.getByTestId("key-scene-frozen-candidate")).toHaveCount(1);
  // The quiet/unapproved candidate never enters the frozen manifest.
  await expect(workspace.getByTestId("key-scene-frozen-manifest")).not.toContainText(
    "第 2 章"
  );
});

test("reject keeps the candidate in history but out of the approved set", async ({
  page,
}) => {
  const store: MockStore = {
    set: makeSet("candidate"),
    reviewFailed: false,
    freezeFailed: false,
  };
  await mockApp(page, store);
  const workspace = await openWorkspace(page);

  await workspace.getByTestId("key-scene-review-reject").first().click();
  await expect(
    workspace.getByTestId("key-scene-candidate").first()
  ).toHaveAttribute("data-review-state", "rejected", { timeout: 15_000 });
  // The rejected candidate stays visible in the review list (auditable history).
  await expect(workspace.getByTestId("key-scene-candidate")).toHaveCount(2);
  await expect(workspace.getByTestId("key-scene-review-event").first()).toHaveAttribute(
    "data-action",
    "reject"
  );
});

test("failed review fails closed with a visible error, never empty-success", async ({
  page,
}) => {
  const store: MockStore = {
    set: makeSet("candidate"),
    reviewFailed: true,
    freezeFailed: false,
  };
  await mockApp(page, store);
  const workspace = await openWorkspace(page);

  await workspace.getByTestId("key-scene-review-approve").first().click();
  // On review failure the workspace renders its error state in place of the
  // workspace, so the error lives at page level, not inside the workspace.
  await expect(page.getByTestId("key-scene-error")).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.getByTestId("key-scene-error")).toContainText(
    "illegal review action"
  );
});

test("spoiler-safe list: only server-provided envelope fields render", async ({
  page,
}) => {
  const store: MockStore = {
    set: makeSet("candidate"),
    reviewFailed: false,
    freezeFailed: false,
  };
  await mockApp(page, store);
  const workspace = await openWorkspace(page);

  await expect(workspace.getByTestId("key-scene-candidate")).toHaveCount(2);
  // No thumbnail/cover/downstream fields exist in the workspace.
  await expect(workspace.getByTestId("key-scene-thumbnail")).toHaveCount(0);
  await expect(workspace.getByTestId("key-scene-cover")).toHaveCount(0);
});
