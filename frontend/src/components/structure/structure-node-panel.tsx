"use client";

import { NM_NODE_BADGE_LABEL } from "@/lib/narrative-memory-api";
import type { NmClaimItem } from "@/lib/narrative-memory-api";
import {
  formatChapterRange,
  nodeKindLabel,
  type StructureNodeSelection,
  type StructureSource,
} from "./structure-types";

type Props = {
  selected: StructureNodeSelection | null;
  structureSource: StructureSource;
  claims?: NmClaimItem[];
  claimsLoading?: boolean;
  claimsError?: string | null;
  className?: string;
};

export function StructureNodePanel({
  selected,
  structureSource,
  claims = [],
  claimsLoading = false,
  claimsError = null,
  className,
}: Props) {
  if (!selected) {
    return (
      <div
        className={
          className ??
          "rounded-xl border border-dashed bg-card/40 p-3 text-xs text-muted-foreground"
        }
      >
        在左侧选择结构节点，以限定时间线 / 关系 / 线索的章节范围。
      </div>
    );
  }

  const isNm = structureSource === "narrative_memory" && selected.nmNodeId != null;

  return (
    <div
      className={
        className ??
        "rounded-xl border bg-card/70 p-3 text-sm shadow-sm"
      }
      data-testid="structure-node-panel"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
            {nodeKindLabel(selected.kind)}
          </p>
          <p className="mt-0.5 font-serif text-base font-semibold leading-snug">
            {selected.label}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            视图范围：{formatChapterRange(selected.chapterStart, selected.chapterEnd)}
          </p>
        </div>
        {isNm && (
          <span
            data-testid="nm-node-badge"
            className="shrink-0 rounded-full border border-violet-300/80 bg-violet-50 px-2 py-0.5 text-[10px] font-medium text-violet-950"
          >
            {NM_NODE_BADGE_LABEL}
          </span>
        )}
      </div>

      {isNm && (
        <div className="mt-3 border-t pt-2">
          <p className="text-[11px] font-medium text-muted-foreground">
            节点声明（候选）
          </p>
          {claimsLoading && (
            <p className="mt-1 text-xs text-muted-foreground">加载声明…</p>
          )}
          {claimsError && (
            <p className="mt-1 text-xs text-destructive" role="alert">
              {claimsError}
            </p>
          )}
          {!claimsLoading && !claimsError && claims.length === 0 && (
            <p className="mt-1 text-xs text-muted-foreground">
              此节点暂无可见声明。
            </p>
          )}
          {!claimsLoading && claims.length > 0 && (
            <ul className="mt-1.5 max-h-36 space-y-1.5 overflow-y-auto">
              {claims.map((c) => (
                <li
                  key={c.id}
                  className="rounded-lg border bg-background/80 px-2 py-1.5 text-xs"
                >
                  <span className="text-[10px] text-muted-foreground">
                    {c.claim_kind}
                  </span>
                  <p className="mt-0.5 line-clamp-2 text-foreground/90">
                    {c.summary || c.text || "（无摘要）"}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
