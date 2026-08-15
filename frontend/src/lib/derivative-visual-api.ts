/**
 * Phase 38-04 derivative visual review API client (REQ-FORK-04 / D-38-03).
 *
 * Mirrors the owner-scoped envelopes from
 * `backend/app/schemas/derivative_visual_asset.py` and the review seam routes
 * in `backend/app/api/derivative_visual_review.py`:
 *
 * - the candidate list / detail only ever expose owner-visible candidates —
 *   a foreign or missing candidate is an identical 404;
 * - review actions are explicit and append-only: the browser submits the
 *   action + event_key + from_review_state and the server decides the legal
 *   transition (a `blocked` candidate can never be approved);
 * - the client never supplies owner/novel/fork/project/namespace/approval/
 *   path — all scope comes from the novel path segment and the server.
 */

import { api } from "./api";

// ---------------------------------------------------------------------------
// Types (match backend app/schemas/derivative_visual_asset.py read envelopes)
// ---------------------------------------------------------------------------

export type DerivativeAssetState =
  | "candidate"
  | "needs_review"
  | "approved"
  | "rejected"
  | "superseded"
  | "blocked";

export type DerivativeAssetAction = "approve" | "reject" | "supersede";

export type DerivativeConsistencyVerdict =
  | "pass"
  | "concern"
  | "fail"
  | "unavailable";

export type DerivativeActorSource = "human" | "machine";

export interface DerivativeAssetIdentityRow {
  stable_id: string;
  entity_key: string;
  entity_type: string;
  source_entity_hash: string;
}

export interface DerivativeAssetSourceRef {
  asset_key: string;
  asset_id: string;
  source_asset_id: string;
  source_bytes_hash: string;
}

export interface DerivativeVisualVersionRef {
  version_id: number;
  version_key: string;
  version_hash: string;
}

export interface DerivativeSourceSnapshotRef {
  source_snapshot_id: string;
  source_snapshot_hash: string;
  source_manifest_hash: string;
  cutoff_chapter: number;
}

/** One chapter's deterministic identity/style score (0.0 or 1.0). */
export interface DerivativeChapterScoreView {
  chapter_number: number;
  identity_score: number;
  style_score: number;
  identity_consistent: boolean;
  style_consistent: boolean;
}

export interface DerivativeConsistencyReport {
  schema_version: string;
  evaluator_id: string;
  evaluator_version: string;
  chapters: DerivativeChapterScoreView[];
  reasons: string[];
  verdict: DerivativeConsistencyVerdict;
  details: Record<string, unknown>;
}

export interface DerivativeAssetReviewEventView {
  action: DerivativeAssetAction;
  actor_source: DerivativeActorSource;
  actor: string;
  reason: string;
  event_key: string;
  from_review_state: DerivativeAssetState;
  to_review_state: DerivativeAssetState;
}

export interface DerivativeAssetReviewEnvelope {
  review_state: DerivativeAssetState;
  consistency_verdict: DerivativeConsistencyVerdict;
  consistency_report: DerivativeConsistencyReport | null;
  reasons: string[];
  review_events: DerivativeAssetReviewEventView[];
}

/** Full candidate read envelope (any review state, owner-scoped). */
export interface DerivativeVisualAssetView {
  id: number;
  owner_id: number;
  novel_id: number;
  project_id: number;
  fork_id: number;
  asset_id: string;
  asset_key: string;
  content_hash: string;
  mime_type: string;
  size_bytes: number;
  /** Sealed to fanfiction_visual (D-38-01). */
  namespace: string;
  scene_spec_hash: string;
  chapter_number: number;
  visual_version: DerivativeVisualVersionRef;
  source_snapshot: DerivativeSourceSnapshotRef;
  approval: DerivativeAssetState;
  review: DerivativeAssetReviewEnvelope;
  source_refs: DerivativeAssetSourceRef[];
  identity_lineage: DerivativeAssetIdentityRow[];
  generator_lineage: Record<string, unknown>;
  divergence_manifest_hash: string;
}

export interface DerivativeReviewListResponse {
  items: DerivativeVisualAssetView[];
  total: number;
}

/** One explicit review action; scope comes from the novel path, never the body. */
export interface DerivativeReviewActionBody {
  event_key: string;
  action: DerivativeAssetAction;
  actor_source: DerivativeActorSource;
  actor: string;
  reason: string;
  from_review_state: DerivativeAssetState;
}

export interface DerivativeReviewActionResponse {
  asset: DerivativeVisualAssetView;
}

// ---------------------------------------------------------------------------
// API client
// ---------------------------------------------------------------------------

export const derivativeVisualApi = {
  /** List every candidate visible in the owner/novel scope (any state). */
  listReviewCandidates: (
    novelId: number | string,
    params?: { project_id?: number; fork_id?: number; review_state?: DerivativeAssetState }
  ) =>
    api.get<DerivativeReviewListResponse>(
      `/novels/${novelId}/derivative-visual/review`,
      { params }
    ),

  /** One candidate review detail (source refs / scores / divergence / events). */
  getReviewCandidate: (
    novelId: number | string,
    candidateId: number
  ) =>
    api.get<DerivativeVisualAssetView>(
      `/novels/${novelId}/derivative-visual/review/${candidateId}`
    ),

  /** Apply one explicit approve/reject/supersede action (idempotent). */
  reviewCandidate: (
    novelId: number | string,
    candidateId: number,
    body: DerivativeReviewActionBody
  ) =>
    api.post<DerivativeReviewActionResponse>(
      `/novels/${novelId}/derivative-visual/review/${candidateId}/action`,
      body
    ),
};
