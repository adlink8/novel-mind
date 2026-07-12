import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";

const fetchAll = vi.fn().mockResolvedValue(undefined);
const resumeQualityJob = vi.fn();
const selectQualityJob = vi.fn();
const cancelQualityJob = vi.fn();

vi.mock("@/stores/eval", () => ({
  useEvalStore: () => ({
    datasets: [{ id: 1, status: "confirmed" }],
    runs: [{ id: 10, run_name: "legacy" }],
    qualityJobs: [
      {
        job_id: "p1",
        status: "passed",
        quality_comparable: true,
        metrics: { answer_faithfulness: 0.99 },
      },
      {
        job_id: "b1",
        status: "blocked_dependency",
        quality_comparable: false,
        metrics: null,
      },
      {
        job_id: "q1",
        status: "queued",
        quality_comparable: false,
        metrics: null,
      },
    ],
    selectedJob: null,
    lastDeprecation: {
      deprecated: true,
      legacy_eval_api: true,
      replacement: {
        create: "POST /api/eval/quality/runs",
        status: "GET /api/eval/quality/runs/{job_id}",
        report: "GET /api/eval/quality/runs/{job_id}",
        resume: "POST /api/eval/quality/runs/{job_id}/resume",
        cancel: "POST /api/eval/quality/runs/{job_id}/cancel",
      },
      migration: "m",
      quality_comparable_default: false,
    },
    loading: false,
    error: null,
    fetchAll,
    fetchDatasets: vi.fn(),
    fetchRuns: vi.fn(),
    fetchQualityJobs: vi.fn(),
    selectQualityJob,
    resumeQualityJob,
    cancelQualityJob,
    clearError: vi.fn(),
  }),
}));

import { useEval } from "./use-eval";

describe("useEval", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("auto-fetches on mount by default", async () => {
    renderHook(() => useEval());
    await waitFor(() => expect(fetchAll).toHaveBeenCalled());
  });

  it("skips auto-fetch when disabled", () => {
    renderHook(() => useEval({ autoFetch: false }));
    expect(fetchAll).not.toHaveBeenCalled();
  });

  it("exposes terminal vs comparable job partitions", () => {
    const { result } = renderHook(() => useEval({ autoFetch: false }));
    expect(result.current.terminalJobs.map((j) => j.job_id)).toEqual(
      expect.arrayContaining(["p1", "b1"])
    );
    expect(result.current.comparableJobs).toHaveLength(1);
    expect(result.current.comparableJobs[0].job_id).toBe("p1");
  });

  it("labels and tones all required statuses", () => {
    const { result } = renderHook(() => useEval({ autoFetch: false }));
    const required = [
      "passed",
      "qualified",
      "failed_policy",
      "quality_regression",
      "blocked_dependency",
      "invalid_fixture",
      "invalid_lineage",
      "quarantined",
    ];
    for (const s of required) {
      expect(result.current.statusLabel(s)).toBeTruthy();
      expect(result.current.statusTone(s)).toBeTruthy();
      expect(result.current.isTerminal(s)).toBe(true);
    }
    expect(result.current.isTerminal("queued")).toBe(false);
  });

  it("describeJob nulls metrics when not comparable", () => {
    const { result } = renderHook(() => useEval({ autoFetch: false }));
    const blocked = result.current.qualityJobs.find((j) => j.job_id === "b1")!;
    const desc = result.current.describeJob(blocked);
    expect(desc.metrics).toBeNull();
    expect(desc.label).toBe("依赖不可用");
    expect(desc.tone).toBe("warning");
  });

  it("forwards resume/select actions", async () => {
    const { result } = renderHook(() => useEval({ autoFetch: false }));
    await act(async () => {
      await result.current.resumeQualityJob("p1");
      await result.current.selectQualityJob("p1");
    });
    expect(resumeQualityJob).toHaveBeenCalledWith("p1");
    expect(selectQualityJob).toHaveBeenCalledWith("p1");
  });
});
