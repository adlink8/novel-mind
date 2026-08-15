import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  DerivativeExportAuditReport,
  DerivativeExportFormat,
} from "@/lib/derivative-export-api";
import {
  AUDIT_DIMENSION_LABEL_TEXT,
  AUDIT_STATUS_LABEL_TEXT,
  ExportPanel,
  shortExportHash,
} from "./export-panel";

/**
 * 39-03 colocated vitest —— derivative export review + download panel.
 *
 * 覆盖 D-39-03 / T-39-03-01/02 的前端约束：
 * - 只能从已批准 ExportPreparationArtifact 请求导出（materialize），浏览器
 *   不组装 manifest / 不选择 live revision；
 * - 展示 preparation_id / revision / export version / manifest checksum /
 *   approved counts / 三维 audit + blocked reasons；
 * - 下载后校验 manifest 头；下载完成绝不显示为质量通过；
 * - EPUB 无 validator → 明确 unverified，不得标绿；
 * - pending / rejected / stale / forged hash / missing asset / header 不匹配
 *   → 可理解错误 + 重试入口（fail closed）。
 */

const mocks = vi.hoisted(() => ({
  listApprovalRequests: vi.fn(),
  getArtifact: vi.fn(),
  listArtifactRevisions: vi.fn(),
  audit: vi.fn(),
  agentPrepare: vi.fn(),
  materialize: vi.fn(),
  download: vi.fn(),
}));

vi.mock("@/lib/derivative-export-api", () => ({
  derivativeExportApi: {
    listApprovalRequests: mocks.listApprovalRequests,
    getArtifact: mocks.getArtifact,
    listArtifactRevisions: mocks.listArtifactRevisions,
    audit: mocks.audit,
    agentPrepare: mocks.agentPrepare,
    materialize: mocks.materialize,
    download: mocks.download,
  },
}));

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const H = (n: number) => String(n).repeat(64);

const NOVEL_ID = 22;
const PROJECT_ID = 5;
const ARTIFACT_ID = 7;
const REVISION_ID = 70;
const APPROVAL_ID = 500;
const MANIFEST_CHECKSUM = H(1);
const PREPARATION_HASH = H(2);
const SNAPSHOT_HASH = H(3);

const approval = {
  id: APPROVAL_ID,
  owner_id: 11,
  action: "approve_export",
  status: "approved",
  payload_summary: {
    project_id: PROJECT_ID,
    project_key: "proj-uat",
    fork_id: 3,
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
};

const artifact = {
  id: ARTIFACT_ID,
  owner_id: 11,
  novel_id: NOVEL_ID,
  type: "export_preparation",
  schema_version: "export-preparation.v1",
  status: "approved",
  branch: "deriv-branch",
  input_hash: H(4),
  current_revision_id: REVISION_ID,
};

const revision = {
  id: REVISION_ID,
  artifact_id: ARTIFACT_ID,
  owner_id: 11,
  novel_id: NOVEL_ID,
  revision_no: 1,
  content: {
    type: "export_preparation",
    preparation: {
      schema_version: "export-preparation.v1",
      artifact_kind: "export_preparation",
      authority_space: "derivative",
      fork: "ff-uat",
      project_id: PROJECT_ID,
      project_key: "proj-uat",
      source_snapshot: {
        source_snapshot_id: "novel:22:ff-uat",
        source_snapshot_hash: H(4),
        source_manifest_hash: H(5),
        cutoff_chapter: 2,
      },
      base_revision: {
        project_manifest_hash: H(5),
        scope_hash: H(6),
        cutoff_snapshot_hash: H(7),
        text_version_hash: H(8),
      },
      content_hash: MANIFEST_CHECKSUM,
      evidence_refs: ["fork:ff-uat:chapter:1"],
      generator_lineage: {},
      validator_report: { verdict: "candidate", reasons: ["deterministic_preparation_ok"] },
      review_state: "candidate",
      approval_request_id: null,
      materialize_lineage: {},
    },
  },
};

const auditReport = {
  schema_version: "derivative-export-audit.v1",
  audit_version: "derivative-export-audit.v1",
  owner_id: 11,
  novel_id: NOVEL_ID,
  project_id: PROJECT_ID,
  snapshot_hash: SNAPSHOT_HASH,
  dimensions: [
    {
      dimension: "implementation_readiness",
      status: "verified",
      blocked_reasons: [],
      evidence: [],
    },
    {
      dimension: "sample_data_coverage",
      status: "verified",
      blocked_reasons: [],
      evidence: [],
    },
    {
      dimension: "quality_qualification",
      status: "blocked",
      blocked_reasons: [H(9)],
      evidence: [],
    },
  ],
  verdict: "blocked",
  blocked_reasons: [H(9)],
  report_hash: H(10),
  phase22: {
    green_observed: 0,
    green_required: 3,
    source: ".planning/STATE.md",
    source_hash: H(11),
  },
} as DerivativeExportAuditReport;

const prepareResponse = {
  preparation: {},
  preparation_hash: PREPARATION_HASH,
  snapshot_hash: SNAPSHOT_HASH,
  manifest_hash: MANIFEST_CHECKSUM,
  schema_version: "export-preparation.v1",
  export_version: "1.0.0",
  project_id: PROJECT_ID,
  fork_id: 3,
  chapter_count: 2,
  asset_count: 1,
  revision_count: 2,
  citation_count: 1,
  candidate_only: true,
};

const materializeResponse = {
  owner_id: 11,
  novel_id: NOVEL_ID,
  project_id: PROJECT_ID,
  fork_id: 3,
  artifact_id: ARTIFACT_ID,
  artifact_revision_id: REVISION_ID,
  approval_request_id: APPROVAL_ID,
  approval_action: "approve_export",
  approval_status: "approved",
  preparation_hash: PREPARATION_HASH,
  snapshot_hash: SNAPSHOT_HASH,
  manifest_hash: MANIFEST_CHECKSUM,
  package_hash: H(12),
  package_schema_version: "derivative-export-package.v1",
  bundle_size: 123,
  bundle_formats: ["package"],
  status: "approved",
  candidate_only: false,
  materialized: true,
};

function wireReady() {
  mocks.listApprovalRequests.mockResolvedValue({
    data: { items: [approval], total: 1, skip: 0, limit: 100 },
  });
  mocks.getArtifact.mockResolvedValue({ data: artifact });
  mocks.listArtifactRevisions.mockResolvedValue({
    data: { items: [revision], total: 1 },
  });
  mocks.audit.mockResolvedValue({ data: auditReport });
  mocks.agentPrepare.mockResolvedValue({ data: prepareResponse });
}

function wireDownloadOk(format: DerivativeExportFormat) {
  mocks.materialize.mockResolvedValue({ data: materializeResponse });
  mocks.download.mockResolvedValue({
    data: new Blob(["# content"], { type: format === "markdown" ? "text/markdown" : "application/epub+zip" }),
    headers: { "x-export-manifest-hash": MANIFEST_CHECKSUM },
  });
}

function renderPanel() {
  return render(<ExportPanel novelId={String(NOVEL_ID)} projectId={PROJECT_ID} />);
}

function dimensionRow(kind: string): HTMLElement {
  const row = screen
    .getAllByTestId("derivative-export-dimension")
    .find((el) => el.getAttribute("data-dimension") === kind);
  if (!row) throw new Error(`dimension row ${kind} not rendered`);
  return row;
}

describe("ExportPanel", () => {
  it("shows a loading state before the approved artifact resolves", () => {
    mocks.listApprovalRequests.mockReturnValue(new Promise(() => undefined));
    renderPanel();
    expect(screen.getByTestId("derivative-export-loading")).toBeInTheDocument();
  });

  it("renders an explicit empty state when there is no approved approval", async () => {
    mocks.listApprovalRequests.mockResolvedValue({
      data: { items: [], total: 0, skip: 0, limit: 100 },
    });
    renderPanel();
    expect(await screen.findByTestId("derivative-export-empty")).toHaveTextContent(
      "没有已批准的导出准备"
    );
  });

  it("filters out a pending approve_export approval (no approved artifact yet)", async () => {
    mocks.listApprovalRequests.mockResolvedValue({
      data: {
        items: [{ ...approval, status: "pending" }],
        total: 1,
        skip: 0,
        limit: 100,
      },
    });
    renderPanel();
    expect(await screen.findByTestId("derivative-export-empty")).toHaveTextContent(
      "没有已批准的导出准备"
    );
  });

  it("renders artifact provenance, counts and the three-dimension audit from the envelope", async () => {
    wireReady();
    renderPanel();
    await screen.findByTestId("derivative-export-ready");

    expect(screen.getByTestId("derivative-export-approved-badge")).toHaveTextContent(
      "已批准"
    );
    expect(screen.getByTestId("derivative-export-preparation-id")).toHaveTextContent(
      `#${ARTIFACT_ID}`
    );
    expect(screen.getByTestId("derivative-export-revision")).toHaveTextContent(
      `#${REVISION_ID}`
    );
    expect(screen.getByTestId("derivative-export-version")).toHaveTextContent("v1.0.0");
    expect(screen.getByTestId("derivative-export-manifest-checksum")).toHaveTextContent(
      shortExportHash(MANIFEST_CHECKSUM)
    );
    expect(screen.getByTestId("derivative-export-counts")).toHaveTextContent(
      "2 章 · 1 资产 · 1 引用 · 2 修订"
    );

    // Three-dimension audit: quality is blocked (Phase 22 0/3), not green.
    expect(screen.getByTestId("derivative-export-audit")).toHaveAttribute(
      "data-verdict",
      "blocked"
    );
    expect(screen.getByTestId("derivative-export-verdict")).toHaveTextContent("阻断");
    expect(dimensionRow("implementation_readiness")).toHaveAttribute(
      "data-status",
      "verified"
    );
    expect(dimensionRow("sample_data_coverage")).toHaveAttribute("data-status", "verified");
    expect(dimensionRow("quality_qualification")).toHaveAttribute(
      "data-status",
      "blocked"
    );
    expect(screen.getByTestId("derivative-export-phase22")).toHaveTextContent("0/3");
    expect(AUDIT_DIMENSION_LABEL_TEXT.quality_qualification).toBe("质量资格");
    expect(AUDIT_STATUS_LABEL_TEXT.blocked).toBe("已阻断");
  });

  it("blocks a stale artifact: server freeze no longer replays the approved checksum", async () => {
    wireReady();
    mocks.agentPrepare.mockResolvedValue({
      data: { ...prepareResponse, manifest_hash: H(20) },
    });
    renderPanel();
    const blocked = await screen.findByTestId("derivative-export-blocked");
    expect(blocked).toHaveTextContent("已过期");
    expect(screen.queryByTestId("derivative-export-actions")).not.toBeInTheDocument();
  });

  it("blocks when the approved artifact is not an export preparation", async () => {
    wireReady();
    mocks.getArtifact.mockResolvedValue({ data: { ...artifact, type: "cited_answer" } });
    renderPanel();
    expect(await screen.findByTestId("derivative-export-blocked")).toHaveTextContent(
      "未对应到有效的已批准 ExportPreparationArtifact"
    );
  });

  it("exports markdown through materialize only, verifies the manifest header, and never calls it a quality pass", async () => {
    wireReady();
    wireDownloadOk("markdown");
    renderPanel();
    await screen.findByTestId("derivative-export-ready");

    fireEvent.click(screen.getByTestId("derivative-export-button-markdown"));
    await waitFor(() => expect(mocks.materialize).toHaveBeenCalledTimes(1));
    const [novelId, projectId, body] = mocks.materialize.mock.calls[0];
    expect(novelId).toBe(String(NOVEL_ID));
    expect(projectId).toBe(PROJECT_ID);
    expect(body.artifact_id).toBe(ARTIFACT_ID);
    expect(body.artifact_revision_id).toBe(REVISION_ID);
    expect(body.approval_id).toBe(APPROVAL_ID);
    expect(body.preparation_hash).toBe(PREPARATION_HASH);
    expect(body.fork).toBe("ff-uat");

    const done = await screen.findByTestId("derivative-export-done-markdown");
    expect(done).toHaveTextContent("manifest 校验通过");
    // The download result is never presented as a quality pass.
    expect(done).not.toHaveTextContent("质量通过");
    expect(done).not.toHaveTextContent("质量资格通过");
    expect(mocks.download).toHaveBeenCalledWith(String(NOVEL_ID), PROJECT_ID, "markdown");
    // The audit still reports the real (blocked) quality state.
    expect(dimensionRow("quality_qualification")).toHaveAttribute("data-status", "blocked");
  });

  it("labels EPUB interoperability as explicitly unverified (no validator, not green)", async () => {
    wireReady();
    wireDownloadOk("epub");
    renderPanel();
    await screen.findByTestId("derivative-export-ready");

    fireEvent.click(screen.getByTestId("derivative-export-button-epub"));
    const done = await screen.findByTestId("derivative-export-done-epub");
    expect(done).toHaveTextContent("manifest 校验通过");
    expect(done).toHaveTextContent("EPUB 互操作性未验证");
    expect(done).toHaveTextContent("不标绿");
    expect(done).not.toHaveTextContent("质量通过");
  });

  it("surfaces a forged preparation hash as a comprehensible error with a retry entry", async () => {
    wireReady();
    mocks.materialize.mockRejectedValue({
      response: {
        data: {
          detail:
            "preparation_hash_mismatch: supplied preparation_hash no longer replays the frozen export lineage (stale preparation)",
        },
      },
    });
    renderPanel();
    await screen.findByTestId("derivative-export-ready");

    fireEvent.click(screen.getByTestId("derivative-export-button-markdown"));
    const error = await screen.findByTestId("derivative-export-error-markdown");
    expect(error).toHaveTextContent("preparation_hash_mismatch");
    const retry = screen.getByTestId("derivative-export-retry-markdown");
    expect(retry).toBeInTheDocument();

    // Retry submits the same approved-artifact materialize intent again.
    mocks.materialize.mockResolvedValue({ data: materializeResponse });
    mocks.download.mockResolvedValue({
      data: new Blob(["# content"]),
      headers: { "x-export-manifest-hash": MANIFEST_CHECKSUM },
    });
    fireEvent.click(retry);
    await screen.findByTestId("derivative-export-done-markdown");
    expect(mocks.materialize).toHaveBeenCalledTimes(2);
  });

  it("blocks export when a missing asset prevents a complete bundle", async () => {
    wireReady();
    mocks.materialize.mockRejectedValue({
      response: {
        data: {
          detail:
            "bundle_blocked: derivative export package blocked: missing_asset_blocks_package",
        },
      },
    });
    renderPanel();
    await screen.findByTestId("derivative-export-ready");

    fireEvent.click(screen.getByTestId("derivative-export-button-epub"));
    const error = await screen.findByTestId("derivative-export-error-epub");
    expect(error).toHaveTextContent("missing_asset_blocks_package");
    expect(screen.getByTestId("derivative-export-retry-epub")).toBeInTheDocument();
  });

  it("fails closed when the downloaded manifest header does not replay the artifact checksum", async () => {
    wireReady();
    mocks.materialize.mockResolvedValue({ data: materializeResponse });
    mocks.download.mockResolvedValue({
      data: new Blob(["# tampered"]),
      headers: { "x-export-manifest-hash": H(30) },
    });
    renderPanel();
    await screen.findByTestId("derivative-export-ready");

    fireEvent.click(screen.getByTestId("derivative-export-button-markdown"));
    const error = await screen.findByTestId("derivative-export-error-markdown");
    expect(error).toHaveTextContent("manifest 头校验失败");
  });

  it("re-fetches the approved artifact + audit on reload (server is the source of truth)", async () => {
    wireReady();
    renderPanel();
    await screen.findByTestId("derivative-export-ready");
    mocks.listApprovalRequests.mockClear();
    mocks.getArtifact.mockClear();
    mocks.audit.mockClear();

    wireReady();
    fireEvent.click(screen.getByTestId("derivative-export-reload"));
    await waitFor(() => expect(screen.getByTestId("derivative-export-ready")).toBeInTheDocument());
    expect(mocks.listApprovalRequests).toHaveBeenCalled();
    expect(mocks.getArtifact).toHaveBeenCalledWith(String(NOVEL_ID), ARTIFACT_ID);
    expect(mocks.audit).toHaveBeenCalledWith(String(NOVEL_ID), PROJECT_ID);
  });
});

describe("shortExportHash", () => {
  it("shortens sha256 hashes for display and passes short values through", () => {
    expect(shortExportHash(H(40))).toMatch(/^.{8}….{4}$/);
    expect(shortExportHash("abc")).toBe("abc");
    expect(shortExportHash(null)).toBe("");
  });
});
