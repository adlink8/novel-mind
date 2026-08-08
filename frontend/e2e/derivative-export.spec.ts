/**
 * Phase 39-03 — derivative export browser UAT (D-39-03, T-39-03-01/02,
 * REQ-FORK-05 / REQ-CRE-07).
 *
 * Proves the browser-visible export contract against a route-mocked backend:
 *   - the export panel only requests export through the deterministic
 *     `agent/materialize` route of an **approved** ExportPreparationArtifact —
 *     the browser never assembles a manifest or selects a live revision;
 *   - approved artifact metadata (preparation_id / revision / export version /
 *     manifest checksum / approved asset+ citation counts) and the
 *     three-dimension audit (quality stays blocked while Phase 22 is 0/3) are
 *     rendered from the server envelope;
 *   - after download the manifest header is verified against the approved
 *     artifact's frozen checksum; a completed download is never shown as a
 *     quality pass, and EPUB interoperability is explicitly **unverified**
 *     (no EPUB validator → not green);
 *   - refresh/reopen restores the approved artifact + audit from the server;
 *   - chapter order / content / asset hash / citation comparison on the
 *     downloaded bytes;
 *   - cross-owner (no approved artifact for the other owner) and Original
 *     (no derivative project / no Original export entry point) attempts fail
 *     closed without leaking content;
 *   - pending / rejected / stale artifacts, a forged preparation hash and a
 *     missing asset surface as comprehensible errors with a retry entry.
 *
 * Routes are mocked (no real backend); the browser `fetch` stack is
 * intercepted by the same `page.route` handlers the UI would hit. NOTE: on this
 * machine the Next 16 canary dev server fails to compile (pre-existing), so
 * this spec is kept structurally valid and executed by the verification
 * sub-agent when the environment allows.
 */
import { createHash } from "crypto";
import { expect, test, type Page, type Route } from "@playwright/test";

const H = (n: number) => String(n).repeat(64);
const sha = (s: string) => createHash("sha256").update(s, "utf8").digest("hex");

const NOVEL_ID = 31;
const PROJECT_ID = 8;
const FORK_ID = 3;
const ARTIFACT_ID = 15;
const REVISION_ID = 150;
const APPROVAL_ID = 900;

const OWNER = { id: 1, username: "owner", email: "owner@example.com", is_active: true };
const OTHER_OWNER = {
  id: 2,
  username: "other",
  email: "other@example.com",
  is_active: true,
};

const MANIFEST_CHECKSUM = H(1);
const SNAPSHOT_HASH = H(2);
const PREPARATION_HASH = H(3);
const PACKAGE_HASH = H(4);
const ASSET_HASH = sha("png-bytes");
const CITATION_KEY = `fork:ff-uat:chapter:1`;

interface ExportState {
  approvalStatus: "approved" | "pending" | "rejected";
  artifactStatus: "approved" | "candidate" | "rejected";
  /** prepare.manifest_hash != approved checksum -> stale blocked. */
  stale: boolean;
  /** materialize endpoint outcome. */
  materialize: "ok" | "hash_mismatch" | "missing_asset" | "rejected";
  /** download header; tampered -> verify fails closed. */
  headerHash: string;
  /** whether the approvals list is visible to the signed-in owner. */
  approvalsVisible: boolean;
}

function freshState(): ExportState {
  return {
    approvalStatus: "approved",
    artifactStatus: "approved",
    stale: false,
    materialize: "ok",
    headerHash: MANIFEST_CHECKSUM,
    approvalsVisible: true,
  };
}

const json = (route: Route, body: unknown, opts?: { status?: number }) =>
  route.fulfill({
    contentType: "application/json",
    status: opts?.status ?? 200,
    json: body,
  });

const approvalFor = (state: ExportState) => ({
  id: APPROVAL_ID,
  owner_id: OWNER.id,
  action: "approve_export",
  status: state.approvalStatus,
  payload_summary: {
    project_id: PROJECT_ID,
    project_key: "proj-uat",
    fork_id: FORK_ID,
    fork: "ff-uat",
    branch: "deriv-branch",
    artifact_id: ARTIFACT_ID,
    artifact_revision_id: REVISION_ID,
    snapshot_hash: SNAPSHOT_HASH,
    manifest_hash: MANIFEST_CHECKSUM,
    approval_note: null,
  },
  created_at: "2026-08-04T00:00:00Z",
  decided_at: "2026-08-04T00:00:01Z",
  expires_at: null,
});

const artifactFor = (state: ExportState) => ({
  id: ARTIFACT_ID,
  owner_id: OWNER.id,
  novel_id: NOVEL_ID,
  type: "export_preparation",
  schema_version: "export-preparation.v1",
  status: state.artifactStatus,
  branch: "deriv-branch",
  input_hash: H(5),
  current_revision_id: REVISION_ID,
});

const revisionContent = {
  type: "export_preparation",
  preparation: {
    schema_version: "export-preparation.v1",
    artifact_kind: "export_preparation",
    authority_space: "derivative",
    fork: "ff-uat",
    project_id: PROJECT_ID,
    project_key: "proj-uat",
    source_snapshot: {
      source_snapshot_id: `novel:${NOVEL_ID}:ff-uat`,
      source_snapshot_hash: H(6),
      source_manifest_hash: H(7),
      cutoff_chapter: 2,
    },
    base_revision: {
      project_manifest_hash: H(7),
      scope_hash: H(8),
      cutoff_snapshot_hash: H(9),
      text_version_hash: H(10),
    },
    content_hash: MANIFEST_CHECKSUM,
    evidence_refs: [CITATION_KEY],
    generator_lineage: {},
    validator_report: { verdict: "candidate", reasons: ["deterministic_preparation_ok"] },
    review_state: "candidate",
    approval_request_id: null,
    materialize_lineage: {},
  },
};

const auditReport = {
  schema_version: "derivative-export-audit.v1",
  audit_version: "derivative-export-audit.v1",
  owner_id: OWNER.id,
  novel_id: NOVEL_ID,
  project_id: PROJECT_ID,
  snapshot_hash: SNAPSHOT_HASH,
  dimensions: [
    { dimension: "implementation_readiness", status: "verified", blocked_reasons: [], evidence: [] },
    { dimension: "sample_data_coverage", status: "verified", blocked_reasons: [], evidence: [] },
    { dimension: "quality_qualification", status: "blocked", blocked_reasons: [H(11)], evidence: [] },
  ],
  // Phase 39-04: the independent lineage audit (source snapshot -> preparation
  // artifact -> approve_export approval -> materialized bundle -> download) and
  // the honest REQ-SHIP-01 production baseline ride on the same report. The
  // verdict stays blocked while any lineage check, shipment item or Phase 22
  // evidence is non-verified — never promotion.
  lineage: {
    schema_version: "derivative-export-lineage-audit.v1",
    checks: [
      { kind: "source_snapshot", status: "verified", raw_evidence_link: "backend/app/services/derivative_export/snapshot.py", detail: "snapshot hash replays" },
      { kind: "manifest", status: "verified", raw_evidence_link: "backend/app/services/derivative_export/manifest.py", detail: "manifest replays" },
      { kind: "parity", status: "verified", raw_evidence_link: "backend/app/services/derivative_export/package.py", detail: "parity clean" },
      { kind: "preparation_hash", status: "verified", raw_evidence_link: "backend/app/services/derivative_export/preparation.py", detail: "preparation hash replays" },
      { kind: "preparation_payload", status: "verified", raw_evidence_link: "backend/app/services/derivative_export/preparation.py", detail: "payload replays" },
      { kind: "artifact_binding", status: "verified", raw_evidence_link: "backend/app/models/agent_runtime.py:Artifact", detail: "approved artifact bound" },
      { kind: "approval_binding", status: "verified", raw_evidence_link: "backend/app/models/agent_runtime.py:ApprovalRequest", detail: "approved approval bound" },
      { kind: "materialization", status: "verified", raw_evidence_link: "backend/app/services/derivative_export/package.py", detail: "bundle replays" },
      { kind: "download_audit", status: "verified", raw_evidence_link: "backend/app/api/derivative_export.py", detail: "download replays" },
      { kind: "epub_validation", status: "blocked", raw_evidence_link: "backend/app/services/derivative_export/epub.py", detail: "EPUB 互操作性未验证", blocked_reasons: ["epub_interoperability_unverified"] },
    ],
  },
  shipment: {
    schema_version: "derivative-export-shipment-baseline.v1",
    items: [
      { requirement: "tls", status: "blocked", raw_evidence_link: "docs/DEPLOYMENT.md#Production-Blockers", detail: "no TLS ingress evidence" },
      { requirement: "secret_sourcing_rotation", status: "unverified", raw_evidence_link: "docs/DEPLOYMENT.md", detail: "provider key rotation only" },
      { requirement: "backup_restore_drill", status: "blocked", raw_evidence_link: "docs/DEPLOYMENT.md#Production-Blockers", detail: "no backup/restore drill evidence" },
      { requirement: "monitoring_alert", status: "blocked", raw_evidence_link: "docs/DEPLOYMENT.md#Production-Blockers", detail: "no monitoring/alert evidence" },
      { requirement: "cost_budget", status: "unverified", raw_evidence_link: "backend/app/models/agent_runtime.py:SkillRun", detail: "per-run budgets only" },
    ],
  },
  verdict: "blocked",
  blocked_reasons: [H(11), "lineage_blocked:epub_validation", "shipment_blocked:tls"],
  report_hash: H(12),
  phase22: {
    green_observed: 0,
    green_required: 3,
    source: ".planning/STATE.md",
    source_hash: H(13),
  },
};

const markdownBytes = [
  "# Deriv Project uat",
  "",
  "<!-- NovelMind derivative export manifest",
  `${MANIFEST_CHECKSUM}; owner_id=${OWNER.id}; novel_id=${NOVEL_ID};`,
  `project_id=${PROJECT_ID}; fork_id=${FORK_ID}; text_version_hash=${H(10)} -->`,
  "",
  "## Chapter 1",
  "",
  "阿宁在竹林入口站定。",
  "",
  `<figure class="derivative-export-asset"><img src="assets/${ASSET_HASH}.png" alt="dv-uat-1"/><figcaption>asset_id=dv-uat-1 chapter=1 content_hash=${ASSET_HASH}</figcaption></figure>`,
  "",
  "## Chapter 2",
  "",
  "她推开了那扇竹门。",
  "",
  "## 引用",
  "",
  `- \`${CITATION_KEY}\` citation_hash=${sha(CITATION_KEY)} source_snapshot=${H(6)} revision_id=1 chapter=1`,
  "",
  "## 导出清单",
  "",
  `- export_version: 1.0.0`,
  `- manifest_hash: ${MANIFEST_CHECKSUM}`,
  `- revisions: 2`,
  `- assets: 1`,
  `- citations: 1`,
  "- 无缺失资产",
  "",
].join("\n");

/**
 * Mock backend for the writing page + export panel. `state` is mutable so each
 * test can drive a fail-closed outcome like the deterministic server.
 */
async function mockApp(page: Page, state: ExportState) {
  await page.route("**/api/**", (route) =>
    json(route, { detail: "unmocked e2e endpoint" }, { status: 500 })
  );
  await page.route("**/api/auth/me", (route) => json(route, OWNER));

  const novel = {
    id: NOVEL_ID,
    title: "雾城夜读",
    author: null,
    description: null,
    genre: null,
    word_count: 100,
    chapter_count: 2,
    status: "ready",
    reading_progress: { chapter_id: 1, progress_percent: 0 },
    created_at: "",
    updated_at: "",
  };
  const project = {
    id: PROJECT_ID,
    owner_id: OWNER.id,
    novel_id: NOVEL_ID,
    fork_id: FORK_ID,
    project_key: "proj-uat",
    name: "Deriv Project uat",
    description: null,
    status: "active",
    space: "fanfiction_canon",
    fork_key: "ff-uat",
    source_version_key: "original:1",
    source_snapshot_hash: H(6),
    through_chapter: 2,
    full_book_authorized: false,
    cutoff_snapshot_hash: H(9),
    scope_hash: H(8),
    manifest_hash: H(7),
    created_at: "2026-08-04T00:00:00Z",
    updated_at: "2026-08-04T00:00:00Z",
  };

  await page.route("**/api/novels", (route) =>
    json(route, { items: [novel], total: 1 })
  );
  await page.route(`**/api/novels/${NOVEL_ID}/canon-fork`, (route) =>
    json(route, {
      novel_id: NOVEL_ID,
      forks: [
        {
          id: FORK_ID,
          fork_key: "ff-uat",
          space: "fanfiction_canon",
          status: "sealed",
          source_version_key: "original:1",
          through_chapter: 2,
          cutoff_snapshot_hash: H(9),
          scope_hash: H(8),
          manifest_hash: H(7),
        },
      ],
    })
  );
  await page.route(`**/api/novels/${NOVEL_ID}/derivative-projects`, (route) =>
    json(route, { novel_id: NOVEL_ID, total: 1, items: [project] })
  );
  await page.route(
    `**/api/novels/${NOVEL_ID}/derivative-projects/${PROJECT_ID}/chapters`,
    (route) => json(route, { project_id: PROJECT_ID, scope: { project_id: PROJECT_ID, space: "fanfiction_canon" }, total: 0, items: [] })
  );
  // The visual review panel also mounts on the writing page; keep it quiet.
  await page.route(`**/api/novels/${NOVEL_ID}/derivative-visual/review`, (route) =>
    json(route, { items: [], total: 0 })
  );

  // ---- Export agent + audit surface (the panel consumes these) ----
  // Patterns end with `**` because the frontend sends query strings
  // (?skip=0&limit=100, ?format=markdown); Playwright globs match the whole
  // URL, so without a trailing ** these requests fall into the 500 catch-all.
  await page.route("**/api/agent/approval-requests**", (route) =>
    json(route, {
      items: state.approvalsVisible
        ? [approvalFor(state)]
        : [],
      total: state.approvalsVisible ? 1 : 0,
      skip: 0,
      limit: 100,
    })
  );
  await page.route(`**/api/agent/novels/${NOVEL_ID}/artifacts/${ARTIFACT_ID}**`, (route) =>
    json(route, artifactFor(state))
  );
  await page.route(
    `**/api/agent/novels/${NOVEL_ID}/artifacts/${ARTIFACT_ID}/revisions**`,
    (route) =>
      json(route, {
        items: [
          { id: REVISION_ID, artifact_id: ARTIFACT_ID, owner_id: OWNER.id, novel_id: NOVEL_ID, revision_no: 1, content: revisionContent },
        ],
        total: 1,
      })
  );
  await page.route(`**/api/novels/${NOVEL_ID}/derivative-projects/${PROJECT_ID}/export/audit`, (route) =>
    json(route, auditReport)
  );
  await page.route(`**/api/novels/${NOVEL_ID}/derivative-projects/${PROJECT_ID}/export/agent/prepare`, (route) =>
    json(route, {
      preparation: {},
      preparation_hash: PREPARATION_HASH,
      snapshot_hash: SNAPSHOT_HASH,
      manifest_hash: state.stale ? H(20) : MANIFEST_CHECKSUM,
      schema_version: "export-preparation.v1",
      export_version: "1.0.0",
      project_id: PROJECT_ID,
      fork_id: FORK_ID,
      chapter_count: 2,
      asset_count: 1,
      revision_count: 2,
      citation_count: 1,
      candidate_only: true,
    })
  );
  await page.route(`**/api/novels/${NOVEL_ID}/derivative-projects/${PROJECT_ID}/export/agent/materialize`, async (route) => {
    const body = route.request().postDataJSON();
    if (state.materialize === "hash_mismatch") {
      await json(
        route,
        { detail: "preparation_hash_mismatch: supplied preparation_hash no longer replays the frozen export lineage (stale preparation)" },
        { status: 400 }
      );
      return;
    }
    if (state.materialize === "missing_asset") {
      await json(
        route,
        { detail: "bundle_blocked: derivative export package blocked: missing_asset_blocks_package" },
        { status: 400 }
      );
      return;
    }
    if (state.materialize === "rejected") {
      await json(
        route,
        { detail: "approval_not_approved: approve_export approval 900 is rejected; only an approved approval can be consumed" },
        { status: 400 }
      );
      return;
    }
    // Approved path: the browser submits the approved artifact's exact refs.
    if (body?.artifact_id !== ARTIFACT_ID || body?.artifact_revision_id !== REVISION_ID) {
      await json(route, { detail: "artifact_not_found: export preparation artifact not found in the owner/novel scope" }, { status: 400 });
      return;
    }
    await json(route, {
      owner_id: OWNER.id,
      novel_id: NOVEL_ID,
      project_id: PROJECT_ID,
      fork_id: FORK_ID,
      artifact_id: ARTIFACT_ID,
      artifact_revision_id: REVISION_ID,
      approval_request_id: APPROVAL_ID,
      approval_action: "approve_export",
      approval_status: "approved",
      preparation_hash: PREPARATION_HASH,
      snapshot_hash: SNAPSHOT_HASH,
      manifest_hash: MANIFEST_CHECKSUM,
      package_hash: PACKAGE_HASH,
      package_schema_version: "derivative-export-package.v1",
      bundle_size: markdownBytes.length,
      bundle_formats: ["package"],
      status: "approved",
      candidate_only: false,
      materialized: true,
    });
  });
  await page.route(`**/api/novels/${NOVEL_ID}/derivative-projects/${PROJECT_ID}/export/download**`, (route) => {
    const format = String(route.request().url()).split("format=")[1] ?? "markdown";
    route.fulfill({
      status: 200,
      contentType: format === "epub" ? "application/epub+zip" : "text/markdown",
      headers: {
        "X-Export-Manifest-Hash": state.headerHash,
        "X-Export-Snapshot-Hash": SNAPSHOT_HASH,
        "X-Export-Format": format,
        "X-Export-Project-Id": String(PROJECT_ID),
      },
      body: markdownBytes,
    });
  });
}

async function gotoWriting(page: Page) {
  await page.goto("/writing");
  await page.waitForLoadState("domcontentloaded");
  await expect(
    page.locator(
      '[data-testid="app-shell-nav"]:visible, [data-testid="app-shell-nav-mobile"]:visible'
    )
  ).toBeVisible({ timeout: 20_000 });
}

async function readyPanel(page: Page) {
  await expect(page.getByTestId("derivative-export-ready")).toBeVisible({
    timeout: 20_000,
  });
}

/** Fetch raw download bytes as text (exercises the served route, not a constant). */
async function fetchText(page: Page, path: string): Promise<string> {
  return page.evaluate(async (url) => {
    const res = await fetch(url);
    return res.text();
  }, path);
}

test.describe("derivative export browser UAT", () => {
  test("approved artifact: export markdown via materialize, verify manifest, quality stays blocked", async ({ page }) => {
    const state = freshState();
    await mockApp(page, state);
    await gotoWriting(page);
    await readyPanel(page);

    // Provenance + counts come from the approved artifact envelope.
    await expect(page.getByTestId("derivative-export-approved-badge")).toContainText("已批准");
    await expect(page.getByTestId("derivative-export-preparation-id")).toContainText(`#${ARTIFACT_ID}`);
    await expect(page.getByTestId("derivative-export-revision")).toContainText(`#${REVISION_ID}`);
    await expect(page.getByTestId("derivative-export-version")).toContainText("v1.0.0");
    await expect(page.getByTestId("derivative-export-manifest-checksum")).toContainText(
      MANIFEST_CHECKSUM.slice(0, 8)
    );
    await expect(page.getByTestId("derivative-export-counts")).toContainText(
      "2 章 · 1 资产 · 1 引用 · 2 修订"
    );
    // Three-dimension audit: quality blocked (Phase 22 0/3), never green.
    await expect(page.getByTestId("derivative-export-audit")).toHaveAttribute("data-verdict", "blocked");
    await expect(page.getByTestId("derivative-export-phase22")).toContainText("0/3");

    // The browser submits the approved artifact's materialize request only.
    await page.getByTestId("derivative-export-button-markdown").click({ force: true });
    await expect(page.getByTestId("derivative-export-done-markdown")).toContainText(
      "manifest 校验通过",
      { timeout: 20_000 }
    );
    await expect(page.getByTestId("derivative-export-done-markdown")).not.toContainText("质量通过");
  });

  test("lineage + REQ-SHIP-01 baseline: verdict stays blocked, never promotion", async ({ page }) => {
    const state = freshState();
    await mockApp(page, state);
    await gotoWriting(page);
    await readyPanel(page);

    // Phase 39-04 release gate: the extended report carries an independent
    // lineage audit and the REQ-SHIP-01 baseline; any blocked check (unverified
    // EPUB, missing TLS/backup/monitoring evidence, Phase 22 0/3) keeps the
    // verdict blocked — the UI never renders a promotion / green state.
    await expect(page.getByTestId("derivative-export-audit")).toHaveAttribute("data-verdict", "blocked");
    await expect(page.getByTestId("derivative-export-verdict")).toContainText("阻断（不可发布）");
    await expect(page.getByTestId("derivative-export-phase22")).toContainText("0/3");
    await expect(page.getByText("阻断（不可发布）")).toHaveCount(1);
    await expect(page.getByText("合格候选")).toHaveCount(0);
  });

  test("epub download reports interoperability explicitly unverified (not green)", async ({ page }) => {
    const state = freshState();
    await mockApp(page, state);
    await gotoWriting(page);
    await readyPanel(page);

    await page.getByTestId("derivative-export-button-epub").click({ force: true });
    await expect(page.getByTestId("derivative-export-done-epub")).toContainText(
      "EPUB 互操作性未验证",
      { timeout: 20_000 }
    );
    await expect(page.getByTestId("derivative-export-done-epub")).toContainText("不标绿");
    await expect(page.getByTestId("derivative-export-done-epub")).not.toContainText("质量通过");
  });

  test("refresh/reopen: the approved artifact + audit restore from the server", async ({ page }) => {
    const state = freshState();
    await mockApp(page, state);
    await gotoWriting(page);
    await readyPanel(page);

    await page.reload();
    await readyPanel(page);
    await expect(page.getByTestId("derivative-export-preparation-id")).toContainText(`#${ARTIFACT_ID}`);
    await expect(page.getByTestId("derivative-export-audit")).toHaveAttribute("data-verdict", "blocked");

    await page.getByTestId("derivative-export-button-markdown").click({ force: true });
    await expect(page.getByTestId("derivative-export-done-markdown")).toContainText(
      "manifest 校验通过",
      { timeout: 20_000 }
    );
  });

  test("chapter order / asset hash / citation comparison on the downloaded bytes", async ({ page }) => {
    const state = freshState();
    await mockApp(page, state);
    await gotoWriting(page);
    await readyPanel(page);

    await page.getByTestId("derivative-export-button-markdown").click({ force: true });
    await expect(page.getByTestId("derivative-export-done-markdown")).toBeVisible({
      timeout: 20_000,
    });

    // Read the served download bytes through the same route the UI would hit and
    // compare chapter order / content / asset hash / citation on the bytes.
    const text = await fetchText(
      page,
      `/api/novels/${NOVEL_ID}/derivative-projects/${PROJECT_ID}/export/download?format=markdown`
    );
    expect(text.indexOf("## Chapter 1")).toBeLessThan(text.indexOf("## Chapter 2"));
    expect(text).toContain("阿宁在竹林入口站定");
    expect(text).toContain(ASSET_HASH);
    expect(text).toContain(CITATION_KEY);
    expect(text).toContain(MANIFEST_CHECKSUM);
  });

  test("cross-owner: the other owner sees no approved artifact and no export entry", async ({ page }) => {
    const state = freshState();
    state.approvalsVisible = false; // other owner's approval surface is empty
    await mockApp(page, state);
    await page.route("**/api/auth/me", (route) => json(route, OTHER_OWNER));
    // The other owner owns no derivative project: the projects list is empty so
    // the export panel never sees a project and the project name never leaks.
    await page.route(`**/api/novels/${NOVEL_ID}/derivative-projects`, (route) =>
      json(route, { novel_id: NOVEL_ID, total: 0, items: [] })
    );

    await gotoWriting(page);
    // No content leak: with no derivative project the export panel is not
    // mounted at all and the project name never appears.
    await expect(page.getByTestId("derivative-export-panel")).toHaveCount(0);
    await expect(page.getByText("Deriv Project uat")).toHaveCount(0);
  });

  test("Original: no derivative project means no Original export entry point", async ({ page }) => {
    await mockApp(page, freshState());
    // Original novels have no derivative projects; the export panel is not mounted.
    await page.route(`**/api/novels/${NOVEL_ID}/derivative-projects`, (route) =>
      json(route, { novel_id: NOVEL_ID, total: 0, items: [] })
    );
    await gotoWriting(page);
    await expect(page.getByText(/还没有项目/)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("derivative-export-panel")).toHaveCount(0);
    // No Original export path is ever offered.
    await expect(page.getByText("original_canon")).toHaveCount(0);
  });

  test("pending approval: export is blocked with a comprehensible state", async ({ page }) => {
    const state = freshState();
    state.approvalStatus = "pending";
    await mockApp(page, state);
    await gotoWriting(page);
    await expect(page.getByTestId("derivative-export-empty")).toContainText(
      "没有已批准的导出准备",
      { timeout: 20_000 }
    );
    await expect(page.getByTestId("derivative-export-actions")).toHaveCount(0);
  });

  test("rejected artifact: export is blocked, never green", async ({ page }) => {
    const state = freshState();
    state.artifactStatus = "rejected";
    await mockApp(page, state);
    await gotoWriting(page);
    await expect(page.getByTestId("derivative-export-blocked")).toContainText(
      "未对应到有效的已批准 ExportPreparationArtifact",
      { timeout: 20_000 }
    );
    await expect(page.getByTestId("derivative-export-actions")).toHaveCount(0);
  });

  test("stale artifact: server freeze no longer replays the approved checksum", async ({ page }) => {
    const state = freshState();
    state.stale = true;
    await mockApp(page, state);
    await gotoWriting(page);
    await expect(page.getByTestId("derivative-export-blocked")).toContainText("已过期", {
      timeout: 20_000,
    });
    await expect(page.getByTestId("derivative-export-actions")).toHaveCount(0);
  });

  test("forged preparation hash: materialize 400 → comprehensible error + retry", async ({ page }) => {
    const state = freshState();
    state.materialize = "hash_mismatch";
    await mockApp(page, state);
    await gotoWriting(page);
    await readyPanel(page);

    await page.getByTestId("derivative-export-button-markdown").click({ force: true });
    await expect(page.getByTestId("derivative-export-error-markdown")).toContainText(
      "preparation_hash_mismatch",
      { timeout: 20_000 }
    );
    await expect(page.getByTestId("derivative-export-retry-markdown")).toBeVisible();

    // Retry after the server recovers submits the same approved-artifact intent.
    state.materialize = "ok";
    await page.getByTestId("derivative-export-retry-markdown").click({ force: true });
    await expect(page.getByTestId("derivative-export-done-markdown")).toContainText(
      "manifest 校验通过",
      { timeout: 20_000 }
    );
  });

  test("missing asset: materialize is blocked and the error is comprehensible", async ({ page }) => {
    const state = freshState();
    state.materialize = "missing_asset";
    await mockApp(page, state);
    await gotoWriting(page);
    await readyPanel(page);

    await page.getByTestId("derivative-export-button-epub").click({ force: true });
    await expect(page.getByTestId("derivative-export-error-epub")).toContainText(
      "missing_asset_blocks_package",
      { timeout: 20_000 }
    );
    await expect(page.getByTestId("derivative-export-retry-epub")).toBeVisible();
  });

  test("tampered download header fails closed with a verifiable error", async ({ page }) => {
    const state = freshState();
    state.headerHash = H(30); // server would never do this, but fail closed anyway
    await mockApp(page, state);
    await gotoWriting(page);
    await readyPanel(page);

    await page.getByTestId("derivative-export-button-markdown").click({ force: true });
    await expect(page.getByTestId("derivative-export-error-markdown")).toContainText(
      "manifest 头校验失败",
      { timeout: 20_000 }
    );
  });
});
