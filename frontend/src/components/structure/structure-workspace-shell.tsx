"use client";

import { useState } from "react";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";

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

const RAIL_OPEN_PX = 272;
const RAIL_CLOSED_PX = 40;

/** Shared work-surface height: left tree and right facets match exactly. */
const WORK_SURFACE_H =
  "h-[min(72vh,40rem)] max-h-[min(72vh,40rem)] min-h-[20rem]";

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
    <div className={cn("grid gap-0", className)}>
      {/* Hidden source markers for tests / a11y; no visible explainer chrome */}
      <span className="sr-only" data-testid="nm-empty-banner" hidden={structureSource !== "chapters"}>
        {structureSource === "chapters" ? "chapters" : ""}
      </span>
      <span
        className="sr-only"
        data-testid="nm-preview-badge"
        hidden={structureSource !== "narrative_memory"}
      >
        {structureSource === "narrative_memory" ? "candidate_preview" : ""}
      </span>

      {/*
        Single fixed-height surface: left rail + right pane share height.
        Long structure lists and long facet content both scroll inside.
      */}
      <div
        data-testid="structure-workspace-track"
        className={cn(
          "flex overflow-hidden rounded-2xl border border-border/50 bg-card/40",
          WORK_SURFACE_H
        )}
      >
        <div
          data-testid="structure-rail"
          data-open={treeOpen ? "true" : "false"}
          className={cn(
            "relative h-full shrink-0 overflow-hidden border-r border-border/40 bg-muted/20",
            "transition-[width] motion-duration-spatial motion-ease-enter"
          )}
          style={{
            width: treeOpen ? RAIL_OPEN_PX : RAIL_CLOSED_PX,
          }}
        >
          <div
            className={cn(
              "absolute inset-y-0 left-0 z-10 flex w-10 flex-col items-center bg-muted/30 py-3",
              "motion-transition-spatial",
              treeOpen
                ? "pointer-events-none -translate-x-1 opacity-0"
                : "translate-x-0 opacity-100"
            )}
            aria-hidden={treeOpen}
          >
            <button
              type="button"
              className="inline-flex flex-col items-center gap-1 rounded-lg px-1 py-2 text-[10px] text-muted-foreground hover:bg-background/70 hover:text-foreground motion-transition-feedback"
              onClick={() => setTreeOpen(true)}
              aria-label="展开结构树"
              tabIndex={treeOpen ? -1 : 0}
            >
              <PanelLeftOpen className="size-4" />
              <span
                className="max-h-28 overflow-hidden text-ellipsis"
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
              "flex h-full w-[272px] flex-col overflow-hidden p-2.5 sm:p-3",
              "motion-transition-spatial",
              treeOpen
                ? "translate-x-0 opacity-100"
                : "pointer-events-none -translate-x-3 opacity-0 motion-closing"
            )}
          >
            <div className="flex shrink-0 items-center justify-between gap-2 pb-2">
              <h2 className="font-serif text-sm font-semibold tracking-tight">
                结构
              </h2>
              <button
                type="button"
                className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-muted-foreground hover:bg-background/80 hover:text-foreground motion-transition-feedback"
                onClick={() => setTreeOpen(false)}
                aria-label="收起结构树"
                tabIndex={treeOpen ? 0 : -1}
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
              className="min-h-0 flex-1 overflow-hidden"
            />

            <div
              data-testid="structure-node-panel-scroll"
              className="mt-2 max-h-[8.5rem] shrink-0 overflow-y-auto overscroll-contain border-t border-border/30 pt-2"
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

        <div className="flex h-full min-w-0 flex-1 flex-col overflow-hidden">
          <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-border/40 px-3 py-2 sm:px-4">
            <p
              data-testid="structure-scope-label"
              className="min-w-0 text-xs text-muted-foreground"
            >
              视图范围：
              {scopeLabel ? (
                <>
                  <span className="font-medium text-foreground">
                    {scopeLabel}
                  </span>
                  {selected && (
                    <span className="ml-1.5 truncate text-muted-foreground">
                      {selected.label}
                    </span>
                  )}
                </>
              ) : (
                <span>未选择节点</span>
              )}
            </p>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-3 sm:p-4">
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}
