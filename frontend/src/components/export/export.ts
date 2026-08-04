/**
 * Phase 34-04 — Novel export contract helpers (REQ-VIS-05, D-34-04).
 *
 * Frontend mirror of the backend export surface:
 * - `exportDownloadUrl` / `exportManifestUrl` — owner-scoped download and frozen
 *   manifest read URLs (scope always comes from the novel path, never the body);
 * - `exportFilename` — deterministic download filename mirroring the backend
 *   Content-Disposition (`{slug}-v{textVersionHash8}.{ext}`);
 * - the export anchor status vocabulary + labels mirror the backend
 *   `ExportAnchorStatus` so a future export UI renders render/stale/
 *   asset_missing/invalid exactly once and never invents a URL.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

export type ExportFormat = "markdown" | "html" | "epub";

export const EXPORT_FORMATS: readonly ExportFormat[] = [
  "markdown",
  "html",
  "epub",
] as const;

export const EXPORT_FORMAT_LABELS: Record<ExportFormat, string> = {
  markdown: "Markdown",
  html: "HTML",
  epub: "EPUB",
};

export const EXPORT_FORMAT_EXTENSIONS: Record<ExportFormat, string> = {
  markdown: "md",
  html: "html",
  epub: "epub",
};

/** Mirrors backend `ExportAnchorStatus` (manifest read-side presentation). */
export type ExportAnchorStatus = "render" | "stale" | "asset_missing" | "invalid";

export const EXPORT_ANCHOR_STATUS_LABELS: Record<ExportAnchorStatus, string> = {
  render: "已渲染",
  stale: "待修复",
  asset_missing: "缺失",
  invalid: "已失效",
};

export const EXPORT_ANCHOR_STATUSES: readonly ExportAnchorStatus[] = [
  "render",
  "stale",
  "asset_missing",
  "invalid",
] as const;

/** Owner-scoped export download URL for one frozen manifest format. */
export function exportDownloadUrl(
  novelId: string | number,
  format: ExportFormat
): string {
  return `${API_BASE}/novels/${novelId}/export?format=${format}`;
}

/** Owner-scoped frozen export manifest read URL. */
export function exportManifestUrl(novelId: string | number): string {
  return `${API_BASE}/novels/${novelId}/export/manifest`;
}

/** Deterministic download filename mirroring the backend Content-Disposition. */
export function exportFilename(
  novelTitle: string,
  textVersionHash: string,
  format: ExportFormat
): string {
  const slug = novelTitle
    .replace(/[^\w\u4e00-\u9fff-]+/gu, "-")
    .replace(/^-+|-+$/g, "");
  const stem = slug || "novel";
  return `${stem}-v${textVersionHash.slice(0, 8)}.${EXPORT_FORMAT_EXTENSIONS[format]}`;
}
