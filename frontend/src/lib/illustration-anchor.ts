/**
 * Phase 34-02 — Illustration anchor reader contract (REQ-VIS-05, D-34-01..D-34-04).
 *
 * Reader-safe presentation of approved illustrations depends on a strict typed
 * contract with the backend `AnchorView` envelope (backend/app/schemas/
 * illustration_anchor.py): owner/novel/chapter scoped published anchors carry an
 * immutable source span, the frozen excerpt hash, the frozen chapter content
 * hash and the approved published AssetRevision. This module owns:
 *
 * - the client-side mirror of `AnchorView` / `AnchorStatus` and the owner-scoped
 *   read API (`GET /novels/{id}/illustration-anchors`);
 * - `verifyAnchorAgainstChapter`, a read-side hash/range re-verification that
 *   never renders a stale anchor as valid: a changed chapter content, a source
 *   span that no longer replays the excerpt, or a non-`valid` status all fail
 *   closed to an explicit `needs_repair` / `invalid` presentation (D-34-01);
 * - the owner-scoped asset bytes URL builder for the approved published
 *   AssetRevision (raw storage paths are never exposed).
 *
 * Nothing here publishes or promotes a candidate; only a server-published
 * `valid` anchor with a replaying hash may render an approved asset.
 */

import { api } from "./api";
import {
  codePointLength,
  codePointSlice,
  sha256Hex,
} from "./reader-selection";

// ---------------------------------------------------------------------------
// Types (mirror backend AnchorStatus / AnchorView read envelope)
// ---------------------------------------------------------------------------

export type IllustrationAnchorStatus =
  | "proposed"
  | "pending_approval"
  | "valid"
  | "needs_repair"
  | "invalid";

export interface IllustrationAnchorView {
  id: number;
  owner_id: number;
  novel_id: number;
  chapter_id: number;
  chapter_number: number;
  anchor_key: string;
  proposal_id: number;
  source_snapshot_id: string;
  source_snapshot_hash: string;
  paragraph_start: number | null;
  paragraph_end: number | null;
  source_start: number;
  source_end: number;
  excerpt: string;
  anchor_hash: string;
  chapter_content_hash: string;
  published_asset_revision_id: number;
  publish_manifest_hash: string;
  approval_request_id: number;
  status: IllustrationAnchorStatus;
  caption: string;
  alt_text: string;
  citation: string;
  approved_by: string | null;
  approved_at: string | null;
}

export interface IllustrationAnchorListResponse {
  items: IllustrationAnchorView[];
  total: number;
}

export const ILLUSTRATION_ANCHOR_STATUSES: readonly IllustrationAnchorStatus[] = [
  "proposed",
  "pending_approval",
  "valid",
  "needs_repair",
  "invalid",
] as const;

// ---------------------------------------------------------------------------
// Owner-scoped read API (published anchors are the only reader-visible surface)
// ---------------------------------------------------------------------------

export const illustrationAnchorApi = {
  /** Published reader/export-visible anchors for the caller's novel. */
  list: (novelId: string | number) =>
    api.get<IllustrationAnchorListResponse>(
      `/novels/${novelId}/illustration-anchors`
    ),
  get: (novelId: string | number, anchorId: number) =>
    api.get<IllustrationAnchorView>(
      `/novels/${novelId}/illustration-anchors/${anchorId}`
    ),
};

/**
 * Owner-scoped asset bytes URL for an approved published AssetRevision.
 * Raw storage paths are never exposed; the backend serves owner-scoped bytes.
 *
 * Returns a bare path (no `/api` prefix): callers resolve it through the shared
 * axios client (`frontend/src/lib/api.ts`), whose baseURL already carries the
 * `/api` (or `NEXT_PUBLIC_API_URL`) prefix. Prefixed paths here would double the
 * prefix and 404.
 */
export function illustrationAssetBytesUrl(
  novelId: string | number,
  assetRevisionId: number
): string {
  return `/novels/${novelId}/illustrations/assets/${assetRevisionId}/bytes`;
}

// ---------------------------------------------------------------------------
// Read-side verification (fail closed; a stale anchor is never "valid")
// ---------------------------------------------------------------------------

export type AnchorVerificationReasonCode =
  | "ok"
  | "not_valid_status"
  | "malformed_hash"
  | "anchor_hash_mismatch"
  | "chapter_content_hash_mismatch"
  | "source_range_out_of_bounds"
  | "source_range_mismatch";

export interface AnchorVerificationResult {
  ok: boolean;
  status: IllustrationAnchorStatus;
  reasonCode: AnchorVerificationReasonCode;
  detail: string;
}

const HEX64 = /^[0-9a-f]{64}$/;

/**
 * D-34-01 read-side gate: an approved asset renders only when the anchor is
 * server-published `valid` and its hash/range/version replay against the
 * current chapter content. A mismatch is explicit (`needs_repair`/`invalid`)
 * and never silently relocates to a nearby paragraph.
 */
export async function verifyAnchorAgainstChapter(
  anchor: IllustrationAnchorView,
  chapterContent: string
): Promise<AnchorVerificationResult> {
  if (anchor.status !== "valid") {
    return {
      ok: false,
      status: anchor.status,
      reasonCode: "not_valid_status",
      detail: "只有服务端发布的 valid anchor 才能渲染已批准插图",
    };
  }
  if (!HEX64.test(anchor.anchor_hash) || !HEX64.test(anchor.chapter_content_hash)) {
    return {
      ok: false,
      status: "invalid",
      reasonCode: "malformed_hash",
      detail: "anchor 哈希必须是 64 位十六进制",
    };
  }
  const [anchorHash, contentHash] = await Promise.all([
    sha256Hex(anchor.excerpt),
    sha256Hex(chapterContent),
  ]);
  if (anchorHash !== anchor.anchor_hash) {
    return {
      ok: false,
      status: "invalid",
      reasonCode: "anchor_hash_mismatch",
      detail: "anchor_hash 无法从冻结的 excerpt 重放",
    };
  }
  if (contentHash !== anchor.chapter_content_hash) {
    return {
      ok: false,
      status: "needs_repair",
      reasonCode: "chapter_content_hash_mismatch",
      detail: "章节内容已变更，anchor 已过期且不会自动迁移",
    };
  }
  const cpLen = codePointLength(chapterContent);
  if (
    anchor.source_start < 0 ||
    anchor.source_end > cpLen ||
    anchor.source_end <= anchor.source_start
  ) {
    return {
      ok: false,
      status: "needs_repair",
      reasonCode: "source_range_out_of_bounds",
      detail: "anchor 区间超出当前章节内容",
    };
  }
  const actual = codePointSlice(
    chapterContent,
    anchor.source_start,
    anchor.source_end
  );
  if (actual !== anchor.excerpt) {
    return {
      ok: false,
      status: "needs_repair",
      reasonCode: "source_range_mismatch",
      detail: "源码区间不再与 excerpt 匹配，anchor 已过期",
    };
  }
  return { ok: true, status: "valid", reasonCode: "ok", detail: "" };
}

// ---------------------------------------------------------------------------
// Presentation labels (stable, tested)
// ---------------------------------------------------------------------------

export const ANCHOR_STATUS_LABELS: Record<IllustrationAnchorStatus, string> = {
  proposed: "提案中",
  pending_approval: "待审批",
  valid: "有效",
  needs_repair: "待修复",
  invalid: "已失效",
};

export const ANCHOR_REASON_LABELS: Record<AnchorVerificationReasonCode, string> = {
  ok: "",
  not_valid_status: "仅已发布的有效插图可展示",
  malformed_hash: "anchor 哈希格式无效",
  anchor_hash_mismatch: "插图锚点哈希与原文不一致",
  chapter_content_hash_mismatch: "章节正文已变更，插图待修复",
  source_range_out_of_bounds: "插图锚点区间已超出当前正文",
  source_range_mismatch: "插图锚点区间与正文不再匹配",
};
