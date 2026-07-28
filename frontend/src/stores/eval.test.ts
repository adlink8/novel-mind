import { describe, it, expect, vi, beforeEach } from "vitest";

const {
  listDatasets,
  listRuns,
  listQualityRuns,
  getQualityRun,
  resumeQualityRun,
  cancelQualityRun,
  createQualityRunFromNovel,
} = vi.hoisted(() => ({
  listDatasets: vi.fn(),
  listRuns: vi.fn(),
  listQualityRuns: vi.fn(),
  getQualityRun: vi.fn(),
  resumeQualityRun: vi.fn(),
  cancelQualityRun: vi.fn(),
  createQualityRunFromNovel: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    evalApi: {
      listDatasets,
      listRuns,
      listQualityRuns,
      getQualityRun,
      resumeQualityRun,
      cancelQualityRun,
      updateDataset: vi.fn(),
      getRun: vi.fn(),
      createRun: vi.fn(),
      createQualityRun: vi.fn(),
      createQualityRunFromNovel,
    },
  };
});

import { useEvalStore } from "./eval";

const deprecation = {
  deprecated: true,
  legacy_eval_api: true,
  replacement: {
    create: "POST /api/eval/quality/runs",
    status: "GET /api/eval/quality/runs/{job_id}",
    report: "GET /api/eval/quality/runs/{job_id}",
    resume: "POST /api/eval/quality/runs/{job_id}/resume",
    cancel: "POST /api/eval/quality/runs/{job_id}/cancel",
  },
  migration: "migrate",
  quality_comparable_default: false,
};

describe("useEvalStore", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useEvalStore.setState({
      datasets: [],
      runs: [],
      qualityJobs: [],
      selectedJob: null,
      lastDeprecation: null,
      loading: false,
      error: null,
    });
  });

  it("fetchAll loads datasets, runs, and quality jobs", async () => {
    listDatasets.mockResolvedValue({ data: [{ id: 1, status: "confirmed" }] });
    listRuns.mockResolvedValue({ data: [{ id: 2, run_name: "r" }] });
    listQualityRuns.mockResolvedValue({
      data: [
        {
          job_id: "j1",
          status: "passed",
          quality_comparable: true,
          metrics: { answer_faithfulness: 0.95 },
        },
        {
          job_id: "j2",
          status: "blocked_dependency",
          quality_comparable: false,
          metrics: { answer_faithfulness: 0 },
        },
      ],
    });

    await useEvalStore.getState().fetchAll();
    const state = useEvalStore.getState();
    expect(state.datasets).toHaveLength(1);
    expect(state.runs).toHaveLength(1);
    expect(state.qualityJobs).toHaveLength(2);
    // blocked_dependency must null out metrics
    const blocked = state.qualityJobs.find((j) => j.job_id === "j2");
    expect(blocked?.metrics).toBeNull();
    expect(blocked?.quality_comparable).toBe(false);
    const passed = state.qualityJobs.find((j) => j.job_id === "j1");
    expect(passed?.metrics?.answer_faithfulness).toBe(0.95);
  });

  it("resumeQualityJob updates store and keeps deprecation", async () => {
    resumeQualityRun.mockResolvedValue({
      data: {
        status: "qualified",
        job_id: "j9",
        quality_comparable: true,
        metrics: { context_recall_at_5: 0.9 },
        data: {
          job_id: "j9",
          status: "qualified",
          quality_comparable: true,
          metrics: { context_recall_at_5: 0.9 },
        },
        deprecation,
      },
    });

    const res = await useEvalStore.getState().resumeQualityJob("j9");
    expect(res?.status).toBe("qualified");
    const state = useEvalStore.getState();
    expect(state.qualityJobs[0].job_id).toBe("j9");
    expect(state.lastDeprecation?.deprecated).toBe(true);
  });

  it("creates a server-orchestrated quality run from a novel", async () => {
    createQualityRunFromNovel.mockResolvedValue({
      data: {
        status: "queued",
        job_id: "from-novel-1",
        quality_comparable: false,
        metrics: null,
        data: {
          job_id: "from-novel-1",
          status: "queued",
          quality_comparable: false,
          metrics: null,
        },
      },
    });

    const response = await useEvalStore
      .getState()
      .createQualityRunFromNovel(42, [3, 4]);

    expect(createQualityRunFromNovel).toHaveBeenCalledWith({
      novel_id: 42,
      dataset_ids: [3, 4],
      run_immediately: true,
    });
    expect(response?.job_id).toBe("from-novel-1");
    expect(useEvalStore.getState().selectedJob?.job_id).toBe("from-novel-1");
  });

  it("cancelQualityJob marks non-comparable", async () => {
    cancelQualityRun.mockResolvedValue({
      data: {
        status: "cancelled",
        job_id: "j3",
        quality_comparable: false,
        metrics: null,
        data: {
          job_id: "j3",
          status: "cancelled",
          quality_comparable: false,
          metrics: { answer_faithfulness: 0.1 },
        },
        deprecation,
      },
    });
    await useEvalStore.getState().cancelQualityJob("j3");
    const job = useEvalStore.getState().qualityJobs[0];
    expect(job.status).toBe("cancelled");
    expect(job.metrics).toBeNull();
  });

  it("selectQualityJob handles failed_policy / invalid_lineage", async () => {
    for (const status of [
      "failed_policy",
      "quality_regression",
      "invalid_fixture",
      "invalid_lineage",
      "quarantined",
    ] as const) {
      getQualityRun.mockResolvedValueOnce({
        data: {
          status,
          job_id: `job-${status}`,
          quality_comparable: false,
          metrics: null,
          data: {
            job_id: `job-${status}`,
            status,
            quality_comparable: false,
            metrics: { answer_faithfulness: 0 },
          },
          deprecation,
        },
      });
      await useEvalStore.getState().selectQualityJob(`job-${status}`);
      const selected = useEvalStore.getState().selectedJob;
      expect(selected?.status).toBe(status);
      expect(selected?.metrics).toBeNull();
    }
  });

  it("records error on fetch failure", async () => {
    listDatasets.mockRejectedValue(new Error("network"));
    listRuns.mockResolvedValue({ data: [] });
    listQualityRuns.mockResolvedValue({ data: [] });
    await useEvalStore.getState().fetchAll();
    expect(useEvalStore.getState().error).toMatch(/network/);
  });
});
