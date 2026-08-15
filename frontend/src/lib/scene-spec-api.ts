/**
 * Phase 32 Scene Spec / Prompt API client (REQ-VIS-03, D-32-01..D-32-04).
 *
 * Mirrors the owner-scoped candidate envelopes from
 * `backend/app/schemas/scene_spec.py`, `backend/app/api/scene_specs.py` and
 * `backend/app/api/prompt_revisions.py`:
 *
 * - the SceneSpec is the canonical candidate Artifact; a provider prompt is a
 *   derived, provider-neutral PromptRevision and never becomes source truth;
 * - every envelope is candidate-only: review_state is server-derived and never
 *   computed in the browser;
 * - preview is server-compiled and never persists; `provider_calls` is always
 *   explicit (0 in Phase 32 — no image provider is ever called);
 * - edits are explicit candidate revisions: the browser submits one edit action
 *   and the server decides whether it is legal (only user_interpretation
 *   details are editable through the prompt seam);
 * - review actions are append-only and explicit; the server decides the legal
 *   transition and returns the derived envelope.
 */

import { api } from "./api";

// ---------------------------------------------------------------------------
// Types (match backend app/schemas/scene_spec.py View contracts)
// ---------------------------------------------------------------------------

export type SpecSource = "evidence" | "visual_bible" | "user_interpretation";

export type SpecDetailKind =
  | "subject"
  | "action"
  | "setting"
  | "composition"
  | "style"
  | "continuity";

export type SpecReviewState =
  | "candidate"
  | "approved"
  | "rejected"
  | "superseded"
  | "needs_relink";

export type SpecReviewAction = "approve" | "reject" | "supersede" | "needs_relink";

export type SpecActorSource = "human" | "machine";

export type SpecUncertaintyReason =
  | "missing_evidence"
  | "conflicting_claim"
  | "future_spoiler"
  | "ambiguous_reference";

/** One canonical scene detail; strictly source-bounded (D-32-02). */
export interface SceneDetailView {
  detail_key: string;
  kind: SpecDetailKind;
  source: SpecSource;
  text: string;
  author?: string | null;
  rationale?: string | null;
  spoiler_cutoff: number;
  evidence_keys: string[];
  visual_bible_stable_ids: string[];
}

export interface NegativeConstraintView {
  constraint_key: string;
  scope: string;
  source: SpecSource;
  text: string;
  author?: string | null;
  rationale?: string | null;
  spoiler_cutoff: number;
}

export interface SceneUncertaintyView {
  uncertainty_key: string;
  reason: SpecUncertaintyReason;
  detail: string;
}

/** Read envelope: candidate-only SceneSpec with evidence/Visual Bible lineage. */
export interface SceneSpecView {
  id: number;
  owner_id: number;
  novel_id: number;
  spec_key: string;
  revision_number: number;
  scene_candidate_hash: string;
  scene_candidate_id?: number | null;
  visual_bible_revision_hash: string;
  visual_bible_revision_id?: number | null;
  source_snapshot_id: string;
  source_snapshot_hash: string;
  cutoff_chapter: number;
  schema_version: string;
  schema_hash: string;
  compiler_id: string;
  compiler_version: string;
  policy_hash: string;
  content_hash: string;
  review_state: SpecReviewState;
  details: SceneDetailView[];
  negative_constraints: NegativeConstraintView[];
  uncertainties: SceneUncertaintyView[];
}

export interface SceneSpecListResponse {
  items: SceneSpecView[];
  total: number;
}

export interface SceneSpecPreviewResponse {
  spec: SceneSpecView;
  uncertainties: SceneUncertaintyView[];
  provider_calls: number;
  persisted: boolean;
}

export interface SceneSpecCreateResponse {
  spec: SceneSpecView;
  replayed: boolean;
}

export interface SceneSpecDetailResponse {
  spec: SceneSpecView;
  stale: boolean;
}

export interface SceneSpecDiffSectionView {
  section_key: string;
  original?: string | null;
  current?: string | null;
}

export interface SceneSpecDiffResponse {
  original_spec_hash: string;
  current_spec_hash: string;
  stale: boolean;
  same: boolean;
  changed_sections: SceneSpecDiffSectionView[];
}

/** Compiled provider-neutral → provider-specific prompt candidate. */
export interface PromptRevisionView {
  id: number;
  owner_id: number;
  novel_id: number;
  prompt_key: string;
  revision_number: number;
  parent_prompt_revision_id?: number | null;
  scene_spec_hash: string;
  visual_bible_revision_hash: string;
  source_snapshot_id: string;
  source_snapshot_hash: string;
  cutoff_chapter: number;
  schema_version: string;
  schema_hash: string;
  prompt_schema_hash: string;
  compiler_version: string;
  adapter_id: string;
  adapter_version: string;
  config_hash: string;
  input_hash: string;
  prompt_hash: string;
  sections: Record<string, string>;
  negative_constraints: string[];
  uncertainties: string[];
  redacted_preview?: string | null;
  review_state: SpecReviewState;
}

export interface PromptArtifactLineage {
  scene_spec_hash: string;
  visual_bible_revision_hash: string;
  source_snapshot_id: string;
  source_snapshot_hash: string;
  cutoff_chapter: number;
  schema_hash: string;
  prompt_schema_hash: string;
  compiler_version: string;
  adapter_id: string;
  adapter_version: string;
  config_hash: string;
  input_hash: string;
  prompt_hash: string;
}

export interface PromptArtifactView {
  revision: PromptRevisionView;
  lineage: PromptArtifactLineage;
  provider_calls: number;
}

export interface PromptCreateResponse {
  revision: PromptRevisionView;
  lineage: PromptArtifactLineage;
  replayed: boolean;
}

export interface PromptDetailResponse {
  revision: PromptRevisionView;
  stale: boolean;
}

export interface PromptListResponse {
  items: PromptRevisionView[];
  total: number;
}

export interface PromptDiffSectionView {
  section_key: string;
  original?: string | null;
  current?: string | null;
}

export interface PromptListDiffItemView {
  item: string;
  original_count?: number | null;
  current_count?: number | null;
}

export interface PromptDiffResponse {
  original_prompt_hash: string;
  current_prompt_hash: string;
  parent_prompt_revision_id?: number | null;
  revision_number: number;
  same: boolean;
  changed_sections: PromptDiffSectionView[];
  changed_negative_constraints: PromptListDiffItemView[];
  changed_uncertainties: PromptListDiffItemView[];
  prompt_text_changed: boolean;
}

export interface PromptEditResponse {
  revision: PromptRevisionView;
  diff: PromptDiffResponse;
}

/** One explicit review action; scope and legality are decided server-side. */
export interface PromptReviewRequest {
  action: SpecReviewAction;
  actor_source: SpecActorSource;
  actor: string;
  reason: string;
  event_key: string;
  from_review_state: SpecReviewState;
}

export interface PromptReviewEventView {
  action: SpecReviewAction;
  actor_source: SpecActorSource;
  actor: string;
  reason: string;
  event_key: string;
  from_review_state: SpecReviewState;
  to_review_state: SpecReviewState;
}

export interface PromptApprovalGateView {
  ok: boolean;
  reason_code?: string | null;
  detail?: string | null;
}

/** Review envelope: current state, stale marker, history and approval gate. */
export interface PromptRevisionReviewEnvelope {
  revision: PromptRevisionView;
  stale: boolean;
  review_events: PromptReviewEventView[];
  approval_gate?: PromptApprovalGateView | null;
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export interface SceneSpecCompileRequest {
  spec_key: string;
  candidate_set_id: number;
  candidate_key: string;
  visual_bible_version_id: number;
  source_snapshot_id: string;
  revision_number?: number;
  policy_hash?: string | null;
  config_hash?: string | null;
}

export interface PromptCompileRequest {
  spec_id: number;
  prompt_key: string;
  adapter_id?: string;
  revision_number?: number;
  parent_prompt_revision_id?: number | null;
}

export interface PromptEditRequest {
  prompt_key: string;
  detail_key: string;
  kind: SpecDetailKind;
  text: string;
  author: string;
  rationale: string;
}

export const sceneSpecsApi = {
  /** List compiled candidate specs for the owned novel (oldest first). */
  listSpecs: (novelId: string | number) =>
    api.get<SceneSpecListResponse>(`/novels/${novelId}/scene-specs`),

  /** Compile a preview; server-side only, nothing is persisted. */
  preview: (novelId: string | number, body: SceneSpecCompileRequest) =>
    api.post<SceneSpecPreviewResponse>(
      `/novels/${novelId}/scene-specs/preview`,
      body
    ),

  /** Persist one compiled candidate spec (append-only, idempotent replay). */
  create: (novelId: string | number, body: SceneSpecCompileRequest) =>
    api.post<SceneSpecCreateResponse>(`/novels/${novelId}/scene-specs`, body),

  /** One candidate spec plus its staleness marker (D-32-03). */
  getSpec: (novelId: string | number, specId: number) =>
    api.get<SceneSpecDetailResponse>(`/novels/${novelId}/scene-specs/${specId}`),

  /** Deterministic recompile diff against the current approved revision. */
  getSpecDiff: (novelId: string | number, specId: number) =>
    api.get<SceneSpecDiffResponse>(`/novels/${novelId}/scene-specs/${specId}/diff`),
};

export const promptRevisionsApi = {
  /** List compiled prompt candidates for the owned novel (oldest first). */
  listRevisions: (novelId: string | number) =>
    api.get<PromptListResponse>(`/novels/${novelId}/prompt-revisions`),

  /** Compile a prompt preview; nothing persists, no provider/network call. */
  preview: (novelId: string | number, body: PromptCompileRequest) =>
    api.post<PromptArtifactView>(
      `/novels/${novelId}/prompt-revisions/preview`,
      body
    ),

  /** Persist one compiled prompt candidate (append-only, idempotent replay). */
  create: (novelId: string | number, body: PromptCompileRequest) =>
    api.post<PromptCreateResponse>(`/novels/${novelId}/prompt-revisions`, body),

  /** One prompt candidate plus its staleness marker (D-32-03). */
  getRevision: (novelId: string | number, revisionId: number) =>
    api.get<PromptDetailResponse>(
      `/novels/${novelId}/prompt-revisions/${revisionId}`
    ),

  /** Deterministic diff against the revision's parent (D-32-04). */
  getRevisionDiff: (novelId: string | number, revisionId: number) =>
    api.get<PromptDiffResponse>(
      `/novels/${novelId}/prompt-revisions/${revisionId}/diff`
    ),

  /** Apply a human edit → explicit new candidate revision (D-32-04). */
  edit: (
    novelId: string | number,
    revisionId: number,
    body: PromptEditRequest
  ) =>
    api.post<PromptEditResponse>(
      `/novels/${novelId}/prompt-revisions/${revisionId}/edit`,
      body
    ),

  /** Append one explicit, idempotent review action (server-derived result). */
  review: (
    novelId: string | number,
    revisionId: number,
    body: PromptReviewRequest
  ) =>
    api.post<PromptRevisionReviewEnvelope>(
      `/novels/${novelId}/prompt-revisions/${revisionId}/review`,
      body
    ),

  /** Append-only review history + state + stale + approval gate. */
  getReviewHistory: (novelId: string | number, revisionId: number) =>
    api.get<PromptRevisionReviewEnvelope>(
      `/novels/${novelId}/prompt-revisions/${revisionId}/history`
    ),
};

// ---------------------------------------------------------------------------
// Read-only helpers (no client-side truth derivation)
// ---------------------------------------------------------------------------

/** Shorten a sha256 hex for compact display while keeping it copyable. */
export function shortSpecHash(hash: string): string {
  if (!hash || hash.length <= 12) return hash;
  return `${hash.slice(0, 8)}…${hash.slice(-4)}`;
}

/** Label for a detail source; never upgrades interpretation to canon. */
export function specSourceLabel(source: SpecSource): string {
  switch (source) {
    case "evidence":
      return "证据";
    case "visual_bible":
      return "视觉圣经";
    case "user_interpretation":
      return "用户解读";
  }
}
