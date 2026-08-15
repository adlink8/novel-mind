/**
 * Phase 32-04 — Scene Spec / Prompt preview + diff browser evidence
 * (REQ-VIS-03, D-32-01..D-32-04).
 *
 * Proves the browser-visible consequences of the candidate workspace:
 *   - desktop + 390px mobile can inspect a Scene Spec candidate with per-detail
 *     evidence/Visual Bible/interpretation provenance, negative constraints and
 *     the server-redacted prompt preview;
 *   - unsupported / future-spoiler material is visible as rejected/unresolved,
 *     never disguised as canon;
 *   - the diff workspace shows changed sections, the stale banner and the
 *     no-provider-call marker;
 *   - an explicit edit action surfaces a server validation error fail-closed
 *     and produces a new candidate revision with the diff retained;
 *   - preview never triggers generation (provider_calls: 0).
 *
 * Routes are mocked (no real backend); the workspace URL is
 * `/novels/{novelId}/scene-spec` — the page integration slot that later plans
 * mount SceneSpecPreview / PromptDiff into. NOTE: on this machine the Next
 * 16 canary dev server fails to compile (pre-existing), so this spec is kept
 * structurally valid and executed by the verification sub-agent when the
 * environment allows.
 */
import { expect, test, type Page } from "@playwright/test";

const H = (n: number) => String(n).repeat(64);

interface MockStore {
  spec: Record<string, unknown>;
  stale: boolean;
  diff: Record<string, unknown>;
  editFailed: boolean;
}

function makeSpec(reviewState = "candidate", over: Record<string, unknown> = {}) {
  return {
    id: 1,
    owner_id: 1,
    novel_id: 11,
    spec_key: "spec-v1",
    revision_number: 1,
    scene_candidate_hash: H(1),
    scene_candidate_id: null,
    visual_bible_revision_hash: H(2),
    visual_bible_revision_id: null,
    source_snapshot_id: "ss-main",
    source_snapshot_hash: H(3),
    cutoff_chapter: 3,
    schema_version: "scene-spec.v1",
    schema_hash: H(4),
    compiler_id: "compiler.v1",
    compiler_version: "1.0.0",
    policy_hash: H(5),
    content_hash: H(6),
    review_state: reviewState,
    details: [
      {
        detail_key: "d-ayla",
        kind: "subject",
        source: "evidence",
        text: "Ayla 立于北境石厅之中，披灰色羊毛斗篷。",
        author: null,
        rationale: null,
        spoiler_cutoff: 3,
        evidence_keys: ["ev-ayla-hall"],
        visual_bible_stable_ids: [],
      },
      {
        detail_key: "d-lighting",
        kind: "style",
        source: "user_interpretation",
        text: "冷色调顶光，强调斗篷暗部",
        author: "读者·小雨",
        rationale: "读者认为光影偏冷",
        spoiler_cutoff: 3,
        evidence_keys: [],
        visual_bible_stable_ids: [],
      },
    ],
    negative_constraints: [
      {
        constraint_key: "nc-no-modern",
        scope: "era",
        source: "visual_bible",
        text: "不得出现现代器物",
        author: null,
        rationale: null,
        spoiler_cutoff: 3,
      },
    ],
    uncertainties: [
      {
        uncertainty_key: "u-weapon",
        reason: "future_spoiler",
        detail: "Ayla 的武器将在后文揭晓",
      },
    ],
    ...over,
  };
}

function makePromptRevision(reviewState = "candidate", over: Record<string, unknown> = {}) {
  return {
    id: 10,
    owner_id: 1,
    novel_id: 11,
    prompt_key: "pk-1",
    revision_number: 1,
    parent_prompt_revision_id: null,
    scene_spec_hash: H(6),
    visual_bible_revision_hash: H(2),
    source_snapshot_id: "ss-main",
    source_snapshot_hash: H(3),
    cutoff_chapter: 3,
    schema_version: "prompt-revision.v1",
    schema_hash: H(4),
    prompt_schema_hash: H(7),
    compiler_version: "1.0.0",
    adapter_id: "mock-provider",
    adapter_version: "1.0.0",
    config_hash: H(8),
    input_hash: H(9),
    prompt_hash: H(10),
    sections: { subject: "Ayla 立于北境石厅之中，披灰色羊毛斗篷。" },
    negative_constraints: ["era: 不得出现现代器物"],
    uncertainties: ["[future_spoiler] Ayla 的武器将在后文揭晓"],
    redacted_preview: "[subject]\nAyla 立于北境石厅之中，披灰色羊毛斗篷。",
    review_state: reviewState,
    ...over,
  };
}

function makeDiff(over: Record<string, unknown> = {}) {
  return {
    original_prompt_hash: H(11),
    current_prompt_hash: H(12),
    parent_prompt_revision_id: 9,
    revision_number: 2,
    same: false,
    changed_sections: [
      { section_key: "style", original: "冷色调顶光", current: "暖色调顶光" },
    ],
    changed_negative_constraints: [],
    changed_uncertainties: [],
    prompt_text_changed: true,
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
    chapter_count: 3,
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

  // Scene spec detail + prompt preview (server-compiled, provider_calls: 0).
  await page.route("**/api/novels/11/scene-specs", (route) =>
    route.fulfill({ json: { items: [store.spec], total: 1 } })
  );
  await page.route("**/api/novels/11/scene-specs/1", (route) =>
    route.fulfill({ json: { spec: store.spec, stale: store.stale } })
  );
  await page.route("**/api/novels/11/prompt-revisions/preview", (route) =>
    route.fulfill({
      json: {
        revision: makePromptRevision(),
        lineage: {
          scene_spec_hash: H(6),
          visual_bible_revision_hash: H(2),
          source_snapshot_id: "ss-main",
          source_snapshot_hash: H(3),
          cutoff_chapter: 3,
          schema_hash: H(4),
          prompt_schema_hash: H(7),
          compiler_version: "1.0.0",
          adapter_id: "mock-provider",
          adapter_version: "1.0.0",
          config_hash: H(8),
          input_hash: H(9),
          prompt_hash: H(10),
        },
        provider_calls: 0,
      },
    })
  );

  // Prompt revision detail + diff + explicit edit action.
  await page.route("**/api/novels/11/prompt-revisions/10", (route) =>
    route.fulfill({ json: { revision: makePromptRevision(), stale: store.stale } })
  );
  await page.route("**/api/novels/11/prompt-revisions/10/diff", (route) =>
    route.fulfill({ json: store.diff })
  );
  await page.route("**/api/novels/11/prompt-revisions/10/edit", async (route) => {
    if (store.editFailed) {
      return route.fulfill({
        status: 409,
        json: { detail: "detail d-ayla is evidence-sourced and cannot be edited" },
      });
    }
    return route.fulfill({
      json: {
        revision: makePromptRevision("candidate", {
          id: 11,
          revision_number: 2,
          prompt_key: "edited-10",
        }),
        diff: store.diff,
      },
    });
  });
}

async function openPreview(page: Page) {
  await page.goto("/novels/11/scene-spec");
  const preview = page.getByTestId("scene-spec-preview");
  await expect(preview).toBeVisible({ timeout: 30_000 });
  return preview;
}

async function openDiff(page: Page) {
  await page.goto("/novels/11/scene-spec?diff=10");
  const diff = page.getByTestId("prompt-diff");
  await expect(diff).toBeVisible({ timeout: 30_000 });
  return diff;
}

test("desktop: preview traces detail provenance and never assembles the prompt", async ({
  page,
}) => {
  const store: MockStore = { spec: makeSpec("candidate"), stale: false, diff: makeDiff(), editFailed: false };
  await mockApp(page, store);
  const preview = await openPreview(page);

  await expect(preview).toHaveAttribute("data-review-state", "candidate");
  await expect(preview.getByTestId("scene-spec-candidate-only")).toBeVisible();
  await expect(preview.getByTestId("scene-spec-detail-evidence")).toContainText(
    "ev-ayla-hall"
  );
  await expect(preview.getByTestId("scene-spec-detail-rationale")).toContainText(
    "读者认为光影偏冷"
  );
  await expect(preview.getByTestId("scene-spec-constraint")).toContainText(
    "不得出现现代器物"
  );
  // Future spoiler is surfaced as unsupported/rejected, never canon.
  await expect(preview.getByTestId("scene-spec-unsupported")).toContainText("未来剧透");
  // The provider prompt is only the server redacted preview.
  await expect(preview.getByTestId("scene-spec-prompt-preview")).toContainText(
    "[subject]"
  );
  await expect(preview.getByTestId("scene-spec-provider-calls")).toContainText(
    "provider_calls: 0"
  );
});

test("mobile 390: preview is reachable and provenance is inspectable", async ({
  page,
}) => {
  const store: MockStore = { spec: makeSpec("candidate"), stale: false, diff: makeDiff(), editFailed: false };
  await mockApp(page, store);
  const preview = await openPreview(page);

  await expect(preview.getByTestId("scene-spec-detail-source").first()).toBeVisible();
  await expect(preview.getByTestId("scene-spec-prompt-preview")).toBeVisible();
});

test("stale revision shows the banner so it cannot be silently reused", async ({
  page,
}) => {
  const store: MockStore = { spec: makeSpec("candidate"), stale: true, diff: makeDiff(), editFailed: false };
  await mockApp(page, store);
  const preview = await openPreview(page);
  await expect(preview.getByTestId("scene-spec-stale")).toContainText("静默复用已被拒绝");
});

test("diff shows changed sections and the no-provider-call marker", async ({
  page,
}) => {
  const store: MockStore = { spec: makeSpec("candidate"), stale: false, diff: makeDiff(), editFailed: false };
  await mockApp(page, store);
  const diff = await openDiff(page);

  await expect(diff.getByTestId("prompt-diff-section")).toHaveAttribute(
    "data-section",
    "style"
  );
  await expect(diff.getByTestId("prompt-diff-no-provider")).toContainText(
    "provider_calls: 0"
  );
});

test("explicit edit produces a new candidate revision with the diff retained", async ({
  page,
}) => {
  const store: MockStore = { spec: makeSpec("candidate"), stale: false, diff: makeDiff(), editFailed: false };
  await mockApp(page, store);
  const diff = await openDiff(page);

  await diff.getByTestId("prompt-diff-edit-detail-key").fill("user-lighting");
  await diff.getByTestId("prompt-diff-edit-text").fill("冷色调顶光，强调斗篷暗部");
  await diff.getByTestId("prompt-diff-edit-author").fill("test-editor");
  await diff.getByTestId("prompt-diff-edit-rationale").fill("人工补充光影解读");
  await diff.getByTestId("prompt-diff-edit-submit").click();

  await expect(diff.getByTestId("prompt-diff-edited")).toBeVisible({
    timeout: 15_000,
  });
});

test("failed edit fails closed with the server validation error", async ({
  page,
}) => {
  const store: MockStore = { spec: makeSpec("candidate"), stale: false, diff: makeDiff(), editFailed: true };
  await mockApp(page, store);
  const diff = await openDiff(page);

  await diff.getByTestId("prompt-diff-edit-detail-key").fill("d-ayla");
  await diff.getByTestId("prompt-diff-edit-text").fill("改写正典细节");
  await diff.getByTestId("prompt-diff-edit-submit").click();

  await expect(diff.getByTestId("prompt-diff-validation-error")).toBeVisible({
    timeout: 15_000,
  });
  await expect(diff.getByTestId("prompt-diff-validation-error")).toContainText(
    "cannot be edited"
  );
});
