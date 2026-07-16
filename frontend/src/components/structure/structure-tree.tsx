"use client";

import { useMemo, useState } from "react";
import {
  BookMarked,
  ChevronRight,
  Layers,
  Network,
} from "lucide-react";

import { cn } from "@/lib/utils";
import type { StructureSource, StructureTreeNode } from "./structure-types";
import { formatChapterRange, nodeKindLabel } from "./structure-types";

type Props = {
  forest: StructureTreeNode[];
  structureSource: StructureSource;
  selectedId: string | null;
  onSelect: (node: StructureTreeNode) => void;
  className?: string;
};

function kindIcon(kind: string) {
  if (kind === "chapter" || kind === "book") {
    return <BookMarked className="size-3.5 shrink-0 text-muted-foreground" />;
  }
  if (kind === "global_story") {
    return <Layers className="size-3.5 shrink-0 text-violet-600" />;
  }
  return <Network className="size-3.5 shrink-0 text-muted-foreground" />;
}

function TreeRow({
  node,
  depth,
  selectedId,
  onSelect,
  defaultOpen,
}: {
  node: StructureTreeNode;
  depth: number;
  selectedId: string | null;
  onSelect: (node: StructureTreeNode) => void;
  defaultOpen: boolean;
}) {
  const hasChildren = node.children.length > 0;
  const [open, setOpen] = useState(defaultOpen || depth < 2);
  const selected = selectedId === node.id;

  return (
    <li className="list-none">
      <div
        className={cn(
          "group flex items-center gap-0.5 rounded-lg pr-1",
          "motion-transition-feedback",
          selected && "bg-foreground/5 shadow-[inset_2px_0_0_0_hsl(var(--foreground)/0.35)]"
        )}
        style={{ paddingLeft: Math.min(depth, 6) * 12 }}
      >
        {hasChildren ? (
          <button
            type="button"
            aria-label={open ? "折叠" : "展开"}
            aria-expanded={open}
            className="grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-muted motion-transition-feedback"
            onClick={() => setOpen((v) => !v)}
          >
            <ChevronRight
              className={cn(
                "size-3.5 transition-transform motion-duration-standard motion-ease-enter",
                open && "rotate-90"
              )}
            />
          </button>
        ) : (
          <span className="inline-block size-7 shrink-0" aria-hidden />
        )}
        <button
          type="button"
          data-structure-node-id={node.id}
          aria-current={selected ? "true" : undefined}
          onClick={() => onSelect(node)}
          className={cn(
            "flex min-w-0 flex-1 items-center gap-1.5 rounded-lg px-1.5 py-1.5 text-left text-sm",
            "transition-[color,background-color,transform] motion-duration-fast motion-ease-enter",
            selected
              ? "font-medium text-foreground"
              : "text-foreground/90 hover:bg-muted/80 active:scale-[0.99]"
          )}
        >
          {kindIcon(node.kind)}
          <span className="min-w-0 flex-1 truncate">{node.label}</span>
          <span className="shrink-0 text-[10px] text-muted-foreground">
            {formatChapterRange(node.chapterStart, node.chapterEnd)}
          </span>
        </button>
      </div>

      {/*
        Height slide via grid-template-rows (0fr ↔ 1fr) — children stay mounted
        so expand/collapse animates instead of abrupt list length jumps.
      */}
      {hasChildren && (
        <div
          data-testid={`tree-branch-${node.id}`}
          data-open={open ? "true" : "false"}
          className={cn(
            "grid transition-[grid-template-rows] motion-duration-spatial motion-ease-enter",
            open ? "grid-rows-[1fr]" : "grid-rows-[0fr]"
          )}
        >
          <div className="min-h-0 overflow-hidden">
            <ul
              className={cn(
                "m-0 list-none space-y-0.5 p-0",
                "transition-opacity motion-duration-standard motion-ease-enter",
                open ? "opacity-100" : "opacity-0"
              )}
              aria-hidden={!open}
            >
              {node.children.map((child) => (
                <TreeRow
                  key={child.id}
                  node={child}
                  depth={depth + 1}
                  selectedId={selectedId}
                  onSelect={onSelect}
                  defaultOpen={depth < 1}
                />
              ))}
            </ul>
          </div>
        </div>
      )}
    </li>
  );
}

export function StructureTree({
  forest,
  structureSource,
  selectedId,
  onSelect,
  className,
}: Props) {
  const empty = forest.length === 0;
  const sourceHint = useMemo(
    () =>
      structureSource === "narrative_memory"
        ? "叙事记忆树（候选预览）"
        : "章节结构",
    [structureSource]
  );

  return (
    <div className={cn("flex min-h-0 flex-col", className)}>
      <div className="mb-2 flex items-center justify-between gap-2 px-1">
        <p className="text-xs font-medium text-muted-foreground">{sourceHint}</p>
        {structureSource === "narrative_memory" && (
          <span className="rounded-full border border-violet-300/70 bg-violet-50 px-2 py-0.5 text-[10px] text-violet-950">
            L2–L4
          </span>
        )}
      </div>
      {empty ? (
        <p className="rounded-xl border border-dashed px-3 py-6 text-center text-xs text-muted-foreground motion-transition-content">
          暂无结构节点。
        </p>
      ) : (
        <ul
          role="tree"
          aria-label="结构树"
          className="m-0 min-h-0 flex-1 list-none space-y-0.5 overflow-y-auto overflow-x-hidden p-0"
        >
          {forest.map((node) => (
            <TreeRow
              key={node.id}
              node={node}
              depth={0}
              selectedId={selectedId}
              onSelect={onSelect}
              defaultOpen
            />
          ))}
        </ul>
      )}
      {!empty && structureSource === "chapters" && (
        <p className="mt-2 px-1 text-[10px] leading-relaxed text-muted-foreground">
          L3/L4 层（故事弧 / 全书叙事）需叙事记忆候选；当前仅章节坐标。
        </p>
      )}
      {!empty && (
        <p className="sr-only">
          节点类型示例：
          {forest.map((n) => nodeKindLabel(n.kind)).join("、")}
        </p>
      )}
    </div>
  );
}
