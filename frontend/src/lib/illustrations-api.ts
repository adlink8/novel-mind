/**
 * Phase 33-04 Illustration review product API client (REQ-VIS-04, D-33-03/D-33-04).
 *
 * Mirrors the owner-scoped candidate envelopes from
 * `backend/app/services/illustrations/review.py` and `backend/app/api/illustrations.py`
 * (GET .../illustrations/gallery, GET .../assets/{id}/review,
 * POST .../assets/{id}/review):
 *
 * - every envelope is candidate-only: approval_state is server-derived and
 *   never computed in the browser; the client never promotes a candidate;
 * - the proposal gate (succeeded job + complete lineage + cleared rights +
 *   settled budget + visible consistency report) is server-side; the client
 *   only surfaces its reason code;
 * - review actions are append-only and explicit: the client submits the action
 *   and the server decides the legal transition and the derived state;
 * - failed/unknown jobs stay explicit (status/error/reason) and the gallery
 *   surfaces the retry action; a provider failure is never an empty success.
 */

import { api } from "./api";

// ---------------------------------------------------------------------------
// Types (match backend app/services/illustrations/review.py View contracts)
// ---------------------------------------------------------------------------

export type IllustrationApprovalState =
  | "candidate"
  | "proposal_ready"
  | "rejected"
  | "superseded";

export type IllustrationReviewAction =
  | "approve"
  | "reject"
  | "supersede"
  | "needs_relink";

export type IllustrationJobStatus =
  | "queued"
  | "running"
  | "paused_budget"
  | "paused_dependency"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "outcome_unknown";

export type IllustrationRightsStatus =
  | "unreviewed"
  | "cleared"
  | "pending"
  | "denied";

export type IllustrationActorSource = "human" | "machine";

export type IllustrationConsistencyVerdict =
  | "pass"
  | "concern"
  | "fail"
  | "unavailable";

export interface AssetRevisionView {
  id: number;
  owner_id: number;
  novel_id: number;
  job_id: number;
  revision_key: string;
  revision_number: number;
  asset_id: string;
  mime_type: string;
  width: number;
  height: number;
  size_bytes: number;
  bytes_hash: string;
  scene_spec_hash: string;
  prompt_revision_id: number | null;
  prompt_revision_hash: string;
  visual_bible_revision_hash: string;
  source_snapshot_id: string;
  source_snapshot_hash: string;
  cutoff_chapter: number;
  provider: string;
  provider_model: string;
  provider_request_id: string | null;
  rights_status: IllustrationRightsStatus;
  approval_state: IllustrationApprovalState;
}

export interface IllustrationJobView {
  id: number;
  owner_id: number;
  novel_id: number;
  job_key: string;
  idempotency_key: string;
  status: IllustrationJobStatus;
  status_reason: string | null;
  error_code: string | null;
  retry_count: number;
  scene_spec_hash: string;
  prompt_revision_id: number | null;
  prompt_revision_hash: string;
  visual_bible_revision_hash: string;
  source_snapshot_id: string;
  source_snapshot_hash: string;
  cutoff_chapter: number;
  config_hash: string;
  price_snapshot: Record<string, unknown>;
}

export interface ConsistencyReportView {
  id: number;
  owner_id: number;
  novel_id: number;
  asset_revision_id: number;
  report_key: string;
  evaluator_id: string;
  evaluator_version: string;
  model_lineage: Record<string, unknown>;
  fixture_set_hash: string;
  reference_asset_ids: string[];
  scores: Record<string, unknown>;
  verdict: IllustrationConsistencyVerdict;
  details: Record<string, unknown>;
  idempotency_key: string;
  schema_version: string;
  created_at: string | null;
}

export interface IllustrationReviewEventView {
  event_key: string;
  action: IllustrationReviewAction;
  actor_source: string;
  actor: string;
  reason: string;
  from_approval_state: IllustrationApprovalState;
  to_approval_state: IllustrationApprovalState;
}

export interface IllustrationProposalGateView {
  ok: boolean;
  reason_code: string | null;
  detail: string | null;
}

export interface IllustrationAttemptView {
  id: number;
  attempt_number: number;
  status: string;
  provider_request_id: string | null;
  request_hash: string;
  response_hash: string | null;
  usage: Record<string, unknown>;
  cost_usd: string | null;
  latency_ms: number | null;
  error_code: string | null;
}

export interface IllustrationBudgetEvidenceView {
  settled_calls: number;
  settled_cost_usd: string | null;
  reservation_status: string;
  settled_usage: Record<string, unknown>;
  price_snapshot: Record<string, unknown>;
  ledger_max_calls: number | null;
  ledger_max_cost_usd: string | null;
}

export interface IllustrationGalleryItemView {
  asset: AssetRevisionView;
  job: IllustrationJobView;
  consistency: ConsistencyReportView | null;
  review_events: IllustrationReviewEventView[];
  approval_gate: IllustrationProposalGateView | null;
}

export interface IllustrationGalleryResponse {
  items: IllustrationGalleryItemView[];
  total: number;
}

export interface IllustrationReviewEnvelope {
  asset: AssetRevisionView;
  job: IllustrationJobView;
  attempts: IllustrationAttemptView[];
  budget: IllustrationBudgetEvidenceView | null;
  consistency: ConsistencyReportView | null;
  review_events: IllustrationReviewEventView[];
  approval_gate: IllustrationProposalGateView | null;
}

export interface IllustrationReviewActionResponse {
  asset: AssetRevisionView;
  envelope: IllustrationReviewEnvelope;
}

/** One explicit approval action; scope and legality are decided server-side. */
export interface IllustrationReviewActionRequest {
  event_key: string;
  action: IllustrationReviewAction;
  actor_source: IllustrationActorSource;
  actor: string;
  reason: string;
  from_approval_state: IllustrationApprovalState;
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export const illustrationsApi = {
  /** Candidate gallery for human review (job status/error/retry visible). */
  gallery: (novelId: string | number) =>
    api.get<IllustrationGalleryResponse>(
      `/novels/${novelId}/illustrations/gallery`
    ),

  /** Full review envelope: lineage drawer + compare + history + gate. */
  reviewEnvelope: (novelId: string | number, assetId: number) =>
    api.get<IllustrationReviewEnvelope>(
      `/novels/${novelId}/illustrations/assets/${assetId}/review`
    ),

  /** Append one explicit human approval action; result state is server-derived. */
  reviewAsset: (
    novelId: string | number,
    assetId: number,
    body: IllustrationReviewActionRequest
  ) =>
    api.post<IllustrationReviewActionResponse>(
      `/novels/${novelId}/illustrations/assets/${assetId}/review`,
      body
    ),

  /** Explicitly re-queue an eligible terminal/paused job (original lineage). */
  retryJob: (novelId: string | number, jobId: number) =>
    api.post<IllustrationJobView>(
      `/novels/${novelId}/illustrations/jobs/${jobId}/retry`
    ),
};

// ---------------------------------------------------------------------------
// Read-only helpers (no client-side truth derivation)
// ---------------------------------------------------------------------------

/** Shorten a sha256 hex for compact display while keeping it copyable. */
export function shortIllustrationHash(hash: string): string {
  if (!hash || hash.length <= 12) return hash;
  return `${hash.slice(0, 8)}…${hash.slice(-4)}`;
}

/** Jobs that can be explicitly retried (mirrors the backend eligibility set). */
export const RETRYABLE_JOB_STATUSES: ReadonlySet<IllustrationJobStatus> = new Set([
  "failed",
  "cancelled",
  "outcome_unknown",
  "paused_budget",
  "paused_dependency",
]);

export function isRetryableJobStatus(status: IllustrationJobStatus): boolean {
  return RETRYABLE_JOB_STATUSES.has(status);
}
