/**
 * Eval / quality Hook (06-05).
 *
 * Wraps useEvalStore with auto-fetch and helpers for status display.
 */

"use client";

import { useEffect, useMemo } from "react";
import { useEvalStore } from "@/stores/eval";
import {
  QUALITY_STATUS_LABELS,
  QUALITY_TERMINAL_STATUSES,
  qualityStatusTone,
  type QualityJobPublic,
  type QualityTerminalStatus,
} from "@/lib/api";

export function useEval(options?: { autoFetch?: boolean }) {
  const autoFetch = options?.autoFetch !== false;
  const {
    datasets,
    runs,
    qualityJobs,
    selectedJob,
    lastDeprecation,
    loading,
    error,
    fetchAll,
    fetchDatasets,
    fetchRuns,
    fetchQualityJobs,
    selectQualityJob,
    resumeQualityJob,
    cancelQualityJob,
    clearError,
  } = useEvalStore();

  useEffect(() => {
    if (autoFetch) {
      void fetchAll();
    }
  }, [autoFetch, fetchAll]);

  const terminalJobs = useMemo(
    () =>
      qualityJobs.filter((j) =>
        (QUALITY_TERMINAL_STATUSES as readonly string[]).includes(String(j.status))
      ),
    [qualityJobs]
  );

  const comparableJobs = useMemo(
    () => qualityJobs.filter((j) => j.quality_comparable),
    [qualityJobs]
  );

  const statusLabel = (status: string) =>
    QUALITY_STATUS_LABELS[status] ?? status;

  const statusTone = (status: string) => qualityStatusTone(status);

  const isTerminal = (status: string): status is QualityTerminalStatus =>
    (QUALITY_TERMINAL_STATUSES as readonly string[]).includes(status);

  const describeJob = (job: QualityJobPublic) => ({
    id: job.job_id,
    status: String(job.status),
    label: statusLabel(String(job.status)),
    tone: statusTone(String(job.status)),
    comparable: Boolean(job.quality_comparable),
    metrics: job.quality_comparable ? job.metrics : null,
    error: job.error ?? null,
  });

  return {
    datasets,
    runs,
    qualityJobs,
    terminalJobs,
    comparableJobs,
    selectedJob,
    lastDeprecation,
    loading,
    error,
    fetchAll,
    fetchDatasets,
    fetchRuns,
    fetchQualityJobs,
    selectQualityJob,
    resumeQualityJob,
    cancelQualityJob,
    clearError,
    statusLabel,
    statusTone,
    isTerminal,
    describeJob,
  };
}
