"use client";

import { useState } from "react";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";

import {
  NM_EMPTY_BANNER,
  NM_PREVIEW_BADGE_LABEL,
} from "@/lib/narrative-memory-api";
import type { NmClaimItem } from "@/lib/narrative-memory-api";
import { StructureNodePanel } from "./structure-node-panel";
import { StructureTree } from "./structure-tree";
import type {
  StructureNodeSelection,
  StructureSource,
  StructureTreeNode,
} from "./structure-types";
import { formatChapterRange } from "./structure-types";

type Props = {
  structureSource: StructureSource;
  forest: StructureTreeNode[];
  selected: StructureNodeSelection | null;
  onSelect: (node: StructureTreeNode) => void;
  claims?: NmClaimItem[];
  claimsLoading?: boolean;
  claimsError?: string | null;
  /** Center facet area (tabs + workspaces). */
  children: React.ReactNode;
  className?: string;
};

export function StructureWorkspaceShell({
  structureSource,
  forest,
  selected,
  onSelect,
  claims,
  claimsLoading,
  claimsError,
  children,
  className,
}: Props) {
  const [treeOpen, setTreeOpen] = useState(true);

  return (
    <div className={className ?? "grid gap-3"}>
      {/* Status banners — honest empty / candidate preview */}
      {structureSource === "chapters" ? (
        <p
          data-testid="nm-empty-banner"
          className="rounded-xl border border-amber-300/70 bg-amber-50 px-3 py-2 text-xs text-amber-950"
        >
          {NM_EMPTY_BANNER}
        </p>
      ) : (
        <p
          data-testid="nm-preview-badge"
          className="rounded-xl border border-violet-300/70 bg-violet-50 px-3 py-2 text-xs text-violet-950"
        >
          <span className="font-medium">{NM_PREVIEW_BADGE_LABEL}</span>
          <span className="ml-2 text-violet-900/80">
            只读候选，不会写入生产活跃指针
          </span>
        </p>
      )}

      {selected && (
        <p
          data-testid="structure-scope-label"
          className="text-xs text-muted-foreground"
        >
          视图范围：
          <span className="font-medium text-foreground">
            {formatChapterRange(selected.chapterStart, selected.chapterEnd)}
          </span>
          <span className="mx-1.5 text-border">·</span>
          <span className="truncate">{selected.label}</span>
        </p>
      )}

      <div
        className={
          treeOpen
            ? "grid gap-3 lg:grid-cols-[minmax(220px,280px)_minmax(0,1fr)]"
            : "grid gap-3"
        }
      >
        {/* Left structure spine */}
        {treeOpen ? (
          <aside
            aria-label="结构导航"
            className="flex min-h-[280px] flex-col gap-2 rounded-2xl border bg-card/50 p-2 sm:p-3 lg:min-h-[420px]"
          >
            <div className="flex items-center justify-between gap-2 px-0.5">
              <h2 className="font-serif text-sm font-semibold">结构</h2>
              <button
                type="button"
                className="inline-flex items-center gap-1 rounded-lg border bg-background px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted"
                onClick={() => setTreeOpen(false)}
                aria-label="收起结构树"
              >
                <PanelLeftClose className="size-3.5" />
                收起
              </button>
            </div>
            <StructureTree
              forest={forest}
              structureSource={structureSource}
              selectedId={selected?.id ?? null}
              onSelect={onSelect}
              className="min-h-0 flex-1"
            />
            <StructureNodePanel
              selected={selected}
              structureSource={structureSource}
              claims={claims}
              claimsLoading={claimsLoading}
              claimsError={claimsError}
            />
          </aside>
        ) : (
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="inline-flex items-center gap-1.5 rounded-xl border bg-card px-3 py-2 text-xs hover:bg-muted"
              onClick={() => setTreeOpen(true)}
              aria-label="展开结构树"
            >
              <PanelLeftOpen className="size-3.5" />
              展开结构
              {selected && (
                <span className="text-muted-foreground">
                  · {formatChapterRange(selected.chapterStart, selected.chapterEnd)}
                </span>
              )}
            </button>
          </div>
        )}

        {/* Center facets */}
        <div className="min-w-0">{children}</div>
      </div>
    </div>
  );
}
