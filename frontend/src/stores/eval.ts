/**
 * Eval / quality job store (06-05).
 *
 * Holds legacy runs + durable quality jobs and exposes load/resume/cancel.
 * Metrics are only retained when quality_comparable is true.
 */

import { create } from "zustand";
import {
  evalApi,
  type DeprecationMeta,
  type EvalDataset,
  type EvalRun,
  type QualityJobPublic,
  type QualityMetrics,
  type QualityRunResponse,
  isQualityComparable,
} from "@/lib/api";

export interface EvalState {
  datasets: EvalDataset[];
  runs: EvalRun[];
  qualityJobs: QualityJobPublic[];
  selectedJob: QualityJobPublic | null;
  lastDeprecation: DeprecationMeta | null;
  loading: boolean;
  error: string | null;

  fetchAll: () => Promise<void>;
  fetchDatasets: () => Promise<void>;
  fetchRuns: () => Promise<void>;
  fetchQualityJobs: () => Promise<void>;
  selectQualityJob: (jobId: string) => Promise<void>;
  resumeQualityJob: (jobId: string) => Promise<QualityRunResponse | null>;
  cancelQualityJob: (jobId: string) => Promise<QualityRunResponse | null>;
  clearError: () => void;
  /** Normalize a quality response: null metrics when not comparable. */
  applyQualityResponse: (res: QualityRunResponse) => void;
}

function normalizeJob(job: QualityJobPublic): QualityJobPublic {
  const comparable = isQualityComparable(String(job.status), job.quality_comparable);
  return {
    ...job,
    quality_comparable: comparable,
    metrics: comparable ? (job.metrics as QualityMetrics | null) : null,
  };
}

export const useEvalStore = create<EvalState>((set, get) => ({
  datasets: [],
  runs: [],
  qualityJobs: [],
  selectedJob: null,
  lastDeprecation: null,
  loading: false,
  error: null,

  clearError: () => set({ error: null }),

  applyQualityResponse: (res) => {
    const job = normalizeJob(res.data ?? {
      job_id: res.job_id,
      status: res.status,
      quality_comparable: res.quality_comparable,
      metrics: res.metrics,
    });
    set((state) => {
      const others = state.qualityJobs.filter((j) => j.job_id !== job.job_id);
      return {
        qualityJobs: [job, ...others],
        selectedJob:
          state.selectedJob?.job_id === job.job_id ? job : state.selectedJob,
        lastDeprecation: res.deprecation ?? state.lastDeprecation,
      };
    });
  },

  fetchDatasets: async () => {
    try {
      const res = await evalApi.listDatasets();
      set({ datasets: res.data });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to fetch datasets";
      set({ error: message });
    }
  },

  fetchRuns: async () => {
    try {
      const res = await evalApi.listRuns();
      set({ runs: res.data });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to fetch runs";
      set({ error: message });
    }
  },

  fetchQualityJobs: async () => {
    try {
      const res = await evalApi.listQualityRuns();
      set({ qualityJobs: res.data.map(normalizeJob) });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to fetch quality jobs";
      set({ error: message });
    }
  },

  fetchAll: async () => {
    set({ loading: true, error: null });
    try {
      await Promise.all([
        get().fetchDatasets(),
        get().fetchRuns(),
        get().fetchQualityJobs(),
      ]);
    } finally {
      set({ loading: false });
    }
  },

  selectQualityJob: async (jobId) => {
    try {
      const res = await evalApi.getQualityRun(jobId);
      get().applyQualityResponse(res.data);
      set({ selectedJob: normalizeJob(res.data.data) });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to load quality job";
      set({ error: message, selectedJob: null });
    }
  },

  resumeQualityJob: async (jobId) => {
    try {
      const res = await evalApi.resumeQualityRun(jobId);
      get().applyQualityResponse(res.data);
      return res.data;
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to resume quality job";
      set({ error: message });
      return null;
    }
  },

  cancelQualityJob: async (jobId) => {
    try {
      const res = await evalApi.cancelQualityRun(jobId);
      get().applyQualityResponse(res.data);
      return res.data;
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to cancel quality job";
      set({ error: message });
      return null;
    }
  },
}));
