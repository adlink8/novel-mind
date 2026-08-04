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
};
