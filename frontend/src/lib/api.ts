/**
 * API 客户端模块
 *
 * 基于 Axios 封装所有后端 API 调用，按业务域组织:
 * - novelsApi     : 小说 CRUD（列表、详情、上传、删除、章节）
 * - analysisApi   : 剧情分析
 * - timelineApi   : 时间线事件
 * - charactersApi : 人物关系
 * - fanfictionApi : 同人文
 * - aiModelsApi   : AI 模型配置
 *
 * 基础配置:
 * - baseURL: 通过 NEXT_PUBLIC_API_URL 环境变量配置，默认 "/api"（走 Next.js rewrite 代理）
 * - timeout: 30 秒
 * - Content-Type: application/json（上传时自动切换为 multipart/form-data）
 *
 * 每个 API 模块导出一组函数，返回 AxiosPromise，组件中通过 .data 获取响应体。
 */

import axios from "axios";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";
const AUTH_TOKEN_KEY = "novelmind_access_token";

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
});

/** Persist JWT so API calls can use Bearer (avoids cookie CSRF Origin mismatches). */
export function setAccessToken(token: string | null) {
  if (typeof window === "undefined") return;
  if (token) {
    window.sessionStorage.setItem(AUTH_TOKEN_KEY, token);
  } else {
    window.sessionStorage.removeItem(AUTH_TOKEN_KEY);
  }
}

export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.sessionStorage.getItem(AUTH_TOKEN_KEY);
}

api.interceptors.request.use((config) => {
  const token = getAccessToken();
  if (token) {
    config.headers = config.headers ?? {};
    config.headers.Authorization = `Bearer ${token}`;
  }
  // Let the browser set multipart boundary; a fixed Content-Type breaks uploads.
  if (typeof FormData !== "undefined" && config.data instanceof FormData) {
    if (config.headers && "Content-Type" in config.headers) {
      delete (config.headers as Record<string, unknown>)["Content-Type"];
    }
  }
  return config;
});

export interface AuthUser {
  id: number;
  username: string;
  email: string;
  is_active: boolean;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user_id: number;
  username: string;
}

export const authApi = {
  me: () => api.get<AuthUser>("/auth/me"),
  login: async (username: string, password: string) => {
    const res = await api.post<LoginResponse>("/auth/login", { username, password });
    setAccessToken(res.data.access_token);
    return res;
  },
  register: (username: string, email: string, password: string) =>
    api.post<AuthUser>("/auth/register", { username, email, password }),
  logout: async () => {
    try {
      await api.post("/auth/logout");
    } finally {
      setAccessToken(null);
    }
  },
};

// ==================== 小说 API ====================

/** 小说基础信息（列表展示用） */
export interface Novel {
  id: number;
  title: string;
  author: string | null;
  description: string | null;
  genre: string | null;
  word_count: number;
  chapter_count: number;
  /** importing | ready | chunking | embedding | analyzing | analyzed */
  status: string;
  /** 检索分块数量；0 表示尚未建索引 */
  chunk_count?: number;
  reading_progress?: {
    chapter_id?: number;
    progress_percent?: number;
  } | null;
  created_at: string;
  updated_at: string;
}

/** 章节信息 */
export interface Chapter {
  id: number;
  novel_id: number;
  chapter_number: number;
  title: string;
  content: string;       // 章节完整正文内容
  summary?: string;
  word_count: number;
  created_at: string;
  updated_at: string;
}

/** 小说列表分页响应 */
export interface NovelListResponse {
  items: Novel[];
  total: number;
  skip: number;
  limit: number;
}

/** 小说上传响应 */
export interface NovelUploadResponse {
  /** 兼容字段：实际为 job_id，用于轮询导入进度 */
  id: number;
  job_id: number;
  novel_id: number | null;
  title: string;
  status: string;
  message: string;
  chapter_count: number;
  word_count: number;
}

/** 导入进度状态 */
export interface ImportStatus {
  job_id?: number | null;
  novel_id?: number | null;
  stage: string;       // uploading / detecting / parsing / saving / ready / failed / error
  percent: number;     // 0-100
  message: string;
}

export const novelsApi = {
  list: () => api.get<NovelListResponse>("/novels"),
  get: (id: string) => api.get<Novel>(`/novels/${id}`),
  upload: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    // Do not set Content-Type manually — axios/browser must add multipart boundary.
    // Large novels can take minutes to parse; extend timeout beyond default 30s.
    return api.post<NovelUploadResponse>("/novels/upload", formData, {
      timeout: 10 * 60 * 1000,
    });
  },
  delete: (id: string) => api.delete(`/novels/${id}`),
  getChapters: (id: string) => api.get<Chapter[]>(`/novels/${id}/chapters`),
  getChapter: (novelId: string, chapterId: string) =>
    api.get<Chapter>(`/novels/${novelId}/chapters/${chapterId}`),
  updateProgress: (novelId: string, chapterId: number, progressPercent: number) =>
    api.patch(`/novels/${novelId}/progress`, { chapter_id: chapterId, progress_percent: progressPercent }),
  /** @deprecated 上传后应使用 getImportJobStatus(job_id) */
  getImportStatus: (novelId: string) => api.get<ImportStatus>(`/novels/${novelId}/import-status`),
  getImportJobStatus: (jobId: string | number) =>
    api.get<ImportStatus>(`/novels/import-jobs/${jobId}`),
};

// ==================== 分析 API ====================

/** 分析结果结构 */
export interface AnalysisResult {
  summary: string;
  characters: Array<{ name: string; role: string; description: string }>;
  key_events: Array<{ title: string; description: string; chapter: number }>;
  themes: string[];
}

export interface AnalysisRunResult {
  id: number;
  novel_id: number;
  chapter_id?: number | null;
  analysis_type: string;
  result_data: Record<string, unknown>;
  model_used?: string | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  created_at?: string | null;
  status?: string;
}

export interface HierarchyStatus {
  novel_id: number;
  active_build_id: string | null;
  ready: boolean;
  scene_count: number;
  chapter_count: number;
  evidence_count: number;
  sample_scenes?: Array<{
    scene_id: string;
    chapter_number: number;
    preview: string;
    char_count: number;
  }>;
}

export const analysisApi = {
  analyze: (
    novelId: string,
    body?: {
      analysis_type?: string;
      chapter_id?: number;
      use_llm?: boolean;
      rebuild_hierarchy?: boolean;
      model?: string;
    }
  ) => api.post<AnalysisRunResult>(`/analysis/${novelId}/analyze`, body ?? {}),
  getAnalysis: (novelId: string, analysisType?: string) =>
    api.get<{
      novel_id: number;
      status: string;
      items: AnalysisRunResult[];
      latest?: AnalysisRunResult;
      supported_types?: string[];
    }>(`/analysis/${novelId}`, {
      params: analysisType ? { analysis_type: analysisType } : undefined,
    }),
  analyzeChapter: (
    novelId: string,
    chapterId: string,
    body?: { analysis_type?: string; use_llm?: boolean }
  ) =>
    api.post<AnalysisRunResult>(
      `/analysis/${novelId}/chapters/${chapterId}/analyze`,
      body ?? { analysis_type: "chapter_summary" }
    ),
  hierarchy: (novelId: string) =>
    api.get<HierarchyStatus>(`/analysis/${novelId}/hierarchy`),
  rebuildHierarchy: (novelId: string) =>
    api.post<HierarchyStatus & { build_id?: string }>(
      `/analysis/${novelId}/hierarchy/rebuild`
    ),
};

// ==================== 时间线 API ====================

export type TimelineVersionSource = "active" | "running_candidate";
export type TimelineOrdering = "narrative" | "story";

export interface TimelineParticipant {
  mention: string;
  entity_id?: number | null;
}

export interface TimelineEvent {
  id: number;
  logical_event_id: string;
  title: string;
  description: string;
  event_type: string;
  narrative_chapter_number: number;
  source_start: number;
  narrative_index: number;
  story_rank?: number | null;
  time_precision: "exact" | "relative" | "fuzzy" | "unknown";
  time_expression?: string | null;
  confidence: number;
  participants: TimelineParticipant[];
  provenance: Record<string, "machine" | "manual">;
}

export interface TimelineCausalEdge {
  source_event_id: number;
  target_event_id: number;
  edge_type: string;
  confidence: number;
}

export interface TimelineVersionView {
  source: TimelineVersionSource;
  version_id: number;
  status: string;
  progress: Record<string, unknown>;
  events: TimelineEvent[];
  causal_edges: TimelineCausalEdge[];
  counts: { events: number; participants: number; causal_edges: number };
  aggregates: Record<string, number>;
  previews: string[];
}

export interface TimelineEnvelope {
  active: TimelineVersionView | null;
  running_candidate: TimelineVersionView | null;
}

export interface TimelineRun {
  id: number;
  novel_id: number;
  version_id?: number | null;
  status: string;
  status_reason?: string | null;
  progress: Record<string, unknown>;
  cancel_requested: boolean;
  updated_at?: string | null;
}

export interface TimelineQuery {
  ordering?: TimelineOrdering;
  person?: string;
  causal?: boolean;
  full_book?: boolean;
}

export const timelineApi = {
  startOrResume: (novelId: string) => api.post<TimelineRun>(`/timeline/${novelId}/start-or-resume`),
  status: (novelId: string) => api.get<TimelineRun>(`/timeline/${novelId}/status`),
  cancel: (novelId: string) => api.post<TimelineRun>(`/timeline/${novelId}/cancel`),
  resume: (novelId: string) => api.post<TimelineRun>(`/timeline/${novelId}/resume`),
  getTimeline: (novelId: string, params?: TimelineQuery) =>
    api.get<TimelineEnvelope>(`/timeline/${novelId}`, { params }),
  getVersion: (novelId: string, versionId: number, params?: TimelineQuery) =>
    api.get<TimelineVersionView>(`/timeline/${novelId}/versions/${versionId}`, { params }),
  rollback: (novelId: string, targetVersionId: number, expectedRevision: number) =>
    api.post(`/timeline/${novelId}/rollback`, {
      target_version_id: targetVersionId,
      expected_revision: expectedRevision,
    }),
  updateEvent: (novelId: string, logicalEventId: string, fieldName: "title" | "description" | "event_type" | "time_expression", value: unknown) =>
    api.put(`/timeline/${novelId}/events/${logicalEventId}`, { field_name: fieldName, value }),
  setFullBookPreference: (novelId: string, fullBook: boolean) =>
    api.put(`/timeline/${novelId}/preference`, { full_book: fullBook }),
  /** @deprecated Use startOrResume. */
  extractTimeline: (novelId: string) => api.post<TimelineRun>(`/timeline/${novelId}/extract`),
  /** @deprecated Phase 08 edits require novel scope; retained for legacy callers only. */
  deleteEvent: (eventId: string) => api.delete(`/timeline/events/${eventId}`),
};

// ==================== 人物 API ====================

export interface Character {
  id: string;
  novel_id: string;
  name: string;
  aliases: string[];
  description: string;
  personality: Record<string, string>;
  role: "protagonist" | "antagonist" | "supporting" | "minor";
  first_appearance: number;
  stats: Record<string, number>;
}

export interface CharacterRelation {
  id: string;
  source_character_id: string;
  target_character_id: string;
  relation_type: string;
  description: string;
  strength: number;
}

export const charactersApi = {
  getCharacters: (novelId: string) => api.get<Character[]>(`/characters/${novelId}`),
  getRelations: (novelId: string) => api.get<CharacterRelation[]>(`/characters/${novelId}/relations`),
  extractCharacters: (novelId: string) => api.post(`/characters/${novelId}/extract`),
};

// ==================== 同人文 API ====================

export interface FanFiction {
  id: string;
  novel_id: string;
  title: string;
  description: string;
  style_config: Record<string, unknown>;
  branch_point: Record<string, unknown>;
  status: "draft" | "writing" | "completed";
}

export const fanfictionApi = {
  list: (novelId: string) => api.get<FanFiction[]>(`/fanfiction/${novelId}`),
  create: (data: Partial<FanFiction>) => api.post<FanFiction>("/fanfiction", data),
  continueWriting: (fanfictionId: string, prompt: string) =>
    api.post(`/fanfiction/${fanfictionId}/continue`, { prompt }),
};

// ==================== AI 模型 API ====================

export interface AIModelConfig {
  id: number;
  name: string;
  provider: "openai" | "anthropic" | "ollama" | "custom";
  model_id: string;
  base_url?: string;
  tier: "quality" | "balanced" | "budget";
  max_tokens: number;
  temperature: number;
  is_default: boolean;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface AIModelConfigCreate {
  name: string;
  provider: AIModelConfig["provider"];
  model_id: string;
  api_key?: string;
  base_url?: string;
  tier?: AIModelConfig["tier"];
  max_tokens?: number;
  temperature?: number;
  is_default?: boolean;
}

export interface AIModelTestResponse {
  success: boolean;
  model_name: string;
  latency_ms: number;
  response_text?: string;
  error?: string;
}

export const aiModelsApi = {
  list: () => api.get<AIModelConfig[]>("/models"),
  create: (data: AIModelConfigCreate) => api.post<AIModelConfig>("/models", data),
  test: (id: number) => api.post<AIModelTestResponse>(`/models/${id}/test`),
  setDefault: (id: number) => api.post(`/models/${id}/default`),
  delete: (id: number) => api.delete(`/models/${id}`),
};

// ==================== 搜索 API ====================

/** 单条搜索结果 */
export interface SearchResult {
  novel_id: number;
  novel_title: string | null;
  chapter_id: number | null;
  chapter_title: string | null;
  chunk_id: number;
  chunk_index: number;
  content_snippet: string;
  score: number;
}

/** 搜索响应 */
export interface SearchResponse {
  results: SearchResult[];
  total: number;
  query: string;
}

export const searchApi = {
  /** 全局搜索（跨所有小说） */
  global: (query: string, topK: number = 10) =>
    api.post<SearchResponse>("/search", { query, top_k: topK }),

  /** 指定小说内搜索 */
  inNovel: (novelId: number, query: string, topK: number = 10) =>
    api.post<SearchResponse>(`/search/novels/${novelId}`, { query, top_k: topK }),
};

// ==================== Eval / Quality API (06-04 + 06-05) ====================

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

  resumeQualityRun: (jobId: string) =>
    api.post<QualityRunResponse>(`/eval/quality/runs/${jobId}/resume`),

  cancelQualityRun: (jobId: string) =>
    api.post<QualityRunResponse>(`/eval/quality/runs/${jobId}/cancel`),
};
