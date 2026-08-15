/**
 * Eval / Quality API (06-04 + 06-05)。
 */

import { api } from "./client";

/**
 * Terminal + intermediate quality job statuses (D-07 / 06-AI-SPEC).
 * Only `passed` / `qualified` are quality-comparable for baseline use.
 */
export const QUALITY_STATUSES = [
  "queued",
  "calibrating",
  "snapshotting",
  "fixture_generation",
  "fixture_review",
  "frozen",
  "retrieving",
  "answering",
  "scoring",
  "arbitrating",
  "passed",
  "qualified",
  "quality_regression",
  "failed_policy",
  "blocked_dependency",
  "invalid_fixture",
  "invalid_lineage",
  "quarantined",
  "cancelled",
] as const;

export type QualityStatus = (typeof QUALITY_STATUSES)[number];

/** Terminal statuses that may surface in UI badges and reports. */
export const QUALITY_TERMINAL_STATUSES = [
  "passed",
  "qualified",
  "failed_policy",
  "quality_regression",
  "blocked_dependency",
  "invalid_fixture",
  "invalid_lineage",
  "quarantined",
  "cancelled",
] as const;

export type QualityTerminalStatus = (typeof QUALITY_TERMINAL_STATUSES)[number];

export const QUALITY_COMPARABLE_STATUSES = new Set<QualityStatus>([
  "passed",
  "qualified",
]);

export interface DeprecationMeta {
  deprecated: boolean;
  legacy_eval_api: boolean;
  replacement: {
    create: string;
    status: string;
    report: string;
    resume: string;
    cancel: string;
  };
  migration: string;
  quality_comparable_default: boolean;
}

export interface EvalDataset {
  id: number;
  novel_id: number;
  question: string;
  question_type: string;
  difficulty: string;
  gold_chunks: number[];
  expected_points: string[];
  must_not_say: string[];
  status: string;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface EvalRun {
  id: number;
  run_name: string;
  strategy: string;
  novel_id: number;
  total_questions: number;
  recall_at_k: number;
  precision_at_k: number | null;
  mrr: number | null;
  ndcg_at_k: number | null;
  latency_ms: number | null;
  cost_usd: number | null;
  config_snapshot: Record<string, unknown>;
  created_at: string;
  // 06-04 compatibility fields (optional on list responses)
  job_id?: string | null;
  quality_comparable?: boolean;
  status?: string;
  deprecation?: DeprecationMeta;
}

export interface EvalReport {
  run: EvalRun;
  results: Array<{
    id: number;
    dataset_id: number;
    recalled_chunks: number[];
    score: number;
    metrics: Record<string, number>;
    is_error_case: boolean;
  }>;
  error_cases: Array<{
    id: number;
    dataset_id: number;
    recalled_chunks: number[];
    score: number;
    is_error_case: boolean;
  }>;
  job_id?: string | null;
  quality_comparable?: boolean;
  deprecation?: DeprecationMeta;
}

export interface LegacyEvalRunResponse {
  status: string;
  data: EvalReport | EvalRun;
  job_id?: string | null;
  quality_comparable: boolean;
  deprecation: DeprecationMeta;
}

export interface QualityMetrics {
  answer_faithfulness?: number | null;
  answer_relevance?: number | null;
  context_precision?: number | null;
  context_recall_at_5?: number | null;
  critical_unsupported_claim_rate?: number | null;
  faithfulness_lb95?: number | null;
  verdict_consistency?: number | null;
  cost_usd?: number | null;
  p95_latency_ms?: number | null;
  [key: string]: number | null | undefined;
}

export interface QualityJobPublic {
  job_id: string;
  status: QualityStatus | string;
  quality_comparable: boolean;
  metrics: QualityMetrics | null;
  owner_id?: number;
  stage?: string | null;
  error?: string | null;
  checkpoint?: Record<string, unknown> | null;
  created_at?: string;
  updated_at?: string;
  [key: string]: unknown;
}

export interface QualityRunResponse {
  status: QualityStatus | string;
  job_id: string;
  quality_comparable: boolean;
  metrics: QualityMetrics | null;
  data: QualityJobPublic;
  deprecation?: DeprecationMeta;
}

export interface QualityRunCreateBody {
  snapshot: Record<string, unknown>;
  cases: Array<Record<string, unknown>>;
  generator_lineage?: Record<string, unknown> | null;
  judge_lineage?: Record<string, unknown> | null;
  calibration_report?: Record<string, unknown> | null;
  baseline?: Record<string, unknown> | null;
  health?: Record<string, unknown> | null;
  run_immediately?: boolean;
}

export interface QualityRunFromNovelBody {
  novel_id: number;
  dataset_ids?: number[];
  run_immediately?: boolean;
}

/** Human-readable Chinese labels for quality statuses. */
export const QUALITY_STATUS_LABELS: Record<string, string> = {
  queued: "排队中",
  calibrating: "校准中",
  snapshotting: "快照中",
  fixture_generation: "生成 fixture",
  fixture_review: "审核 fixture",
  frozen: "已冻结",
  retrieving: "检索中",
  answering: "生成答案",
  scoring: "评分中",
  arbitrating: "仲裁中",
  passed: "通过",
  qualified: "合格",
  quality_regression: "质量回归",
  failed_policy: "策略失败",
  blocked_dependency: "依赖不可用",
  invalid_fixture: "无效 fixture",
  invalid_lineage: "谱系无效",
  quarantined: "隔离",
  cancelled: "已取消",
};

/** Whether a quality status may contribute comparable metrics. */
export function isQualityComparable(status: string, flag?: boolean): boolean {
  if (flag === false) return false;
  if (flag === true) return QUALITY_COMPARABLE_STATUSES.has(status as QualityStatus);
  return QUALITY_COMPARABLE_STATUSES.has(status as QualityStatus);
}

/** Badge tone for terminal quality statuses. */
export function qualityStatusTone(
  status: string
): "success" | "warning" | "danger" | "muted" | "info" {
  switch (status) {
    case "passed":
    case "qualified":
      return "success";
    case "quality_regression":
    case "failed_policy":
      return "danger";
    case "blocked_dependency":
    case "invalid_fixture":
    case "invalid_lineage":
    case "quarantined":
      return "warning";
    case "cancelled":
      return "muted";
    default:
      return "info";
  }
}

export const evalApi = {
  /** Legacy list datasets */
  listDatasets: (params?: {
    novel_id?: number;
    status?: string;
    question_type?: string;
  }) => {
    const search = new URLSearchParams();
    if (params?.novel_id != null) search.set("novel_id", String(params.novel_id));
    if (params?.status) search.set("status", params.status);
    if (params?.question_type) search.set("question_type", params.question_type);
    const qs = search.toString();
    return api.get<EvalDataset[]>(`/eval/datasets${qs ? `?${qs}` : ""}`);
  },

  updateDataset: (id: number, body: { status?: string; gold_chunks?: number[] }) =>
    api.patch<EvalDataset>(`/eval/datasets/${id}`, body),

  /** Legacy retrieval runs (returns deprecation metadata on create/report). */
  listRuns: (novelId?: number) => {
    const qs = novelId != null ? `?novel_id=${novelId}` : "";
    return api.get<EvalRun[]>(`/eval/runs${qs}`);
  },

  getRun: (runId: number) =>
    api.get<LegacyEvalRunResponse>(`/eval/runs/${runId}`),

  createRun: (body: {
    run_name: string;
    strategy: string;
    novel_id: number;
    dataset_ids: number[];
  }) => api.post<LegacyEvalRunResponse>("/eval/runs", body),

  /** Durable quality jobs (preferred path). */
  listQualityRuns: () => api.get<QualityJobPublic[]>("/eval/quality/runs"),

  getQualityRun: (jobId: string) =>
    api.get<QualityRunResponse>(`/eval/quality/runs/${jobId}`),

  createQualityRun: (body: QualityRunCreateBody) =>
    api.post<QualityRunResponse>("/eval/quality/runs", body),

  createQualityRunFromNovel: (body: QualityRunFromNovelBody) =>
    api.post<QualityRunResponse>("/eval/quality/runs/from-novel", body),

  resumeQualityRun: (jobId: string) =>
    api.post<QualityRunResponse>(`/eval/quality/runs/${jobId}/resume`),

  cancelQualityRun: (jobId: string) =>
    api.post<QualityRunResponse>(`/eval/quality/runs/${jobId}/cancel`),
};
