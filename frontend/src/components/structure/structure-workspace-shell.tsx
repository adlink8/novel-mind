"use client";

import { useState } from "react";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";

import {
  NM_EMPTY_BANNER,
  NM_PREVIEW_BADGE_LABEL,
} from "@/lib/narrative-memory-api";
import type { NmClaimItem, NmSourceLinkItem } from "@/lib/narrative-memory-api";
import { cn } from "@/lib/utils";
import { StructureNodePanel } from "./structure-node-panel";
import { StructureTree } from "./structure-tree";
import type {
  StructureNodeSelection,
  StructureSource,
  StructureTreeNode,
} from "./structure-types";
import { formatChapterRange } from "./structure-types";

/** Open rail width. */
const RAIL_OPEN_PX = 280;
const RAIL_CLOSED_PX = 44;

/**
 * Structure rail viewport height — fixed, not content-sized.
 * 500 chapters scroll inside the tree pane; they must not lengthen the page.
 */
const RAIL_VIEWPORT_CLASS =
  "h-[min(70vh,36rem)] max-h-[min(70vh,36rem)] min-h-[16rem]";

type Props = {
  structureSource: StructureSource;
  forest: StructureTreeNode[];
  selected: StructureNodeSelection | null;
  onSelect: (node: StructureTreeNode) => void;
  claims?: NmClaimItem[];
  claimsLoading?: boolean;
  claimsError?: string | null;
  selectedClaimId?: number | null;
  onClaimSelect?: (claim: NmClaimItem) => void;
  sourceLinks?: NmSourceLinkItem[];
  sourceLinksLoading?: boolean;
  sourceLinksError?: string | null;
  novelId?: string;
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
  selectedClaimId,
  onClaimSelect,
  sourceLinks,
  sourceLinksLoading,
  sourceLinksError,
  novelId,
  children,
  className,
}: Props) {
  const [treeOpen, setTreeOpen] = useState(true);

  const scopeLabel = selected
    ? formatChapterRange(selected.chapterStart, selected.chapterEnd)
    : null;

  return (
    <div className={className ?? "grid gap-3"}>
      {structureSource === "chapters" ? (
        <p
          data-testid="nm-empty-banner"
          className="rounded-xl border border-amber-300/70 bg-amber-50 px-3 py-2 text-xs text-amber-950 motion-transition-content"
        >
          {NM_EMPTY_BANNER}
        </p>
      ) : (
        <p
          data-testid="nm-preview-badge"
          className="rounded-xl border border-violet-300/70 bg-violet-50 px-3 py-2 text-xs text-violet-950 motion-transition-content"
        >
          <span className="font-medium">{NM_PREVIEW_BADGE_LABEL}</span>
          <span className="ml-2 text-violet-900/80">
            只读候选，不会写入生产活跃指针
          </span>
        </p>
      )}

      {/*
        items-start: rail height is independent of facet column.
        Rail uses fixed viewport; tree list scrolls vertically inside.
      */}
      <div
        data-testid="structure-workspace-track"
        className="flex items-start gap-3"
      >
        <div
          data-testid="structure-rail"
          data-open={treeOpen ? "true" : "false"}
          className={cn(
            "relative shrink-0 overflow-hidden",
            "transition-[width] motion-duration-spatial motion-ease-enter",
            RAIL_VIEWPORT_CLASS
          )}
          style={{
            width: treeOpen ? RAIL_OPEN_PX : RAIL_CLOSED_PX,
          }}
        >
          {/* Collapsed strip */}
          <div
            className={cn(
              "absolute inset-y-0 left-0 z-10 flex w-11 flex-col items-center border bg-card/80 py-2 shadow-sm",
              "rounded-2xl motion-transition-spatial",
              treeOpen
                ? "pointer-events-none -translate-x-1 opacity-0"
                : "translate-x-0 opacity-100"
            )}
            aria-hidden={treeOpen}
          >
            <button
              type="button"
              className="inline-flex flex-col items-center gap-1 rounded-xl px-1.5 py-2 text-[10px] text-muted-foreground hover:bg-muted hover:text-foreground motion-transition-feedback"
              onClick={() => setTreeOpen(true)}
              aria-label="展开结构树"
              tabIndex={treeOpen ? -1 : 0}
            >
              <PanelLeftOpen className="size-4" />
              <span
                className="max-h-24 overflow-hidden text-ellipsis"
                style={{ writingMode: "vertical-rl" }}
              >
                结构
                {selected
                  ? ` · ${formatChapterRange(selected.chapterStart, selected.chapterEnd)}`
                  : ""}
              </span>
            </button>
          </div>

          <aside
            aria-label="结构导航"
            aria-hidden={!treeOpen}
            data-testid="structure-rail-panel"
            className={cn(
              "flex w-[280px] flex-col gap-2 overflow-hidden rounded-2xl border bg-card/50 p-2 sm:p-3",
              RAIL_VIEWPORT_CLASS,
              "motion-transition-spatial",
              treeOpen
                ? "translate-x-0 opacity-100"
                : "pointer-events-none -translate-x-3 opacity-0 motion-closing"
            )}
          >
            <div className="flex shrink-0 items-center justify-between gap-2 px-0.5">
              <h2 className="font-serif text-sm font-semibold">结构</h2>
              <button
                type="button"
                className="inline-flex items-center gap-1 rounded-lg border bg-background px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted motion-transition-feedback"
                onClick={() => setTreeOpen(false)}
                aria-label="收起结构树"
                tabIndex={treeOpen ? 0 : -1}
              >
                <PanelLeftClose className="size-3.5" />
                收起
              </button>
            </div>

            {/* Tree fills remaining height and scrolls inside */}
            <StructureTree
              forest={forest}
              structureSource={structureSource}
              selectedId={selected?.id ?? null}
              onSelect={onSelect}
              className="min-h-0 flex-1 overflow-hidden"
            />

            {/* Detail strip: capped height + own scroll so claims don't grow rail */}
            <div
              data-testid="structure-node-panel-scroll"
              className="max-h-[9.5rem] shrink-0 overflow-y-auto overscroll-contain border-t border-border/60 pt-2"
            >
              <StructureNodePanel
                selected={selected}
                structureSource={structureSource}
                claims={claims}
                claimsLoading={claimsLoading}
                claimsError={claimsError}
                selectedClaimId={selectedClaimId}
                onClaimSelect={onClaimSelect}
                sourceLinks={sourceLinks}
                sourceLinksLoading={sourceLinksLoading}
                sourceLinksError={sourceLinksError}
                novelId={novelId}
              />
            </div>
          </aside>
        </div>

        <div className="flex min-w-0 flex-1 flex-col gap-2 motion-transition-content">
          <p
            data-testid="structure-scope-label"
            className="text-xs text-muted-foreground"
          >
            视图范围：
            {scopeLabel ? (
              <>
                <span className="font-medium text-foreground">{scopeLabel}</span>
                {selected && (
                  <>
                    <span className="mx-1.5 text-border">·</span>
                    <span className="truncate">{selected.label}</span>
                  </>
                )}
              </>
            ) : (
              <span className="text-muted-foreground">未选择结构节点</span>
            )}
          </p>
          {children}
        </div>
      </div>
    </div>
  );
}
