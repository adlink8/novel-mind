/**
 * Phase 36-04 — Derivative editor browser UAT (REQ-FORK-02, REQ-CRE-03/04,
 * D-36-01..D-36-04).
 *
 * Proves the browser-visible editor contract against a route-mocked backend:
 *   - create a project with an explicit Canon Fork, add a chapter, type
 *     Markdown, wait for the CAS autosave and see the saved state;
 *   - refresh the page and confirm the server draft is restored (no client-only
 *     localStorage history — the source of truth is the immutable revision
 *     lineage);
 *   - a stale 409 conflict is surfaced with the head revision and a reload path
 *     instead of overwriting newer content;
 *   - the revision history renders the deterministic diff and rollback requires
 *     an explicit two-step approval, reporting only the server result;
 *   - cross-owner/fork errors do not leak content (identical empty/404 state);
 *   - the writing page never offers an Original Canon / User Interpretation
 *     write or a publish entry point (D-36-03).
 *
 * Routes are mocked (no real backend); the spec runs under the configured
 * browser matrix (chromium-desktop + chromium-mobile-390). NOTE: on this
 * machine the Next 16 canary dev server fails to compile (pre-existing), so
 * this spec is kept structurally valid and executed by the verification
 * sub-agent when the environment allows.
 */
import { createHash } from "crypto";
import { expect, test, type Page } from "@playwright/test";

const H = (n: number) => String(n).repeat(64);
const sha = (s: string) => createHash("sha256").update(s, "utf8").digest("hex");

const NOVEL_ID = 11;
const PROJECT_ID = 5;
const CHAPTER_ID = 10;

const OWNER = { id: 1, username: "owner", email: "owner@example.com", is_active: true };
const OTHER_OWNER = {
  id: 2,
  username: "other",
  email: "other@example.com",
  is_active: true,
};

interface EditorState {
  revision: number;
  markdown: string;
  title: string;
  patchStatus: "ok" | "conflict";
}

function chapterFor(state: EditorState) {
  return {
    id: CHAPTER_ID,
    project_id: PROJECT_ID,
    owner_id: OWNER.id,
    novel_id: NOVEL_ID,
    position: 0,
    title: state.title,
    markdown: state.markdown,
    markdown_checksum: sha(state.markdown),
    status: "draft",
    revision: state.revision,
    created_at: "2026-08-04T00:00:00Z",
    updated_at: "2026-08-04T00:00:00Z",
  };
}

function revisionRows(state: EditorState, extraKinds: string[] = []) {
  // Newest first; the head row matches the chapter's CAS token.
  const rows = [];
  for (let n = state.revision; n >= 1; n -= 1) {
    const kind =
      n === state.revision && extraKinds.length
        ? extraKinds.shift()!
        : n === 1
          ? "create"
          : "autosave";
    rows.push({
      id: 1000 + n,
      chapter_id: CHAPTER_ID,
      project_id: PROJECT_ID,
      revision_number: n,
      parent_revision_id: n === 1 ? null : 1000 + n - 1,
      kind,
      content_checksum: H(n % 7 + 1),
      actor_id: OWNER.id,
      reason: kind === "rollback" ? "restore earlier draft" : null,
      approval_state: kind === "rollback" ? "approved" : "not_required",
      created_at: `2026-08-04T00:00:0${n}Z`,
    });
  }
  return rows;
}

/**
 * Default editor backend. `state` is mutable so each test can advance the
 * server-side draft (revision/content) like a real backend would.
 */
async function mockApp(page: Page, state: EditorState) {
  await page.route("**/api/**", (route) =>
    route.fulfill({ status: 500, json: { detail: "unmocked e2e endpoint" } })
  );
  await page.route("**/api/auth/me", (route) =>
    route.fulfill({ json: OWNER })
  );
  const novel = {
    id: NOVEL_ID,
    title: "雾城夜读",
    author: "佚名",
    description: null,
    genre: null,
    word_count: 100,
    chapter_count: 3,
    status: "ready",
    reading_progress: { chapter_id: 1, progress_percent: 0 },
    created_at: "",
    updated_at: "",
  };
  const project = {
    id: PROJECT_ID,
    owner_id: OWNER.id,
    novel_id: NOVEL_ID,
    fork_id: 3,
    project_key: "my-story",
    name: "我的分支",
    description: null,
    status: "active",
    space: "fanfiction_canon",
    fork_key: "ff-branch",
    source_version_key: "original:1",
    source_snapshot_hash: H(1),
    through_chapter: 3,
    full_book_authorized: false,
    cutoff_snapshot_hash: H(2),
    scope_hash: H(3),
    manifest_hash: H(4),
    created_at: "2026-08-04T00:00:00Z",
    updated_at: "2026-08-04T00:00:00Z",
  };
  const fork = {
    id: 3,
    fork_key: "ff-branch",
    space: "fanfiction_canon",
    status: "sealed",
    source_version_key: "original:1",
    through_chapter: 3,
    cutoff_snapshot_hash: H(2),
    scope_hash: H(3),
    manifest_hash: H(4),
  };

  await page.route(`**/api/novels`, (route) =>
    route.fulfill({ json: { items: [novel], total: 1 } })
  );
  await page.route(`**/api/novels/${NOVEL_ID}/canon-fork`, (route) =>
    route.fulfill({ json: { novel_id: NOVEL_ID, forks: [fork] } })
  );
  await page.route(`**/api/novels/${NOVEL_ID}/derivative-projects`, (route) => {
    if (route.request().method() === "POST") {
      route.fulfill({ json: { project, message: "project created" } });
      return;
    }
    route.fulfill({ json: { novel_id: NOVEL_ID, total: 1, items: [project] } });
  });
  await page.route(
    `**/api/novels/${NOVEL_ID}/derivative-projects/${PROJECT_ID}/chapters`,
    (route) => {
      if (route.request().method() === "POST") {
        route.fulfill({
          json: {
            chapter: chapterFor({ ...state, title: "第一章" }),
            scope: { project_id: PROJECT_ID, space: "fanfiction_canon" },
          },
        });
        return;
      }
      route.fulfill({
        json: {
          project_id: PROJECT_ID,
          scope: { project_id: PROJECT_ID, space: "fanfiction_canon" },
          total: 1,
          items: [chapterFor(state)],
        },
      });
    }
  );
  await page.route(
    `**/api/novels/${NOVEL_ID}/derivative-projects/${PROJECT_ID}/chapters/${CHAPTER_ID}`,
    (route) => {
      if (route.request().method() === "PATCH") {
        const body = route.request().postDataJSON();
        if (state.patchStatus === "conflict" || body?.base_revision !== state.revision) {
          route.fulfill({
            status: 409,
            json: {
              detail: `revision_conflict: stale write: chapter ${CHAPTER_ID} is at revision ${state.revision}`,
            },
          });
          return;
        }
        state.markdown = body?.markdown ?? state.markdown;
        state.title = body?.title ?? state.title;
        state.revision += 1;
        route.fulfill({ json: chapterFor(state) });
        return;
      }
      route.fulfill({ json: chapterFor(state) });
    }
  );

  // ---- Revision surface (Phase 36-03) ----
  await page.route(
    `**/api/novels/${NOVEL_ID}/derivative-projects/${PROJECT_ID}/chapters/${CHAPTER_ID}/revisions`,
    (route) => {
      if (route.request().method() === "GET") {
        const rows = revisionRows(state);
        route.fulfill({
          json: { chapter_id: CHAPTER_ID, project_id: PROJECT_ID, total: rows.length, items: rows },
        });
        return;
      }
      // autosave: advance the draft like a real CAS autosave.
      const body = route.request().postDataJSON();
      if (body?.base_revision !== state.revision) {
        route.fulfill({
          status: 409,
          json: {
            detail: {
              code: "revision_conflict",
              message: "stale write",
              current_revision_number: state.revision,
              current_checksum: sha(state.markdown),
            },
          },
        });
        return;
      }
      state.markdown = body?.content ?? state.markdown;
      state.revision += 1;
      route.fulfill({
        json: {
          status: "saved",
          chapter: chapterFor(state),
          revision: revisionRows(state)[0],
          message: "autosave acknowledged (Fanfiction Canon draft)",
        },
      });
    }
  );
  await page.route(
    `**/api/novels/${NOVEL_ID}/derivative-projects/${PROJECT_ID}/chapters/${CHAPTER_ID}/revisions/*`,
    (route) =>
      route.fulfill({
        json: { ...revisionRows(state)[0], content: state.markdown, updated_at: "" },
      })
  );
  await page.route(
    `**/api/novels/${NOVEL_ID}/derivative-projects/${PROJECT_ID}/chapters/${CHAPTER_ID}/diff**`,
    (route) =>
      route.fulfill({
        json: {
          base_revision_id: 1000 + state.revision - 1,
          base_revision_number: state.revision - 1,
          target_revision_id: 1000 + state.revision,
          target_revision_number: state.revision,
          additions: 1,
          deletions: 1,
          hunks: [
            {
              old_start: 1,
              old_count: 1,
              new_start: 1,
              new_count: 1,
              lines: [
                { op: "delete", text: "旧内容" },
                { op: "add", text: "新内容" },
              ],
            },
          ],
        },
      })
  );
  await page.route(
    `**/api/novels/${NOVEL_ID}/derivative-projects/${PROJECT_ID}/chapters/${CHAPTER_ID}/rollback`,
    (route) => {
      if (route.request().method() === "POST") {
        const body = route.request().postDataJSON();
        if (body?.base_revision !== state.revision) {
          route.fulfill({
            status: 409,
            json: {
              detail: {
                code: "revision_conflict",
                message: "stale write",
                current_revision_number: state.revision,
              },
            },
          });
          return;
        }
        state.revision += 1;
        route.fulfill({
          json: {
            chapter: chapterFor(state),
            revision: {
              ...revisionRows(state)[0],
              kind: "rollback",
              approval_state: "approved",
              reason: body?.reason ?? null,
            },
            target_revision_id: body?.target_revision_id,
            message: "rollback restored the target as a new immutable child revision",
          },
        });
        return;
      }
      route.fulfill({ status: 405, json: { detail: "method not allowed" } });
    }
  );

  // ---- Quiet the writing-page side panels (visual review + export) ----
  // These panels mount on /writing; leaving their endpoints unmocked falls
  // into the 500 catch-all and raises error alerts that pollute assertions
  // like getByRole('alert').
  await page.route(`**/api/novels/${NOVEL_ID}/derivative-visual/review`, (route) =>
    route.fulfill({ json: { items: [], total: 0 } })
  );
  await page.route(`**/api/novels/${NOVEL_ID}/derivative-visual/review/*`, (route) =>
    route.fulfill({ status: 404, json: { detail: "no candidate" } })
  );
  await page.route("**/api/agent/approval-requests**", (route) =>
    route.fulfill({ json: { items: [], total: 0, skip: 0, limit: 100 } })
  );
  await page.route(
    `**/api/agent/novels/${NOVEL_ID}/artifacts/**`,
    (route) => route.fulfill({ status: 404, json: { detail: "no artifact" } })
  );
  await page.route(
    `**/api/novels/${NOVEL_ID}/derivative-projects/${PROJECT_ID}/export/audit**`,
    (route) =>
      route.fulfill({ json: { overall: "blocked", dimensions: [] } })
  );
}

function freshState(): EditorState {
  return {
    revision: 1,
    markdown: "## 开场\n服务器已保存的正文",
    title: "第一章",
    patchStatus: "ok",
  };
}

async function gotoWriting(page: Page) {
  await page.goto("/writing");
  await page.waitForLoadState("domcontentloaded");
  // The authenticated shell must be present (desktop rail or mobile nav).
  await expect(
    page.locator('[data-testid="app-shell-nav"], [data-testid="app-shell-nav-mobile"]').first()
  ).toBeVisible({ timeout: 20_000 });
}

async function openHistory(page: Page) {
  await page.getByTestId("revision-history-toggle").click();
  await expect(page.getByTestId("revision-history-list")).toBeVisible({
    timeout: 15_000,
  });
}

test("create: explicit fork project + chapter edit + CAS autosave state", async ({
  page,
}) => {
  const state = freshState();
  await mockApp(page, state);
  await gotoWriting(page);

  // No project exists yet for a brand-new owner? Default backend returns one —
  // create a second one through the explicit fork picker (D-36-01).
  await page.getByLabel("新项目名称").fill("第二个分支");
  await page.getByLabel(/Canon Fork/).selectOption("3"); // fork id 3 = ff-branch
  await page.getByRole("button", { name: "创建项目" }).click();
  await expect(page.getByText(/项目已创建并绑定到所选 fork/)).toBeVisible({
    timeout: 15_000,
  });

  // Type Markdown and let the debounced CAS autosave fire.
  const textbox = page.getByLabel("章节 Markdown 内容");
  await textbox.fill("## 开场\n读者可见的新草稿");
  await expect(page.getByTestId("editor-save-state")).toContainText("dirty");

  // The save button forces a synchronous save; success state comes from the server.
  await page.getByRole("button", { name: /^保存$/ }).click();
  await expect(page.getByTestId("editor-save-state")).toContainText("saved", {
    timeout: 15_000,
  });
  expect(state.revision).toBeGreaterThan(1);

  // The surface is sealed to Fanfiction Canon: no Original/Interpretation write.
  await expect(page.getByText("fanfiction_canon").first()).toBeVisible();
  await expect(page.getByText("original_canon")).toHaveCount(0);
  await expect(page.getByText("user_interpretation")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /发布/i })).toHaveCount(0);
});

test("refresh: the server draft is restored after a page reload", async ({
  page,
}) => {
  const state = freshState();
  await mockApp(page, state);
  await gotoWriting(page);

  const textbox = page.getByLabel("章节 Markdown 内容");
  await textbox.fill("## 开场\n刷新后仍要保留的正文");
  await page.getByRole("button", { name: /^保存$/ }).click();
  await expect(page.getByTestId("editor-save-state")).toContainText("saved", {
    timeout: 15_000,
  });

  // Full reload: the draft must come back from the server (no localStorage).
  await page.reload();
  await expect(page.getByTestId("revision-history-summary")).toBeVisible({
    timeout: 20_000,
  });
  await expect(textbox).toHaveValue("## 开场\n刷新后仍要保留的正文");
});

test("conflict: a stale 409 is surfaced with a reload path, not overwritten", async ({
  page,
}) => {
  const state = freshState();
  state.patchStatus = "conflict"; // every save reports the head has advanced
  await mockApp(page, state);
  await gotoWriting(page);

  const textbox = page.getByLabel("章节 Markdown 内容");
  await textbox.fill("旧标签试图覆盖新内容");
  await page.getByRole("button", { name: /^保存$/ }).click();

  // Conflict state is explicit and carries the head revision.
  await expect(page.getByTestId("editor-save-state")).toContainText("conflict", {
    timeout: 15_000,
  });
  await expect(page.getByText(/检测到更新版本.*revision 1/)).toBeVisible({
    timeout: 15_000,
  });

  // Reloading fetches the server snapshot and clears the conflict.
  // Scope to the editor: the writing page also renders review/export panels
  // that each carry a persistent "重新加载" header button.
  await page
    .getByLabel(/Markdown 编辑器/)
    .getByRole("button", { name: "重新加载" })
    .click();
  await expect(page.getByTestId("editor-save-state")).toContainText("idle", {
    timeout: 15_000,
  });
  await expect(textbox).toHaveValue("## 开场\n服务器已保存的正文");
});

test("history: diff is rendered and rollback needs explicit approval", async ({
  page,
}) => {
  const state = freshState();
  state.revision = 3;
  state.markdown = "## 开场\n第三版内容";
  await mockApp(page, state);
  await gotoWriting(page);

  await openHistory(page);

  // Newest-first immutable history.
  const rows = page.getByTestId("revision-row");
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0)).toContainText("v3");

  // Deterministic diff renders add/delete lines.
  await expect(page.getByTestId("revision-diff")).toContainText("+1 / −1");
  await expect(page.getByTestId("revision-diff")).toContainText("新内容");
  await expect(page.getByTestId("revision-diff")).toContainText("旧内容");

  // Rollback: first click only opens the approval panel; no request yet.
  await rows.nth(2).getByTestId("revision-rollback").click();
  await expect(page.getByTestId("rollback-confirm")).toBeVisible();
  await expect(page.getByText("回滚到 v1？")).toBeVisible();

  await page.getByLabel("回滚原因").fill("恢复初稿");
  await page.getByTestId("rollback-confirm-button").click();

  // Success is reported from the actual server response.
  await expect(page.getByTestId("rollback-result")).toBeVisible({ timeout: 15_000 });
  await expect(page.getByTestId("rollback-result")).toContainText("v4");
  expect(state.revision).toBe(4);
});

test("isolation: a cross-owner project never leaks content", async ({ page }) => {
  const state = freshState();
  await mockApp(page, state);

  // The "other owner" sees an empty project shelf for their own novel.
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: OTHER_OWNER }));
  await page.route(`**/api/novels`, (route) =>
    route.fulfill({
      json: {
        items: [
          {
            id: NOVEL_ID + 100,
            title: "别人的小说",
            author: null,
            description: null,
            genre: null,
            word_count: 50,
            chapter_count: 1,
            status: "ready",
            created_at: "",
            updated_at: "",
          },
        ],
        total: 1,
      },
    })
  );
  await page.route(`**/api/novels/${NOVEL_ID + 100}/derivative-projects`, (route) =>
    route.fulfill({ json: { novel_id: NOVEL_ID + 100, total: 0, items: [] } })
  );
  await page.route(`**/api/novels/${NOVEL_ID + 100}/canon-fork`, (route) =>
    route.fulfill({ json: { novel_id: NOVEL_ID + 100, forks: [] } })
  );

  await gotoWriting(page);
  await expect(page.getByText(/还没有项目/)).toBeVisible({ timeout: 20_000 });
  // Owner A's project name and markdown never reach the DOM.
  await expect(page.getByText("我的分支")).toHaveCount(0);
  await expect(page.getByText(/服务器已保存的正文/)).toHaveCount(0);
});
