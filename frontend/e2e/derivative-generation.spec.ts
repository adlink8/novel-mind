/**
 * Phase 37-04 — Explicit divergence override browser/API UAT (REQ-FORK-03,
 * REQ-CRE-06, D-37-03/D-37-04).
 *
 * Proves the browser-visible override review gate against a route-mocked
 * backend (a mutable state machine that behaves like the deterministic
 * server):
 *   - generate a candidate through the sealed package -> strict candidate
 *     job, and see the blocked / needs_override gate verdict;
 *   - view the candidate evidence (citations, CanonDelta, gate snapshot);
 *   - fill a divergence override (reason + affected evidence) — a blank
 *     reason or no evidence is rejected fail-closed;
 *   - approve with an explicit approval note — the candidate materializes
 *     into a Fanfiction Canon derivative revision only and returns the
 *     immutable PublishedDerivativeRevision DTO (status `derivative_revision`,
 *     never `original`/`promoted`);
 *   - reject — no revision is created and a decided override cannot be
 *     re-approved;
 *   - refresh recovery — after a reload the override audit and revision are
 *     restored from the server (no client-only state).
 *
 * Routes are mocked (no real backend). The flow is driven through the browser
 * `fetch` stack (page.evaluate), which is intercepted by the same
 * `page.route` handlers the UI would hit. NOTE: on this machine the Next 16
 * canary dev server fails to compile (pre-existing), so this spec is kept
 * structurally valid and executed by the verification sub-agent when the
 * environment allows.
 */
import { createHash } from "crypto";
import { expect, test, type Page, type Route } from "@playwright/test";

// This spec drives the API directly from page.evaluate without navigating,
// so relative URLs never resolve. Match playwright.config.ts BASE_URL.
const BASE_URL = "http://127.0.0.1:3005";

const H = (n: number) => String(n).repeat(64);
const sha = (s: string) => createHash("sha256").update(s, "utf8").digest("hex");

const NOVEL_ID = 21;
const FORK_ID = 7;
const PROJECT_ID = 6;
const CHAPTER_ID = 11;

const OWNER = { id: 1, username: "owner", email: "owner@example.com", is_active: true };

const PACKAGE_HASH = H(1);
const DELTA_HASH = H(2);
const REQ_HASH = H(3);
const RESP_HASH = H(4);
const CANDIDATE_EVIDENCE = "fork:ff-override:chapter:1";

interface Candidate {
  id: number;
  job_id: number;
  gate_verdict: "needs_override" | "blocked" | "candidate";
  gate_reason?: string;
  draft_text: string;
  citation_keys: string[];
  divergence: { divergence_type: string; reason: string; affected_evidence: string[]; scope: string } | null;
  canon_delta_hash: string | null;
  approval_state: string;
}

interface OverrideRow {
  id: number;
  candidate_id: number;
  project_id: number;
  chapter_id: number;
  kind: string;
  reason: string;
  affected_evidence: string[];
  canon_delta_hash: string;
  approval_state: "pending" | "approved" | "rejected";
  approval_reason: string | null;
}

interface GenState {
  jobId: number;
  candidate: Candidate | null;
  override: OverrideRow | null;
  revisionAppended: number; // count of agent_proposal rows appended
  chapterMarkdown: string;
}

function freshState(): GenState {
  return {
    jobId: 0,
    candidate: null,
    override: null,
    revisionAppended: 0,
    chapterMarkdown: "",
  };
}

function candidateFor(jobId: number): Candidate {
  return {
    id: 100 + jobId,
    job_id: jobId,
    gate_verdict: "needs_override",
    gate_reason: "divergence_requires_override",
    draft_text: "阿宁在竹林入口站定，深吸一口气，终究没有说出秘密。",
    citation_keys: [CANDIDATE_EVIDENCE],
    divergence: {
      divergence_type: "character",
      reason: "the twist requires the hero to know the secret early",
      affected_evidence: [CANDIDATE_EVIDENCE],
      scope: "derivative",
    },
    canon_delta_hash: DELTA_HASH,
    approval_state: "needs_override",
  };
}

function publishedFor(state: GenState) {
  const row = state.override!;
  return {
    owner_id: OWNER.id,
    project_id: PROJECT_ID,
    fork_id: FORK_ID,
    revision_id: 9000 + state.revisionAppended,
    version_id: state.revisionAppended + 1,
    status: "derivative_revision",
    source_snapshot: H(5),
    manifest_hash: H(6),
    citation_hash: sha(state.candidate!.citation_keys.sort().join(",")),
    asset_hashes: [],
    approval: {
      approval_state: "approved",
      approver_id: OWNER.id,
      approved_at: "2026-08-04T00:00:00Z",
      approval_reason: row.approval_reason,
      kind: row.kind,
      reason: row.reason,
    },
    review: {
      gate_verdict: state.candidate!.gate_verdict,
      gate_reason: "divergence_requires_override",
      canon_delta_hash: row.canon_delta_hash,
      evidence_snapshot: { gate_verdict: state.candidate!.gate_verdict },
    },
  };
}

function overrideFor(state: GenState): OverrideRow {
  return {
    id: 500 + state.jobId,
    candidate_id: state.candidate!.id,
    project_id: PROJECT_ID,
    chapter_id: CHAPTER_ID,
    kind: state.candidate!.divergence?.divergence_type ?? "other",
    reason: state.candidate!.divergence?.reason ?? "owner-stated divergence",
    affected_evidence: state.candidate!.divergence?.affected_evidence ?? [CANDIDATE_EVIDENCE],
    canon_delta_hash: state.candidate!.canon_delta_hash ?? DELTA_HASH,
    approval_state: "pending",
    approval_reason: null,
  };
}

const json = (route: Route, body: unknown, opts?: { status?: number }) =>
  route.fulfill({
    contentType: "application/json",
    status: opts?.status ?? 200,
    json: body,
  });

/**
 * Mock backend. `state` is mutable so each step behaves like the deterministic
 * server: the override gate rejects blank reason/evidence/approval and only an
 * approved override appends an agent_proposal revision.
 */
async function mockApp(page: Page, state: GenState) {
  await page.route("**/api/**", (route) =>
    json(route, { detail: "unmocked e2e endpoint" })
  );
  await page.route("**/api/auth/me", (route) => json(route, OWNER));

  await page.route("**/api/novels/**/derivative-context-packages", (route) => {
    if (route.request().method() !== "POST") return;
    json(route, {
      package: {
        id: 1,
        owner_id: OWNER.id,
        novel_id: NOVEL_ID,
        fork_id: FORK_ID,
        package_hash: PACKAGE_HASH,
        intent: "continuation",
        space: "fanfiction_canon",
        source_snapshot_hash: H(5),
        manifest_hash: H(6),
      },
      replayed: false,
    });
  });

  await page.route("**/api/novels/**/derivative-generation-jobs", (route) => {
    if (route.request().method() !== "POST") return;
    state.jobId += 1;
    json(route, {
      job: {
        id: state.jobId,
        owner_id: OWNER.id,
        novel_id: NOVEL_ID,
        fork_id: FORK_ID,
        context_package_id: 1,
        package_hash: PACKAGE_HASH,
        intent: "continuation",
        status: "queued",
        idempotency_key: H(7),
        prompt_hash: H(8),
        schema_hash: H(9),
        config_hash: H(10),
        model_lineage: {},
        price_snapshot: {},
        budget_policy: {},
      },
      replayed: false,
    });
  });

  await page.route("**/api/novels/**/derivative-generation-jobs/*/run", async (route) => {
    state.candidate = candidateFor(state.jobId);
    json(route, {
      job: { id: state.jobId, status: "needs_override", error_code: "divergence_requires_override" },
      candidate: {
        id: state.candidate.id,
        job_id: state.jobId,
        intent: "continuation",
        draft_text: state.candidate.draft_text,
        citation_keys: state.candidate.citation_keys,
        divergence: state.candidate.divergence,
        canon_delta_hash: state.candidate.canon_delta_hash,
        gate_verdict: state.candidate.gate_verdict,
        gate_reason: "divergence_requires_override",
        package_hash: PACKAGE_HASH,
        prompt_hash: H(8),
        schema_hash: H(9),
        request_hash: REQ_HASH,
        response_hash: RESP_HASH,
        usage: { input_tokens: 5, output_tokens: 2 },
        cost_usd: "0.00001",
        model_lineage: {},
        approval_state: "needs_override",
      },
      attempts: [
        { id: 1, job_id: state.jobId, attempt_number: 1, status: "succeeded", provider: "fake", model_id: "fake/1", request_hash: REQ_HASH, response_hash: RESP_HASH },
      ],
    });
  });

  await page.route("**/api/novels/**/derivative-generation-jobs/*", (route) => {
    json(route, {
      job: { id: state.jobId, status: state.candidate?.gate_verdict ?? "queued", error_code: state.candidate?.gate_verdict === "needs_override" ? "divergence_requires_override" : null },
      candidate: state.candidate ? { ...state.candidate } : null,
      attempts: [],
    });
  });

  await page.route("**/api/novels/**/derivative-overrides", async (route) => {
    if (route.request().method() !== "POST") {
      // Let the GET list handler below serve it (route.fallback continues to
      // the next matching route); without this the handler returns without
      // fulfilling and the request hangs.
      await route.fallback();
      return;
    }
    const body = route.request().postDataJSON();
    if (!(body?.reason ?? "").trim()) {
      await json(route, { detail: "missing_reason: an explicit divergence override requires a reason" }, { status: 400 });
      return;
    }
    if (!body?.affected_evidence?.length) {
      await json(route, { detail: "missing_evidence: an override must affect at least one evidence key from the sealed package" }, { status: 400 });
      return;
    }
    if (state.candidate?.gate_verdict === "candidate") {
      await json(route, { detail: "candidate_not_overridable: only blocked or needs_override candidates accept an explicit override" }, { status: 409 });
      return;
    }
    state.override = overrideFor(state);
    json(route, {
      override: { ...state.override },
      message: "divergence override recorded (pending); it becomes a derivative revision only after the owner's explicit approval",
    });
  });

  await page.route("**/api/novels/**/derivative-overrides/*/approve", async (route) => {
    const row = state.override;
    if (!row || row.approval_state !== "pending") {
      await json(route, { detail: "already_decided: a decided override cannot be re-approved" }, { status: 409 });
      return;
    }
    const body = route.request().postDataJSON();
    if (!(body?.approval_reason ?? "").trim()) {
      await json(route, { detail: "missing_approval: approving a divergence override requires an explicit approval note" }, { status: 400 });
      return;
    }
    row.approval_state = "approved";
    row.approval_reason = body.approval_reason;
    state.revisionAppended += 1;
    state.chapterMarkdown = state.candidate!.draft_text;
    json(route, {
      override: { ...row },
      published: publishedFor(state),
      message: "override approved; candidate materialized into a Fanfiction Canon derivative revision (never Original, never promoted)",
    });
  });

  await page.route("**/api/novels/**/derivative-overrides/*/reject", async (route) => {
    const row = state.override;
    if (!row || row.approval_state !== "pending") {
      await json(route, { detail: "already_decided" }, { status: 409 });
      return;
    }
    row.approval_state = "rejected";
    json(route, { override: { ...row }, message: "override rejected; no derivative revision was materialized" });
  });

  await page.route("**/api/novels/**/derivative-overrides", async (route) => {
    if (route.request().method() !== "GET") {
      // Let the POST creation handler above serve it (fallback continues to
      // the previously registered route in LIFO order).
      await route.fallback();
      return;
    }
    json(route, {
      novel_id: NOVEL_ID,
      total: state.override ? 1 : 0,
      items: state.override ? [{ ...state.override }] : [],
    });
  });

  await page.route("**/api/novels/**/derivative-overrides/*", (route) => {
    json(route, { override: state.override ? { ...state.override } : null });
  });

  await page.route("**/api/novels/**/derivative-projects/*/chapters/*/revisions", (route) => {
    json(route, {
      chapter_id: CHAPTER_ID,
      project_id: PROJECT_ID,
      total: 1 + state.revisionAppended,
      items: [
        ...Array.from({ length: state.revisionAppended }, (_, i) => ({
          id: 9000 + i + 1,
          chapter_id: CHAPTER_ID,
          project_id: PROJECT_ID,
          revision_number: i + 2,
          kind: "agent_proposal",
          content_checksum: H(11 + i),
          actor_id: OWNER.id,
          reason: `divergence override:${state.override?.id ?? 0}:${state.override?.kind ?? "character"}`,
          approval_state: "approved",
        })),
        { id: 9000, chapter_id: CHAPTER_ID, project_id: PROJECT_ID, revision_number: 1, kind: "create", content_checksum: H(20), actor_id: OWNER.id, reason: null, approval_state: "not_required" },
      ],
    });
  });
}

/** Drive a JSON request through the browser fetch stack (route-intercepted). */
async function api(
  page: Page,
  method: string,
  path: string,
  body?: unknown
): Promise<{ status: number; data: any }> {
  // This spec never navigates (the page stays about:blank), so a relative
  // fetch inside page.evaluate fails to parse. Resolve against the app origin.
  const url = new URL(path, BASE_URL).toString();
  return page.evaluate(
    async ({ method, url, body }) => {
      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      let data: unknown = null;
      try {
        data = await res.json();
      } catch {
        data = null;
      }
      return { status: res.status, data };
    },
    { method, url, body }
  );
}

test.describe("derivative generation override review", () => {
  test("generate -> blocked/needs_override -> view evidence", async ({ page }) => {
    const state = freshState();
    await mockApp(page, state);

    // Compile a sealed package.
    const compiled = await api(page, "POST", `/api/novels/${NOVEL_ID}/derivative-context-packages`, {
      fork_id: FORK_ID,
      intent: "continuation",
    });
    expect(compiled.status).toBe(200);
    expect(compiled.data.package.space).toBe("fanfiction_canon");

    // Create + run the generation job.
    const created = await api(page, "POST", `/api/novels/${NOVEL_ID}/derivative-generation-jobs`, {
      context_package_id: 1,
      intent: "continuation",
      job_key: "e2e-divergence",
    });
    expect(created.data.job.status).toBe("queued");
    const run = await api(page, "POST", `/api/novels/${NOVEL_ID}/derivative-generation-jobs/${created.data.job.id}/run`);
    expect(run.data.job.status).toBe("needs_override");
    expect(run.data.candidate.gate_verdict).toBe("needs_override");
    expect(run.data.candidate.divergence.divergence_type).toBe("character");

    // View evidence: the candidate detail restores the gate + divergence audit.
    const detail = await api(page, "GET", `/api/novels/${NOVEL_ID}/derivative-generation-jobs/${created.data.job.id}`);
    expect(detail.data.candidate.citation_keys).toContain(CANDIDATE_EVIDENCE);
    expect(detail.data.candidate.gate_reason).toBe("divergence_requires_override");
    expect(detail.data.candidate.canon_delta_hash).toHaveLength(64);
  });

  test("fill divergence: blank reason / no evidence are rejected fail-closed", async ({ page }) => {
    const state = freshState();
    await mockApp(page, state);
    await api(page, "POST", `/api/novels/${NOVEL_ID}/derivative-generation-jobs`, {
      context_package_id: 1, intent: "continuation", job_key: "e2e-div",
    });
    await api(page, "POST", `/api/novels/${NOVEL_ID}/derivative-generation-jobs/${state.jobId}/run`);

    const blank = await api(page, "POST", `/api/novels/${NOVEL_ID}/derivative-overrides`, {
      candidate_id: state.candidate!.id,
      project_id: PROJECT_ID,
      chapter_id: CHAPTER_ID,
      reason: "   ",
      affected_evidence: [CANDIDATE_EVIDENCE],
    });
    expect(blank.status).toBe(400);
    expect(blank.data.detail).toContain("missing_reason");

    const noEvidence = await api(page, "POST", `/api/novels/${NOVEL_ID}/derivative-overrides`, {
      candidate_id: state.candidate!.id,
      project_id: PROJECT_ID,
      chapter_id: CHAPTER_ID,
      reason: "the twist requires the hero to know the secret",
      affected_evidence: [],
    });
    expect(noEvidence.status).toBe(400);
    expect(noEvidence.data.detail).toContain("missing_evidence");

    const ok = await api(page, "POST", `/api/novels/${NOVEL_ID}/derivative-overrides`, {
      candidate_id: state.candidate!.id,
      project_id: PROJECT_ID,
      chapter_id: CHAPTER_ID,
      reason: "the twist requires the hero to know the secret",
      affected_evidence: [CANDIDATE_EVIDENCE],
    });
    expect(ok.status).toBe(200);
    expect(ok.data.override.approval_state).toBe("pending");
  });

  test("approval: explicit approval note materializes a Fanfiction revision only", async ({ page }) => {
    const state = freshState();
    await mockApp(page, state);
    await api(page, "POST", `/api/novels/${NOVEL_ID}/derivative-generation-jobs`, {
      context_package_id: 1, intent: "continuation", job_key: "e2e-approve",
    });
    await api(page, "POST", `/api/novels/${NOVEL_ID}/derivative-generation-jobs/${state.jobId}/run`);
    await api(page, "POST", `/api/novels/${NOVEL_ID}/derivative-overrides`, {
      candidate_id: state.candidate!.id,
      project_id: PROJECT_ID,
      chapter_id: CHAPTER_ID,
      reason: "the twist requires the hero to know the secret",
      affected_evidence: [CANDIDATE_EVIDENCE],
    });

    // An approval without an approval note is rejected.
    const noApproval = await api(page, "POST", `/api/novels/${NOVEL_ID}/derivative-overrides/${state.override!.id}/approve`, {
      approval_reason: "",
    });
    expect(noApproval.status).toBe(400);
    expect(noApproval.data.detail).toContain("missing_approval");

    const approved = await api(page, "POST", `/api/novels/${NOVEL_ID}/derivative-overrides/${state.override!.id}/approve`, {
      approval_reason: "owner approved the twist divergence",
    });
    expect(approved.status).toBe(200);
    expect(approved.data.override.approval_state).toBe("approved");
    // Immutable Phase 39 DTO: derivative-only status, no promotion.
    expect(approved.data.published.status).toBe("derivative_revision");
    expect(approved.data.published.owner_id).toBe(OWNER.id);
    expect(approved.data.published.approval.kind).toBe("character");
    expect(approved.data.published.review.gate_verdict).toBe("needs_override");
    expect(approved.data.published.asset_hashes).toEqual([]);
    expect(state.revisionAppended).toBe(1);

    // The revision history now shows one immutable agent_proposal row.
    const revisions = await api(page, "GET", `/api/novels/${NOVEL_ID}/derivative-projects/${PROJECT_ID}/chapters/${CHAPTER_ID}/revisions`);
    expect(revisions.data.items.filter((r: any) => r.kind === "agent_proposal")).toHaveLength(1);
  });

  test("rejection: no revision is created and a decided override cannot be re-approved", async ({ page }) => {
    const state = freshState();
    await mockApp(page, state);
    await api(page, "POST", `/api/novels/${NOVEL_ID}/derivative-generation-jobs`, {
      context_package_id: 1, intent: "continuation", job_key: "e2e-reject",
    });
    await api(page, "POST", `/api/novels/${NOVEL_ID}/derivative-generation-jobs/${state.jobId}/run`);
    await api(page, "POST", `/api/novels/${NOVEL_ID}/derivative-overrides`, {
      candidate_id: state.candidate!.id,
      project_id: PROJECT_ID,
      chapter_id: CHAPTER_ID,
      reason: "the twist requires the hero to know the secret",
      affected_evidence: [CANDIDATE_EVIDENCE],
    });

    const rejected = await api(page, "POST", `/api/novels/${NOVEL_ID}/derivative-overrides/${state.override!.id}/reject`, {
      rejection_reason: "author changed their mind",
    });
    expect(rejected.status).toBe(200);
    expect(rejected.data.override.approval_state).toBe("rejected");
    expect(state.revisionAppended).toBe(0);

    const lateApproval = await api(page, "POST", `/api/novels/${NOVEL_ID}/derivative-overrides/${state.override!.id}/approve`, {
      approval_reason: "late approval",
    });
    expect(lateApproval.status).toBe(409);
    expect(lateApproval.data.detail).toContain("already_decided");
  });

  test("refresh recovery: the override audit and revision survive a reload", async ({ page }) => {
    const state = freshState();
    await mockApp(page, state);
    await api(page, "POST", `/api/novels/${NOVEL_ID}/derivative-generation-jobs`, {
      context_package_id: 1, intent: "continuation", job_key: "e2e-refresh",
    });
    await api(page, "POST", `/api/novels/${NOVEL_ID}/derivative-generation-jobs/${state.jobId}/run`);
    await api(page, "POST", `/api/novels/${NOVEL_ID}/derivative-overrides`, {
      candidate_id: state.candidate!.id,
      project_id: PROJECT_ID,
      chapter_id: CHAPTER_ID,
      reason: "the twist requires the hero to know the secret",
      affected_evidence: [CANDIDATE_EVIDENCE],
    });
    await api(page, "POST", `/api/novels/${NOVEL_ID}/derivative-overrides/${state.override!.id}/approve`, {
      approval_reason: "owner approved the twist divergence",
    });

    // Simulate a page reload: the route mock state machine is the "server".
    await page.reload();
    const listed = await api(page, "GET", `/api/novels/${NOVEL_ID}/derivative-overrides`);
    expect(listed.data.total).toBe(1);
    expect(listed.data.items[0].approval_state).toBe("approved");
    expect(listed.data.items[0].approval_reason).toBe("owner approved the twist divergence");

    const revisions = await api(page, "GET", `/api/novels/${NOVEL_ID}/derivative-projects/${PROJECT_ID}/chapters/${CHAPTER_ID}/revisions`);
    expect(revisions.data.items.some((r: any) => r.kind === "agent_proposal")).toBe(true);
  });
});
