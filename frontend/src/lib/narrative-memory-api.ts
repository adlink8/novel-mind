/**
 * Phase 20 Narrative Memory product API client (read-only candidate preview).
 *
 * Always treat responses as candidate_preview — never promote / never resolve
 * an active production pointer. Mirrors GET /api/narrative-memory/* routes.
 */

import { api } from "./api";

// ---------------------------------------------------------------------------
// Types (match backend app/schemas/narrative_memory_product.py)
// ---------------------------------------------------------------------------

export type NmReadiness =
  | "empty"
  | "incomplete"
  | "preview_eligible"
  | "sealed_candidate";

/** Product surface is always candidate preview in Phase 20. */
export type NmPublicationStatus = "candidate_preview";

export type NmBadge = "candidate_preview";

export interface NmVersionListItem {
  version_id: number;
  version_key: string;
  readiness: NmReadiness;
  badge: NmBadge;
  node_counts?: Record<string, number> | null;
  has_manifest: boolean;
  validation_verdict?: string | null;
  created_at?: string | null;
}

export interface NmVersionListResponse {
  novel_id: number;
  versions: NmVersionListItem[];
  publication_status: NmPublicationStatus;
  message?: string | null;
}

export interface NmStructureNode {
  id: number;
  node_key: string;
  node_kind: string;
  display_label?: string | null;
  chapter_start: number;
  chapter_end: number;
  child_ids: number[];
}

export interface NmStructureTreeResponse {
  novel_id: number;
  version_id: number;
  through_chapter: number;
  publication_status: NmPublicationStatus;
  readiness: NmReadiness;
  nodes: NmStructureNode[];
  message?: string | null;
}

export interface NmClaimItem {
  id: number;
  claim_kind: string;
  summary: string;
  text?: string | null;
  typed_payload: Record<string, unknown>;
  uncertainty: string;
  confidence: number;
  visible_from_chapter: number;
  node_id: number;
}

export interface NmClaimsResponse {
  novel_id: number;
  version_id: number;
  node_id: number;
  through_chapter: number;
  publication_status: NmPublicationStatus;
  claims: NmClaimItem[];
  message?: string | null;
}

export interface NmSourceLinkItem {
  id: number;
  claim_id: number;
  source_kind: string;
  hierarchy_build_id: string;
  evidence_node_id: string;
  chapter_number: number;
  source_start: number;
  source_end: number;
  content_hash?: string | null;
  optional_source_ref?: Record<string, unknown> | null;
}

export interface NmSourceLinksResponse {
  novel_id: number;
  version_id: number;
  node_id: number;
  through_chapter: number;
  publication_status: NmPublicationStatus;
  source_links: NmSourceLinkItem[];
  message?: string | null;
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export const narrativeMemoryApi = {
  /** List candidate NM versions (no default active pick). */
  listVersions: (novelId: string | number) =>
    api.get<NmVersionListResponse>(`/narrative-memory/${novelId}/versions`),

  /** Structure tree filtered by through_chapter (spoiler cutoff). */
  getTree: (
    novelId: string | number,
    versionId: number,
    params?: { through_chapter?: number }
  ) =>
    api.get<NmStructureTreeResponse>(
      `/narrative-memory/${novelId}/versions/${versionId}/tree`,
      {
        params: {
          through_chapter: params?.through_chapter,
        },
      }
    ),

  /** Claims on a node visible at through_chapter. */
  getClaims: (
    novelId: string | number,
    versionId: number,
    nodeId: number,
    params?: { through_chapter?: number }
  ) =>
    api.get<NmClaimsResponse>(
      `/narrative-memory/${novelId}/versions/${versionId}/nodes/${nodeId}/claims`,
      {
        params: {
          through_chapter: params?.through_chapter,
        },
      }
    ),

  /** Evidence source links for claims on a node (cutoff-filtered). */
  getSourceLinks: (
    novelId: string | number,
    versionId: number,
    nodeId: number,
    params?: { through_chapter?: number }
  ) =>
    api.get<NmSourceLinksResponse>(
      `/narrative-memory/${novelId}/versions/${versionId}/nodes/${nodeId}/source-links`,
      {
        params: {
          through_chapter: params?.through_chapter,
        },
      }
    ),
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Pick the latest candidate version for preview (highest version_id). Never "active production". */
export function pickLatestPreviewVersion(
  versions: NmVersionListItem[]
): NmVersionListItem | null {
  if (!versions.length) return null;
  return [...versions].sort((a, b) => b.version_id - a.version_id)[0] ?? null;
}

export const NM_PREVIEW_BADGE_LABEL = "叙事记忆候选 · 预览未发布";
export const NM_EMPTY_BANNER =
  "多层叙事记忆未就绪 · 当前为章节结构 + 单层分析";
export const NM_NODE_BADGE_LABEL = "预览·未发布";
