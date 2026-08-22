/**
 * Phase 30 Visual Bible product API client (REQ-VIS-01, D-30-01..D-30-04).
 *
 * Mirrors the owner-scoped candidate envelopes from
 * `backend/app/schemas/visual_bible.py` and `backend/app/api/visual_bible.py`
 * (GET /api/novels/{novel_id}/visual-bible[/{version_id}],
 * POST .../{version_id}/review):
 *
 * - every envelope is candidate-only: review_state is server-derived and never
 *   computed in the browser;
 * - authority is one of four labels (canon_fact / probable_inference /
 *   literary_interpretation / user_interpretation) — the client never upgrades
 *   an inference or interpretation to canon;
 * - review actions are append-only and explicit; the client submits the action
 *   and the server decides the legal transition.
 */

import { api } from "./api";

// ---------------------------------------------------------------------------
// Types (match backend app/schemas/visual_bible.py View contracts)
// ---------------------------------------------------------------------------

export type VisualAuthority =
  | "canon_fact"
  | "probable_inference"
  | "literary_interpretation"
  | "user_interpretation";

export type VisualEntityType =
  | "character"
  | "place"
  | "item"
  | "faction"
  | "style";

export type VisualReviewAction =
  | "approve"
  | "reject"
  | "edit"
  | "supersede"
  | "needs_relink";

export type VisualReviewState =
  | "candidate"
  | "approved"
  | "rejected"
  | "superseded"
  | "needs_relink";

export type VisualRightsStatus = "unreviewed" | "cleared" | "pending" | "denied";

export type VisualActorSource = "human" | "machine";

/** One primary-text evidence locator; offsets/hash/cutoff are server-verified. */
export interface VisualEvidenceRefView {
  evidence_key: string;
  source_snapshot_id: string;
  source_snapshot_hash: string;
  chapter_id: number;
  chapter_number: number;
  source_start: number;
  source_end: number;
  content_hash: string;
  excerpt?: string | null;
  cutoff_chapter: number;
}

export interface VisualClaimView {
  claim_key: string;
  entity_stable_id: string;
  authority: VisualAuthority;
  description: string;
  author?: string | null;
  rationale?: string | null;
  cutoff_chapter: number;
  claim_hash: string;
  evidence_refs: VisualEvidenceRefView[];
}

export interface VisualEntityView {
  stable_id: string;
  entity_key: string;
  entity_type: VisualEntityType;
  description: string;
  authority: VisualAuthority;
  disclosure_cutoff: number;
  claims: VisualClaimView[];
}

export interface VisualReferenceAssetView {
  asset_key: string;
  asset_id: string;
  mime_type: string;
  bytes_hash: string;
  rights_status: VisualRightsStatus;
  approved: boolean;
}

export interface VisualReviewEventView {
  action: VisualReviewAction;
  actor_source: VisualActorSource;
  actor: string;
  reason: string;
  event_key: string;
  from_review_state: VisualReviewState;
  to_review_state: VisualReviewState;
}

/** Read envelope: candidate review state + evidence + authority labels. */
export interface VisualBibleVersionView {
  id: number;
  owner_id: number;
  novel_id: number;
  version_key: string;
  revision_number: number;
  parent_version_id?: number | null;
  source_snapshot_id: string;
  source_snapshot_hash: string;
  cutoff_chapter: number;
  schema_version: string;
  schema_hash: string;
  policy_hash: string;
  manifest_hash: string;
  review_state: VisualReviewState;
  style_profile?: Record<string, unknown> | null;
  constraints?: Array<Record<string, unknown>> | null;
  entities: VisualEntityView[];
  reference_assets: VisualReferenceAssetView[];
  review_events: VisualReviewEventView[];
}

export interface VisualBibleVersionListResponse {
  items: VisualBibleVersionView[];
  total: number;
}

/** One explicit review action; scope and legality are decided server-side. */
export interface VisualBibleReviewRequest {
  action: VisualReviewAction;
  actor_source: VisualActorSource;
  actor: string;
  reason: string;
  event_key: string;
  from_review_state: VisualReviewState;
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export const visualBibleApi = {
  /** List candidate revisions for the owned novel (oldest first). */
  listVersions: (novelId: string | number) =>
    api.get<VisualBibleVersionListResponse>(`/novels/${novelId}/visual-bible`),

  /** One full candidate envelope (authority labels + evidence + review). */
  getVersion: (novelId: string | number, versionId: number) =>
    api.get<VisualBibleVersionView>(
      `/novels/${novelId}/visual-bible/${versionId}`
    ),

  /** Apply one append-only, idempotent review action; result is server-derived. */
  review: (
    novelId: string | number,
    versionId: number,
    body: VisualBibleReviewRequest
  ) =>
    api.post<VisualBibleVersionView>(
      `/novels/${novelId}/visual-bible/${versionId}/review`,
      body
    ),
};

// ---------------------------------------------------------------------------
// Helpers (read-only; no client-side truth derivation)
// ---------------------------------------------------------------------------

/** Pick the latest candidate revision for review (highest id). Never "active canon". */
export function pickLatestVisualBibleVersion(
  versions: VisualBibleVersionView[]
): VisualBibleVersionView | null {
  if (!versions.length) return null;
  return [...versions].sort((a, b) => b.id - a.id)[0] ?? null;
}

/** Shorten a sha256 hex for compact display while keeping it copyable. */
export function shortVisualHash(hash: string): string {
  if (!hash || hash.length <= 12) return hash;
  return `${hash.slice(0, 8)}…${hash.slice(-4)}`;
}
