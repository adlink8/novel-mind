/**
 * API consumer contracts for legacy Eval + quality jobs (06-05).
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

const { mockGet, mockPost, mockPut, mockPatch, mockDelete } = vi.hoisted(() => ({
  mockGet: vi.fn().mockResolvedValue({ data: {} }),
  mockPost: vi.fn().mockResolvedValue({ data: {} }),
  mockPut: vi.fn().mockResolvedValue({ data: {} }),
  mockPatch: vi.fn().mockResolvedValue({ data: {} }),
  mockDelete: vi.fn().mockResolvedValue({ data: {} }),
}));

vi.mock("axios", () => ({
  default: {
    create: vi.fn(() => ({
      get: mockGet,
      post: mockPost,
      put: mockPut,
      patch: mockPatch,
      delete: mockDelete,
      defaults: { baseURL: "/api", timeout: 30000 },
      interceptors: {
        request: { use: vi.fn(), eject: vi.fn() },
        response: { use: vi.fn(), eject: vi.fn() },
      },
    })),
  },
}));

import {
  evalApi,
  QUALITY_STATUSES,
  QUALITY_TERMINAL_STATUSES,
  QUALITY_COMPARABLE_STATUSES,
  QUALITY_STATUS_LABELS,
  isQualityComparable,
  qualityStatusTone,
  type DeprecationMeta,
} from "./api";

const SAMPLE_DEPRECATION: DeprecationMeta = {
  deprecated: true,
  legacy_eval_api: true,
  replacement: {
    create: "POST /api/eval/quality/runs",
    status: "GET /api/eval/quality/runs/{job_id}",
    report: "GET /api/eval/quality/runs/{job_id}",
    resume: "POST /api/eval/quality/runs/{job_id}/resume",
    cancel: "POST /api/eval/quality/runs/{job_id}/cancel",
  },
  migration: "Migrate gold_chunks to content-hash EvidenceRef",
  quality_comparable_default: false,
};

describe("evalApi contract", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("lists datasets via GET /eval/datasets", async () => {
    await evalApi.listDatasets({ novel_id: 1, status: "confirmed" });
    expect(mockGet).toHaveBeenCalledWith(
      "/eval/datasets?novel_id=1&status=confirmed"
    );
  });

  it("lists legacy runs via GET /eval/runs", async () => {
    await evalApi.listRuns(3);
    expect(mockGet).toHaveBeenCalledWith("/eval/runs?novel_id=3");
  });

  it("creates legacy run via POST /eval/runs", async () => {
    mockPost.mockResolvedValueOnce({
      data: {
        status: "completed",
        data: {},
        quality_comparable: false,
        deprecation: SAMPLE_DEPRECATION,
      },
    });
    const res = await evalApi.createRun({
      run_name: "r1",
      strategy: "bm25",
      novel_id: 1,
      dataset_ids: [1, 2],
    });
    expect(mockPost).toHaveBeenCalledWith("/eval/runs", {
      run_name: "r1",
      strategy: "bm25",
      novel_id: 1,
      dataset_ids: [1, 2],
    });
    expect(res.data.deprecation.deprecated).toBe(true);
    expect(res.data.quality_comparable).toBe(false);
  });

  it("gets legacy report with deprecation metadata", async () => {
    mockGet.mockResolvedValueOnce({
      data: {
        status: "ok",
        data: { run: { id: 1 }, results: [], error_cases: [] },
        quality_comparable: false,
        deprecation: SAMPLE_DEPRECATION,
      },
    });
    const res = await evalApi.getRun(9);
    expect(mockGet).toHaveBeenCalledWith("/eval/runs/9");
    expect(res.data.deprecation.replacement.create).toContain("quality/runs");
  });

  it("creates quality run via POST /eval/quality/runs", async () => {
    await evalApi.createQualityRun({
      snapshot: { snapshot_id: "s1" },
      cases: [{ case_id: "c1" }],
      run_immediately: true,
    });
    expect(mockPost).toHaveBeenCalledWith("/eval/quality/runs", {
      snapshot: { snapshot_id: "s1" },
      cases: [{ case_id: "c1" }],
      run_immediately: true,
    });
  });

  it("gets/resumes/cancels quality jobs", async () => {
    await evalApi.getQualityRun("job-1");
    expect(mockGet).toHaveBeenCalledWith("/eval/quality/runs/job-1");
    await evalApi.resumeQualityRun("job-1");
    expect(mockPost).toHaveBeenCalledWith("/eval/quality/runs/job-1/resume");
    await evalApi.cancelQualityRun("job-1");
    expect(mockPost).toHaveBeenCalledWith("/eval/quality/runs/job-1/cancel");
    await evalApi.listQualityRuns();
    expect(mockGet).toHaveBeenCalledWith("/eval/quality/runs");
  });
});

describe("quality status catalog (D-07)", () => {
  const requiredTerminal = [
    "passed",
    "qualified",
    "failed_policy",
    "quality_regression",
    "blocked_dependency",
    "invalid_fixture",
    "invalid_lineage",
    "quarantined",
  ];

  it("includes all required terminal statuses", () => {
    for (const s of requiredTerminal) {
      expect(QUALITY_TERMINAL_STATUSES).toContain(s);
      expect(QUALITY_STATUSES).toContain(s);
      expect(QUALITY_STATUS_LABELS[s]).toBeTruthy();
    }
  });

  it("only passed/qualified are quality-comparable", () => {
    expect(isQualityComparable("passed")).toBe(true);
    expect(isQualityComparable("qualified")).toBe(true);
    for (const s of [
      "failed_policy",
      "quality_regression",
      "blocked_dependency",
      "invalid_fixture",
      "invalid_lineage",
      "quarantined",
    ]) {
      expect(isQualityComparable(s)).toBe(false);
      expect(isQualityComparable(s, true)).toBe(false);
    }
    expect(isQualityComparable("passed", false)).toBe(false);
    expect(QUALITY_COMPARABLE_STATUSES.size).toBe(2);
  });

  it("maps tones for UI badges", () => {
    expect(qualityStatusTone("passed")).toBe("success");
    expect(qualityStatusTone("qualified")).toBe("success");
    expect(qualityStatusTone("quality_regression")).toBe("danger");
    expect(qualityStatusTone("failed_policy")).toBe("danger");
    expect(qualityStatusTone("blocked_dependency")).toBe("warning");
    expect(qualityStatusTone("invalid_fixture")).toBe("warning");
    expect(qualityStatusTone("invalid_lineage")).toBe("warning");
    expect(qualityStatusTone("quarantined")).toBe("warning");
    expect(qualityStatusTone("cancelled")).toBe("muted");
  });
});
