"use client";

/**
 * Phase 30 Visual Bible — Reference asset status (REQ-VIS-01, D-30-03/D-30-04).
 *
 * Renders reference asset rights/provenance state from the server envelope:
 *
 * - four rights statuses (unreviewed / cleared / pending / denied) are visible
 *   and color-coded;
 * - an asset is shown as approved only when the server marks ``approved ===
 *   true``; every other combination renders the explicit 未批准 state so a
 *   generated/reference asset can never silently become canon;
 * - denied rights are surfaced as a hard block, not a silent skip.
 */

import type {
  VisualReferenceAssetView,
  VisualRightsStatus,
} from "@/lib/visual-bible-api";
import { shortVisualHash } from "@/lib/visual-bible-api";
import { cn } from "@/lib/utils";

export const RIGHTS_STATUS_LABEL: Record<VisualRightsStatus, string> = {
  unreviewed: "未审查",
  cleared: "已许可",
  pending: "待定",
  denied: "已拒绝",
};

export const RIGHTS_STATUS_CLASS: Record<VisualRightsStatus, string> = {
  unreviewed: "border-border bg-muted text-muted-foreground",
  cleared: "border-emerald-500/40 bg-emerald-500/10 text-emerald-800",
  pending: "border-amber-500/40 bg-amber-500/10 text-amber-800",
  denied: "border-rose-500/40 bg-rose-500/10 text-rose-800",
};

export type ReferenceAssetStatusProps = {
  assets: VisualReferenceAssetView[];
  className?: string;
};

export function ReferenceAssetStatus({
  assets,
  className,
}: ReferenceAssetStatusProps) {
  return (
    <section
      data-testid="visual-bible-reference-assets"
      className={cn("space-y-2", className)}
    >
      <p className="text-xs font-medium text-muted-foreground">
        参考素材 · rights/provenance
      </p>
      {assets.length === 0 ? (
        <p data-testid="visual-bible-assets-empty" className="text-xs text-muted-foreground">
          暂无参考素材
        </p>
      ) : (
        <ul className="space-y-1.5">
          {assets.map((asset) => (
            <li
              key={asset.asset_key}
              data-testid="visual-bible-asset"
              data-asset-key={asset.asset_key}
              data-approved={asset.approved}
              data-rights={asset.rights_status}
              className="rounded-lg border border-border/60 bg-card px-2 py-1.5"
            >
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-xs font-medium">{asset.asset_key}</span>
                <span
                  data-testid="visual-bible-asset-rights"
                  data-rights={asset.rights_status}
                  className={cn(
                    "inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] font-medium",
                    RIGHTS_STATUS_CLASS[asset.rights_status]
                  )}
                >
                  {RIGHTS_STATUS_LABEL[asset.rights_status]}
                </span>
              </div>
              <div className="mt-0.5 flex flex-wrap gap-1.5 font-mono text-[10px] text-muted-foreground">
                <span>{asset.mime_type}</span>
                <span data-testid="visual-bible-asset-hash">
                  {shortVisualHash(asset.bytes_hash)}
                </span>
              </div>
              {asset.approved && asset.rights_status === "cleared" ? (
                <span
                  data-testid="visual-bible-asset-approved"
                  className="mt-1 inline-flex items-center rounded-full border border-emerald-500/40 bg-emerald-500/10 px-1.5 py-0.5 text-[10px] font-medium text-emerald-800"
                >
                  已批准 · 可进入正典
                </span>
              ) : (
                <span
                  data-testid="visual-bible-asset-not-approved"
                  className="mt-1 inline-flex items-center rounded-full border border-border bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
                >
                  未批准 — 不进入正典
                </span>
              )}
              {asset.rights_status === "denied" ? (
                <p className="mt-1 text-[10px] text-rose-800">
                  权利未获授权 — 禁止使用该素材
                </p>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
