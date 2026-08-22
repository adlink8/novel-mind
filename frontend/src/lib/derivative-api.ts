/**
 * Phase 36 derivative editor API client (REQ-FORK-02 / REQ-CRE-03, D-36-01..D-36-03).
 *
 * Mirrors the owner-scoped envelopes from
 * `backend/app/schemas/derivative_project.py`, `derivative_chapter.py` and the
 * canon-fork list route:
 *
 * - every project is bound to an explicit Canon Fork and sealed to
 *   `fanfiction_canon` — the client never supplies owner/novel/space/version/
 *   cutoff, and the editor never infers a fork from a reading page;
 * - chapters are an ordered plan; patches carry the `base_revision`
 *   optimistic-concurrency token and the server rejects stale writes with the
 *   current revision/checksum;
 * - reorder sends the exact full set of chapter ids (missing/extras/duplicates
 *   fail closed).
 */

import { api } from "./api";

// ---------------------------------------------------------------------------
// Derivative project (Phase 36-01)
// ---------------------------------------------------------------------------

export type DerivativeProjectStatus = "active" | "archived";

export interface DerivativeProjectView {
  id: number;
  owner_id: number;
  novel_id: number;
  fork_id: number;
  project_key: string;
  name: string;
  description: string | null;
  status: DerivativeProjectStatus;
  /** Sealed to fanfiction_canon (D-36-03); there is no other write target. */
  space: "fanfiction_canon";
  fork_key: string;
  source_version_key: string;
  source_snapshot_hash: string;
  through_chapter: number;
  full_book_authorized: boolean;
  cutoff_snapshot_hash: string;
  scope_hash: string;
  manifest_hash: string;
  created_at: string;
  updated_at: string;
}

export interface DerivativeProjectListResponse {
  novel_id: number;
  total: number;
  items: DerivativeProjectView[];
}

export interface DerivativeProjectCreateResponse {
  project: DerivativeProjectView;
  message?: string | null;
}

export interface DerivativeProjectCreateBody {
  /** Explicit Canon Fork selection; never inferred from a reading page. */
  fork_id: number;
  name: string;
  project_key?: string | null;
  description?: string | null;
}

// ---------------------------------------------------------------------------
// Canon fork (read-only list for the explicit fork picker)
// ---------------------------------------------------------------------------

export interface CanonForkView {
  id: number;
  fork_key: string;
  space: string;
  status: string;
  source_version_key: string;
  through_chapter: number;
  cutoff_snapshot_hash: string;
}

export interface CanonForkListResponse {
  novel_id: number;
  forks: CanonForkView[];
}

// ---------------------------------------------------------------------------
// Derivative chapter (Phase 36-02)
// ---------------------------------------------------------------------------

export type DerivativeChapterStatus = "draft" | "archived";

export interface DerivativeChapterView {
  id: number;
  project_id: number;
  owner_id: number;
  novel_id: number;
  position: number;
  title: string;
  markdown: string;
  markdown_checksum: string;
  status: DerivativeChapterStatus;
  /** Optimistic-concurrency token the editor echoes back as base_revision. */
  revision: number;
  created_at: string;
  updated_at: string;
}

/** The project's frozen fork/version/cutoff scope echoed on every list/detail. */
export interface DerivativeChapterScope {
  project_id: number;
  owner_id: number;
  novel_id: number;
  fork_id: number;
  space: "fanfiction_canon";
  fork_key: string;
  source_version_key: string;
  through_chapter: number;
  full_book_authorized: boolean;
  cutoff_snapshot_hash: string;
}

export interface DerivativeChapterListResponse {
  project_id: number;
  scope: DerivativeChapterScope;
  total: number;
  items: DerivativeChapterView[];
}

export interface DerivativeChapterCreateResponse {
  chapter: DerivativeChapterView;
  scope: DerivativeChapterScope;
  message?: string | null;
}

export interface DerivativeChapterPatchBody {
  title?: string;
  markdown?: string;
  status?: DerivativeChapterStatus;
  /** Required optimistic-concurrency token; a stale value is rejected (409). */
  base_revision: number;
}

export interface DerivativeChapterReorderResponse extends DerivativeChapterListResponse {}

// ---------------------------------------------------------------------------
// Derivative chapter revision / autosave / diff / rollback (Phase 36-03/04)
// ---------------------------------------------------------------------------

/** Why an immutable revision row exists; release is never a row kind (D-36-02). */
export type DerivativeRevisionKind = "create" | "autosave" | "rollback";

/** Rollback journal: explicit owner actions are approved; drafts are not. */
export type DerivativeRevisionApproval = "not_required" | "approved";

/** History row without the full content (keeps the listing lean). */
export interface DerivativeRevisionSummary {
  id: number;
  chapter_id: number;
  project_id: number;
  revision_number: number;
  parent_revision_id: number | null;
  kind: DerivativeRevisionKind;
  content_checksum: string;
  actor_id: number | null;
  reason: string | null;
  approval_state: DerivativeRevisionApproval;
  created_at: string;
}

/** Full immutable revision row, including the canonical Markdown snapshot. */
export interface DerivativeRevisionView extends DerivativeRevisionSummary {
  owner_id: number;
  novel_id: number;
  content: string;
  updated_at: string;
}

export interface DerivativeRevisionHistoryResponse {
  chapter_id: number;
  project_id: number;
  total: number;
  items: DerivativeRevisionSummary[];
}

/** Client intent: save the current Markdown draft under a CAS token. */
export interface DerivativeAutosaveBody {
  content: string;
  base_revision: number;
}

export interface DerivativeAutosaveResponse {
  status: "saved" | "noop";
  chapter: DerivativeChapterView;
  revision: DerivativeRevisionView;
  message: string | null;
}

/** One deterministic diff line inside a hunk. */
export interface DerivativeDiffLine {
  op: "context" | "add" | "delete";
  text: string;
}

/** One contiguous changed region (unified-diff style, 1-based line numbers). */
export interface DerivativeDiffHunk {
  old_start: number;
  old_count: number;
  new_start: number;
  new_count: number;
  lines: DerivativeDiffLine[];
}

export interface DerivativeDiffResponse {
  base_revision_id: number;
  base_revision_number: number;
  target_revision_id: number;
  target_revision_number: number;
  additions: number;
  deletions: number;
  hunks: DerivativeDiffHunk[];
}

/** Client intent: restore a historical revision as a NEW child snapshot. */
export interface DerivativeRollbackBody {
  target_revision_id: number;
  reason?: string | null;
  /** Optimistic-concurrency token; a stale value is rejected (409). */
  base_revision: number;
}

export interface DerivativeRollbackResponse {
  chapter: DerivativeChapterView;
  revision: DerivativeRevisionView;
  target_revision_id: number;
  message: string | null;
}

/** 409 conflict carries the latest head so the stale client can recover. */
export interface DerivativeConflictDetail {
  code?: string;
  message?: string;
  current_revision_number?: number;
  current_checksum?: string;
  current_revision?: DerivativeRevisionView;
}

// ---------------------------------------------------------------------------
// API client
// ---------------------------------------------------------------------------

export const derivativeApi = {
  listProjects: (novelId: number | string) =>
    api.get<DerivativeProjectListResponse>(
      `/novels/${novelId}/derivative-projects`
    ),

  createProject: (novelId: number | string, body: DerivativeProjectCreateBody) =>
    api.post<DerivativeProjectCreateResponse>(
      `/novels/${novelId}/derivative-projects`,
      body
    ),

  /** Explicit fork candidates for the owned novel (read-only picker). */
  listForks: (novelId: number | string) =>
    api.get<CanonForkListResponse>(`/novels/${novelId}/canon-fork`),

  listChapters: (novelId: number | string, projectId: number) =>
    api.get<DerivativeChapterListResponse>(
      `/novels/${novelId}/derivative-projects/${projectId}/chapters`
    ),

  createChapter: (
    novelId: number | string,
    projectId: number,
    body: { title: string; markdown?: string; status?: DerivativeChapterStatus }
  ) =>
    api.post<DerivativeChapterCreateResponse>(
      `/novels/${novelId}/derivative-projects/${projectId}/chapters`,
      body
    ),

  getChapter: (novelId: number | string, projectId: number, chapterId: number) =>
    api.get<DerivativeChapterView>(
      `/novels/${novelId}/derivative-projects/${projectId}/chapters/${chapterId}`
    ),

  patchChapter: (
    novelId: number | string,
    projectId: number,
    chapterId: number,
    body: DerivativeChapterPatchBody
  ) =>
    api.patch<DerivativeChapterView>(
      `/novels/${novelId}/derivative-projects/${projectId}/chapters/${chapterId}`,
      body
    ),

  reorderChapters: (
    novelId: number | string,
    projectId: number,
    chapterIds: number[]
  ) =>
    api.put<DerivativeChapterReorderResponse>(
      `/novels/${novelId}/derivative-projects/${projectId}/chapters/order`,
      { chapter_ids: chapterIds }
    ),

  deleteChapter: (
    novelId: number | string,
    projectId: number,
    chapterId: number
  ) =>
    api.delete(
      `/novels/${novelId}/derivative-projects/${projectId}/chapters/${chapterId}`
    ),

  // ---- Revision surface (Phase 36-03) ----

  /** Conditional-CAS draft autosave; a stale base is a 409 carrying the head. */
  autosaveChapter: (
    novelId: number | string,
    projectId: number,
    chapterId: number,
    body: DerivativeAutosaveBody
  ) =>
    api.post<DerivativeAutosaveResponse>(
      `/novels/${novelId}/derivative-projects/${projectId}/chapters/${chapterId}/autosave`,
      body
    ),

  /** Newest-first append-only history of one chapter. */
  listRevisions: (
    novelId: number | string,
    projectId: number,
    chapterId: number
  ) =>
    api.get<DerivativeRevisionHistoryResponse>(
      `/novels/${novelId}/derivative-projects/${projectId}/chapters/${chapterId}/revisions`
    ),

  /** Read one immutable revision; a foreign/missing revision is an identical 404. */
  getRevision: (
    novelId: number | string,
    projectId: number,
    chapterId: number,
    revisionId: number
  ) =>
    api.get<DerivativeRevisionView>(
      `/novels/${novelId}/derivative-projects/${projectId}/chapters/${chapterId}/revisions/${revisionId}`
    ),

  /** Deterministic canonical-Markdown diff from base to target revision. */
  diffRevisions: (
    novelId: number | string,
    projectId: number,
    chapterId: number,
    baseRevisionId: number,
    targetRevisionId: number
  ) =>
    api.get<DerivativeDiffResponse>(
      `/novels/${novelId}/derivative-projects/${projectId}/chapters/${chapterId}/diff`,
      {
        params: {
          base_revision_id: baseRevisionId,
          target_revision_id: targetRevisionId,
        },
      }
    ),

  /** Restore a target revision as a NEW child; history is never overwritten. */
  rollbackChapter: (
    novelId: number | string,
    projectId: number,
    chapterId: number,
    body: DerivativeRollbackBody
  ) =>
    api.post<DerivativeRollbackResponse>(
      `/novels/${novelId}/derivative-projects/${projectId}/chapters/${chapterId}/rollback`,
      body
    ),
};
