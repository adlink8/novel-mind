/**
 * 阅读器选区对话 API（Phase 10）。
 */

import { api } from "./client";

export type ConversationStatus = "active" | "archived";
export type MessageRole = "user" | "assistant";

export type GenerationJobStatus =
  | "queued"
  | "running"
  | "paused_budget"
  | "paused_dependency"
  | "cancelled"
  | "completed"
  | "failed"
  | "failed_validation";

export interface SelectionCoordinate {
  chapter_id: number;
  source_start: number;
  source_end: number;
  selection_text: string;
  selection_text_hash: string;
  chapter_content_hash: string;
}

export interface SelectionSummary {
  chapter_id: number;
  source_start: number;
  source_end: number;
  selection_text_hash: string;
  chapter_content_hash: string;
}

export interface CitationView {
  block_id: string;
  evidence_key: string;
  context_evidence_ref_id: number;
  chapter_id: number;
  source_start: number;
  source_end: number;
}

export interface GenerationJobView {
  id: number;
  user_message_id: number;
  status: GenerationJobStatus;
  status_reason: string | null;
  cancel_requested: boolean;
  retry_count: number;
  error_code: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConversationListItem {
  id: number;
  novel_id: number;
  title: string;
  status: ConversationStatus;
  next_sequence: number;
  last_opened_at: string | null;
  created_at: string;
  updated_at: string;
  last_message_sequence: number | null;
  last_message_role: MessageRole | null;
  last_message_at: string | null;
}

export type ConversationDetail = ConversationListItem;

/** 25.1-01：结构区间锚点回显（服务端已按剧透边界收窄后的实际区间）。 */
export interface ChapterRangeAnchor {
  kind: "chapter_range";
  chapter_start: number;
  chapter_end: number;
}

/** 26-04：共享 QueryPlan trace/citation 暴露（Reader/Analysis Chat 同一核心）。 */
export interface QueryPlanTraceView {
  trace_id: string;
  plan_hash: string;
  intent: "reader" | "analysis";
  anchor_kind: "selection" | "chapter_range" | null;
  cutoff_mode: string;
  through_chapter: number;
  full_book_authorized: boolean;
  availability: Array<Record<string, string>>;
  fallback: Record<string, unknown>;
  manifest_checksum: string;
  allowed_evidence_ids: string[];
  citation_jump: Array<{
    evidence_key: string;
    chapter_id: number;
    chapter_number: number;
    source_start: number;
    source_end: number;
    excerpt: string;
  }>;
  abstained: boolean;
  /** 27-04：world projection authority/disclosure/evidence 契约（可缺省）。 */
  world_projection?: WorldProjectionView | null;
}

/**
 * 27-04：World projection 序列化契约（后端 queryplan/contracts.py 镜像）。
 * authority 严格四标签之一；candidate-only 经 approved 标识；user
 * interpretation 隔离在 overrides 中（D-06）。
 */
export type WorldAuthorityLabel =
  | "canon_fact"
  | "probable_inference"
  | "literary_interpretation"
  | "user_interpretation";

export interface WorldProjectionItemView {
  claim_key: string;
  kind: "character" | "world";
  subject: string;
  aspect: string;
  proposition: string;
  authority: WorldAuthorityLabel;
  known_at: number;
  disclosure_cutoff: number;
  pov: string | null;
  gate_status: "pending" | "passed" | "rejected";
  approved: boolean;
  is_override: boolean;
  evidence_key: string;
  chapter_id: number;
  chapter_number: number;
  source_start: number;
  source_end: number;
  content_hash: string;
  source_snapshot_hash: string;
  lineage: string[];
}

export interface WorldProjectionView {
  schema_version: string;
  available: boolean;
  status: "available" | "candidate_only" | "unavailable";
  cutoff: number;
  items: WorldProjectionItemView[];
  overrides: WorldProjectionItemView[];
  authorities: WorldAuthorityLabel[];
  manifest_checksum?: string | null;
  snapshot_hash?: string | null;
}

export interface MessageView {
  id: number;
  conversation_id: number;
  sequence: number;
  role: MessageRole;
  body: string;
  client_message_id: string | null;
  reply_to_message_id: number | null;
  selection: SelectionSummary | null;
  anchor?: ChapterRangeAnchor | null;
  citations: CitationView[];
  generation_job: GenerationJobView | null;
  queryplan?: QueryPlanTraceView | null;
  backfill_runs?: BackfillRunView[];
  created_at: string;
}

export interface BackfillRunView {
  run_id: number;
  skill_name: string;
  status: "queued" | "running" | "cancelled" | "completed" | "failed";
  backfill_dimension?: string | null;
}

export interface MessageAccepted {
  message: MessageView;
  job: GenerationJobView;
}

export interface ConversationListResponse {
  items: ConversationListItem[];
  total: number;
  skip: number;
  limit: number;
}

export interface MessageListResponse {
  items: MessageView[];
  total: number;
  skip: number;
  limit: number;
  after_sequence: number;
}

export interface MessageCreateBody {
  client_message_id: string;
  body: string;
  chapter_id?: number;
  selection?: SelectionCoordinate;
  /** 与 chapter_id/selection 互斥；章号语义，服务端按剧透边界收窄 chapter_end。 */
  chapter_range?: { chapter_start: number; chapter_end: number };
}

const TERMINAL_JOB_STATUSES: ReadonlySet<GenerationJobStatus> = new Set([
  "completed",
  "cancelled",
  "failed",
  "failed_validation",
  "paused_budget",
  "paused_dependency",
]);

export function isTerminalJobStatus(status: string): boolean {
  return TERMINAL_JOB_STATUSES.has(status as GenerationJobStatus);
}

/**
 * Poll a generation job until terminal or timeout.
 * Never fabricates assistant content — only returns server job views.
 */
export async function pollReaderChatJob(
  novelId: string | number,
  conversationId: number,
  jobId: number,
  options?: {
    intervalMs?: number;
    timeoutMs?: number;
    signal?: AbortSignal;
    onUpdate?: (job: GenerationJobView) => void;
  }
): Promise<GenerationJobView> {
  const intervalMs = options?.intervalMs ?? 800;
  const timeoutMs = options?.timeoutMs ?? 120_000;
  const started = Date.now();
  let last: GenerationJobView | null = null;
  while (Date.now() - started < timeoutMs) {
    if (options?.signal?.aborted) {
      throw new DOMException("Aborted", "AbortError");
    }
    const res = await readerChatApi.getJob(novelId, conversationId, jobId);
    last = res.data;
    options?.onUpdate?.(last);
    if (isTerminalJobStatus(last.status)) return last;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  if (last) return last;
  throw new Error("job poll timeout with no response");
}

export const readerChatApi = {
  listConversations: (
    novelId: string | number,
    params?: { status?: ConversationStatus; skip?: number; limit?: number }
  ) =>
    api.get<ConversationListResponse>(`/novels/${novelId}/conversations`, {
      params: {
        status: params?.status,
        skip: params?.skip,
        limit: params?.limit,
      },
    }),

  createConversation: (novelId: string | number, title = "New chat") =>
    api.post<ConversationDetail>(`/novels/${novelId}/conversations`, { title }),

  getConversation: (novelId: string | number, conversationId: number) =>
    api.get<ConversationDetail>(
      `/novels/${novelId}/conversations/${conversationId}`
    ),

  patchConversation: (
    novelId: string | number,
    conversationId: number,
    body: { title?: string; status?: ConversationStatus }
  ) =>
    api.patch<ConversationDetail>(
      `/novels/${novelId}/conversations/${conversationId}`,
      body
    ),

  deleteConversation: (novelId: string | number, conversationId: number) =>
    api.delete(`/novels/${novelId}/conversations/${conversationId}`),

  listMessages: (
    novelId: string | number,
    conversationId: number,
    params?: { after_sequence?: number; skip?: number; limit?: number }
  ) =>
    api.get<MessageListResponse>(
      `/novels/${novelId}/conversations/${conversationId}/messages`,
      {
        params: {
          after_sequence: params?.after_sequence,
          skip: params?.skip,
          limit: params?.limit,
        },
      }
    ),

  createMessage: (
    novelId: string | number,
    conversationId: number,
    body: MessageCreateBody
  ) =>
    api.post<MessageAccepted>(
      `/novels/${novelId}/conversations/${conversationId}/messages`,
      body
    ),

  getJob: (novelId: string | number, conversationId: number, jobId: number) =>
    api.get<GenerationJobView>(
      `/novels/${novelId}/conversations/${conversationId}/jobs/${jobId}`
    ),

  cancelJob: (
    novelId: string | number,
    conversationId: number,
    jobId: number
  ) =>
    api.post<GenerationJobView>(
      `/novels/${novelId}/conversations/${conversationId}/jobs/${jobId}/cancel`
    ),

  retryJob: (
    novelId: string | number,
    conversationId: number,
    jobId: number
  ) =>
    api.post<GenerationJobView>(
      `/novels/${novelId}/conversations/${conversationId}/jobs/${jobId}/retry`
    ),
};
