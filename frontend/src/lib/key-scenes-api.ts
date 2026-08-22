/**
 * Phase 31 Key Scenes product API client (REQ-VIS-02, D-31-01..D-31-05).
 *
 * Mirrors the owner-scoped candidate envelopes from
 * `backend/app/schemas/key_scene.py` and `backend/app/api/key_scenes.py`
 * (GET /api/novels/{novel_id}/key-scenes[/{set_id}],
 * POST .../{set_id}/review, POST .../{set_id}/freeze, GET .../{set_id}/frozen):
 *
 * - every envelope is candidate-only: review_state is server-derived and never
 *   computed in the browser; the client never re-scores or re-ranks;
 * - evidence ranges are the only citation authority; the advisory
 *   speaker/dialogue heuristic signal is diagnostic metadata only;
 * - review actions are append-only and explicit; the client submits the action
 *   and the server decides the legal transition and the frozen set.
 */

import { api } from "./api";

// ---------------------------------------------------------------------------
// Types (match backend app/schemas/key_scene.py View contracts)
// ---------------------------------------------------------------------------

export type KeySceneReviewAction =
  | "approve"
  | "reject"
  | "needs_relink"
  | "supersede";

export type KeySceneReviewState =
  | "candidate"
  | "approved"
  | "rejected"
  | "superseded"
  | "needs_relink";

export type KeySceneActorSource = "human" | "machine";

export type HeuristicAvailability = "available" | "ambiguous" | "unavailable";

export interface SceneCoordinatesView {
  cast: string[];
  place?: string | null;
  time?: string | null;
  pov?: string | null;
}

/** One primary-text evidence locator; the only citation authority. */
export interface SceneEvidenceRangeView {
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

export interface SalienceReasonView {
  reason_code: string;
  detail?: string | null;
  score?: number | null;
}

export interface SpeakerOffsetView {
  offset_start: number;
  offset_end: number;
  speaker_key?: string | null;
}

export interface DialogueOffsetView {
  offset_start: number;
  offset_end: number;
}

/** REQ-VIS-06 advisory speaker/dialogue metadata (never citation authority). */
export interface SpeakerDialogueHeuristicSignalView {
  availability: HeuristicAvailability;
  speaker_offsets: SpeakerOffsetView[];
  dialogue_offsets: DialogueOffsetView[];
  confidence?: number | null;
  warnings: string[];
  detector_id: string;
  detector_version: string;
}

export interface SceneCandidateView {
  candidate_key: string;
  candidate_order: number;
  scene_id: string;
  chapter_id: number;
  chapter_number: number;
  source_start: number;
  source_end: number;
  source_hash: string;
  coordinates: SceneCoordinatesView;
  spoiler_cutoff: number;
  salience_reasons: SalienceReasonView[];
  score_total: number;
  score_breakdown: Record<string, unknown>;
  diversity_key: string;
  detector_id: string;
  detector_version: string;
  policy_hash: string;
  evidence_ranges: SceneEvidenceRangeView[];
  heuristic_signal?: SpeakerDialogueHeuristicSignalView | null;
  review_state: KeySceneReviewState;
}

export interface SceneReviewDecisionView {
  decision_key: string;
  action: KeySceneReviewAction;
  actor_source: KeySceneActorSource;
  actor: string;
  reason: string;
  from_review_state: KeySceneReviewState;
  to_review_state: KeySceneReviewState;
  candidate_key?: string | null;
}

/** Read envelope: candidate-only, evidence + reasons + heuristic metadata. */
export interface SceneCandidateSetView {
  id: number;
  owner_id: number;
  novel_id: number;
  version_key: string;
  revision_number: number;
  parent_set_id?: number | null;
  source_snapshot_id: string;
  source_snapshot_hash: string;
  cutoff_chapter: number;
  schema_version: string;
  schema_hash: string;
  policy_hash: string;
  detector_id: string;
  detector_version: string;
  manifest_hash: string;
  approved_visual_bible_revision_id?: number | null;
  approved_visual_bible_revision_hash?: string | null;
  review_state: KeySceneReviewState;
  candidates: SceneCandidateView[];
  review_decisions: SceneReviewDecisionView[];
}

/** Frozen set envelope: approved candidates only + frozen manifest hash. */
export interface FrozenKeySceneSetView {
  id: number;
  owner_id: number;
  novel_id: number;
  version_key: string;
  revision_number: number;
  parent_set_id?: number | null;
  source_snapshot_id: string;
  source_snapshot_hash: string;
  cutoff_chapter: number;
  schema_version: string;
  schema_hash: string;
  policy_hash: string;
  detector_id: string;
  detector_version: string;
  manifest_hash: string;
  approved_visual_bible_revision_id?: number | null;
  approved_visual_bible_revision_hash?: string | null;
  review_state: KeySceneReviewState;
  candidates: SceneCandidateView[];
  review_decisions: SceneReviewDecisionView[];
}

export interface KeySceneSetListResponse {
  items: SceneCandidateSetView[];
  total: number;
}

export interface KeySceneGenerateResponse {
  set: SceneCandidateSetView;
  replayed: boolean;
}

/** One explicit candidate review action; scope and legality are server-side. */
export interface KeySceneReviewRequest {
  decision_key: string;
  action: KeySceneReviewAction;
  actor_source: KeySceneActorSource;
  actor: string;
  reason: string;
  from_review_state: KeySceneReviewState;
  candidate_key: string;
}

export interface KeySceneReviewResponse {
  set: SceneCandidateSetView;
}

/** Explicit set freeze; the decision key is server-derived (idempotent). */
export interface KeySceneFreezeRequest {
  actor_source: KeySceneActorSource;
  actor: string;
  reason: string;
}

export interface KeySceneFreezeResponse {
  set: SceneCandidateSetView;
  frozen: FrozenKeySceneSetView;
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export const keyScenesApi = {
  /** List candidate sets for the owned novel (oldest first). */
  listSets: (novelId: string | number) =>
    api.get<KeySceneSetListResponse>(`/novels/${novelId}/key-scenes`),

  /** One full candidate envelope (evidence + reasons + diversity + review). */
  getSet: (novelId: string | number, setId: number) =>
    api.get<SceneCandidateSetView>(`/novels/${novelId}/key-scenes/${setId}`),

  /** Append one candidate review action; result state is server-derived. */
  reviewCandidate: (
    novelId: string | number,
    setId: number,
    body: KeySceneReviewRequest
  ) =>
    api.post<KeySceneReviewResponse>(
      `/novels/${novelId}/key-scenes/${setId}/review`,
      body
    ),

  /** Freeze the set (server-gated, idempotent). */
  freeze: (
    novelId: string | number,
    setId: number,
    body: KeySceneFreezeRequest
  ) =>
    api.post<KeySceneFreezeResponse>(
      `/novels/${novelId}/key-scenes/${setId}/freeze`,
      body
    ),

  /** Read the frozen candidate subset (approved candidates + manifest). */
  getFrozen: (novelId: string | number, setId: number) =>
    api.get<FrozenKeySceneSetView>(`/novels/${novelId}/key-scenes/${setId}/frozen`),
};

// ---------------------------------------------------------------------------
// Read-only helpers (no client-side truth derivation)
// ---------------------------------------------------------------------------

/** Shorten a sha256 hex for compact display while keeping it copyable. */
export function shortKeySceneHash(hash: string): string {
  if (!hash || hash.length <= 12) return hash;
  return `${hash.slice(0, 8)}…${hash.slice(-4)}`;
}

/** Stable diversity group label for one candidate's deterministic key. */
export function diversityGroupLabel(diversityKey: string): string {
  if (!diversityKey) return "未分组";
  return shortKeySceneHash(diversityKey);
}
