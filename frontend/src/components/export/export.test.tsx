import { describe, expect, it } from "vitest";

import {
  EXPORT_ANCHOR_STATUSES,
  EXPORT_ANCHOR_STATUS_LABELS,
  EXPORT_FORMATS,
  EXPORT_FORMAT_LABELS,
  exportDownloadUrl,
  exportFilename,
  exportManifestUrl,
} from "./export";

const H = (n: number) => String(n).repeat(64);

describe("export download/status contract (Phase 34-04, D-34-04)", () => {
  it("provides exactly the three formats with stable labels and extensions", () => {
    expect(EXPORT_FORMATS).toEqual(["markdown", "html", "epub"]);
    expect(Object.keys(EXPORT_FORMAT_LABELS).sort()).toEqual(
      [...EXPORT_FORMATS].sort()
    );
    expect(EXPORT_FORMAT_LABELS).toMatchObject({
      markdown: "Markdown",
      html: "HTML",
      epub: "EPUB",
    });
  });

  it("builds owner-scoped download and manifest URLs", () => {
    expect(exportDownloadUrl(11, "markdown")).toBe(
      "/api/novels/11/export?format=markdown"
    );
    expect(exportDownloadUrl(11, "epub")).toBe("/api/novels/11/export?format=epub");
    expect(exportManifestUrl(11)).toBe("/api/novels/11/export/manifest");
  });

  it("builds a deterministic download filename from title/version/format", () => {
    const hash = H(1);
    expect(exportFilename("雾城夜读", hash, "epub")).toBe(
      `雾城夜读-v${hash.slice(0, 8)}.epub`
    );
    expect(exportFilename("The Lantern Novel", hash, "markdown")).toBe(
      `The-Lantern-Novel-v${hash.slice(0, 8)}.md`
    );
    expect(exportFilename("", hash, "html")).toBe(
      `novel-v${hash.slice(0, 8)}.html`
    );
  });

  it("mirrors the backend ExportAnchorStatus vocabulary with stable labels", () => {
    expect(EXPORT_ANCHOR_STATUSES).toEqual([
      "render",
      "stale",
      "asset_missing",
      "invalid",
    ]);
    expect(EXPORT_ANCHOR_STATUS_LABELS).toMatchObject({
      render: "已渲染",
      stale: "待修复",
      asset_missing: "缺失",
      invalid: "已失效",
    });
  });
});
