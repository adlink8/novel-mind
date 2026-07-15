/**
 * Phase 11 clue / foreshadow API client.
 *
 * Isolated from the shared api.ts surface so Phase 08/09/10 consumer contracts
 * stay untouched. Reuses the repository Axios instance (auth interceptors).
 *
 * Spoiler full-book is Phase 08 timeline_full_book only — this module never
 * exposes a clue-specific preference mutation.
 */

import { api } from "./api";

// ---------------------------------------------------------------------------
// Types (match backend /api/clues projection + human actions)
// ---------------------------------------------------------------------------

export type ClueState =
  | "candidate"
  | "active"
  | "reinforced"
  | "paid_off"
  | "dismissed";

export type ClueVersionSource = "active" | "running_candidate" | "history";

export type ClueRunStatus =
  | "pending"
  | "running"
  | "paused_budget"
  | "paused_dependency"
  | "cancelled"
  | "completed"
  | "failed";

export type ClueOverrideAction =
  | "confirm"
  | "reject"
  | "annotate"
  | "adjust_link";

export type ClueEvidenceRole =
  | "cue"
  | "reinforcement"
  | "payoff"
  | "disposition";

export type ClueLinkTargetKind =
  | "character"
  | "timeline_event"
  | "relationship_observation";

export type ClueLinkValidationStatus =
  | "valid"
  | "unresolved"
  | "source_unavailable"
  | "invalid";

export type ClueOverrideStatus = "active" | "superseded" | "needs_relink";

export type ClueProvenanceKind = "machine" | "manual";

export interface VisibleClue {
  logical_clue_id: string;
  title: string;
  derived_state: ClueState;
  narrative_chapter_number: number;
  source_start: number;
  confidence: number;
  evidence_count: number;
  link_count: number;
  provenance: Record<string, ClueProvenanceKind>;
}

export interface ClueVersionView {
  novel_id: number;
  version_id: number;
  source: ClueVersionSource;
  through_chapter: number;
  full_book: boolean;
  cutoff_chapter: number;
  clues: VisibleClue[];
  /** Server-derived counts only — never invent client-side hidden totals. */
  counts: {
    clues: number;
    by_state: Partial<Record<ClueState, number>>;
    progress?: Record<string, unknown>;
    status?: string;
  };
  /** Filter options derived from the visible set only. */
  available_states: ClueState[];
  available_character_ids: number[];
}

/** Dual-source envelope from GET /api/clues/{novel_id}. */
export interface ClueEnvelope {
  active: ClueVersionView | null;
  running_candidate: ClueVersionView | null;
}

export interface ClueRun {
  id: number;
  novel_id: number;
  version_id: number | null;
  status: ClueRunStatus | string;
  status_reason: string | null;
  progress: Record<string, unknown>;
  cancel_requested: boolean;
  updated_at: string | null;
}

export interface ClueEvidenceItem {
  evidence_id: string;
  role: ClueEvidenceRole | string;
  chapter_id: number;
  narrative_chapter_number: number;
  source_start: number;
  source_end: number;
  content_hash: string;
  excerpt: string | null;
}

export interface ClueLinkItem {
  target_kind: ClueLinkTargetKind | string;
  character_id: number | null;
  timeline_event_id: number | null;
  relationship_observation_ref: string | null;
  validation_status: ClueLinkValidationStatus | string;
}

export interface ClueLifecycleItem {
  from_status: ClueState | string;
  to_status: ClueState | string;
  actor_source: "machine" | "human" | string;
  reason: string;
  event_key: string;
}

export interface CluePayoffChainItem {
  to_status: string;
  event_key: string;
}

export interface ClueDetailPanels {
  clue: VisibleClue;
  evidence: ClueEvidenceItem[];
  links: ClueLinkItem[];
  lifecycle: ClueLifecycleItem[];
  payoff_chain: CluePayoffChainItem[];
}

export interface ClueVersionDiff {
  from_version_id: number;
  to_version_id: number;
  added_logical_clue_ids: string[];
  removed_logical_clue_ids: string[];
  changed_logical_clue_ids: string[];
  lifecycle_differences: Record<string, unknown>[];
  override_applications: Record<string, unknown>[];
}

export interface ClueActionResult {
  override_id: number;
  action: ClueOverrideAction | string;
  logical_clue_id: string;
  version_id: number;
  status: ClueOverrideStatus | string;
  supersedes_id: number | null;
}

export interface ClueListQuery {
  full_book?: boolean;
  character_id?: number;
  /** Server filter alias `status`. */
  status?: ClueState;
}

export interface ClueConfirmPayload {
  action: "confirm";
  reason: string;
}

export interface ClueRejectPayload {
  action: "reject";
  reason: string;
}

export interface ClueAnnotatePayload {
  action: "annotate";
  reason: string;
  note: string;
}

export interface ClueAdjustLinkPayload {
  action: "adjust_link";
  reason: string;
  link: {
    target_kind: ClueLinkTargetKind;
    character_id?: number;
    timeline_event_id?: number;
    relationship_observation_ref?: string;
    validation_status?: ClueLinkValidationStatus;
    supporting_evidence?: Array<Record<string, unknown>>;
  };
}

export type ClueActionPayload =
  | ClueConfirmPayload
  | ClueRejectPayload
  | ClueAnnotatePayload
  | ClueAdjustLinkPayload;

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

const RUN_TIMEOUT_MS = 300_000;

export const clueApi = {
  startOrResume: (novelId: string | number) =>
    api.post<ClueRun>(`/clues/${novelId}/start-or-resume`, null, {
      timeout: RUN_TIMEOUT_MS,
    }),

  status: (novelId: string | number) =>
    api.get<ClueRun>(`/clues/${novelId}/status`),

  cancel: (novelId: string | number) =>
    api.post<ClueRun>(`/clues/${novelId}/cancel`),

  resume: (novelId: string | number) =>
    api.post<ClueRun>(`/clues/${novelId}/resume`, null, {
      timeout: RUN_TIMEOUT_MS,
    }),

  reanalyze: (novelId: string | number) =>
    api.post<ClueRun>(`/clues/${novelId}/reanalyze`, null, {
      timeout: RUN_TIMEOUT_MS,
    }),

  /** Active + running_candidate envelopes; filters are server-side only. */
  getClues: (novelId: string | number, params?: ClueListQuery) =>
    api.get<ClueEnvelope>(`/clues/${novelId}`, {
      params: {
        full_book: params?.full_book,
        character_id: params?.character_id,
        status: params?.status,
      },
    }),

  getVersion: (
    novelId: string | number,
    versionId: number,
    params?: ClueListQuery
  ) =>
    api.get<ClueVersionView>(`/clues/${novelId}/versions/${versionId}`, {
      params: {
        full_book: params?.full_book,
        character_id: params?.character_id,
        status: params?.status,
      },
    }),

  getDetail: (
    novelId: string | number,
    versionId: number,
    logicalClueId: string,
    params?: { full_book?: boolean }
  ) =>
    api.get<ClueDetailPanels>(
      `/clues/${novelId}/versions/${versionId}/clues/${encodeURIComponent(logicalClueId)}`,
      {
        params: {
          full_book: params?.full_book,
        },
      }
    ),

  compare: (
    novelId: string | number,
    fromVersionId: number,
    toVersionId: number
  ) =>
    api.get<ClueVersionDiff>(`/clues/${novelId}/compare`, {
      params: {
        from_version_id: fromVersionId,
        to_version_id: toVersionId,
      },
    }),

  rollback: (
    novelId: string | number,
    targetVersionId: number,
    expectedRevision: number
  ) =>
    api.post<{
      version_id: number;
      revision: number;
      manifest_checksum: string;
    }>(`/clues/${novelId}/rollback`, null, {
      params: {
        target_version_id: targetVersionId,
        expected_revision: expectedRevision,
      },
    }),

  /** Typed human actions — confirm / reject / annotate / adjust_link. */
  action: (
    novelId: string | number,
    logicalClueId: string,
    payload: ClueActionPayload
  ) =>
    api.post<ClueActionResult>(
      `/clues/${novelId}/clues/${encodeURIComponent(logicalClueId)}/actions`,
      payload
    ),
};

/** Stable narrative order: chapter → source_start → logical_clue_id. */
export function sortVisibleClues(clues: VisibleClue[]): VisibleClue[] {
  return [...clues].sort((a, b) => {
    if (a.narrative_chapter_number !== b.narrative_chapter_number) {
      return a.narrative_chapter_number - b.narrative_chapter_number;
    }
    if (a.source_start !== b.source_start) {
      return a.source_start - b.source_start;
    }
    return a.logical_clue_id.localeCompare(b.logical_clue_id);
  });
}

export const CLUE_STATE_LABELS: Record<ClueState, string> = {
  candidate: "候选",
  active: "活跃",
  reinforced: "强化",
  paid_off: "已回收",
  dismissed: "已驳回",
};

export const CLUE_ACTIVE_RUN = new Set([
  "pending",
  "running",
  "partial",
]);
