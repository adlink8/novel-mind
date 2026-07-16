/**
 * Pure helpers: build StructureTreeNode forests from NM nodes or chapter counts.
 */

import type { NmStructureNode } from "@/lib/narrative-memory-api";
import type { StructureTreeNode } from "./structure-types";

/** Fallback tree when no NM candidate: book root + one node per chapter. */
export function buildChapterFallbackTree(
  chapterCount: number,
  options?: { titles?: Record<number, string> }
): StructureTreeNode[] {
  const n = Math.max(0, Math.floor(chapterCount));
  if (n < 1) {
    return [
      {
        id: "book",
        kind: "book",
        label: "全书结构",
        chapterStart: 1,
        chapterEnd: 1,
        children: [],
      },
    ];
  }

  const children: StructureTreeNode[] = [];
  for (let i = 1; i <= n; i += 1) {
    const title = options?.titles?.[i];
    children.push({
      id: `chapter:${i}`,
      kind: "chapter",
      label: title ? `第 ${i} 章 · ${title}` : `第 ${i} 章`,
      chapterStart: i,
      chapterEnd: i,
      children: [],
    });
  }

  return [
    {
      id: "book",
      kind: "book",
      label: "全书结构",
      chapterStart: 1,
      chapterEnd: n,
      children,
    },
  ];
}

/**
 * Build forest from NM structure nodes (global → arc/volume → chapter_state).
 * Roots are nodes that are not referenced as anyone's child.
 */
export function buildNmStructureTree(nodes: NmStructureNode[]): StructureTreeNode[] {
  if (!nodes.length) return [];

  const byId = new Map<number, NmStructureNode>();
  for (const n of nodes) byId.set(n.id, n);

  const referenced = new Set<number>();
  for (const n of nodes) {
    for (const childId of n.child_ids ?? []) {
      if (byId.has(childId)) referenced.add(childId);
    }
  }

  const toTree = (n: NmStructureNode): StructureTreeNode => {
    const children = (n.child_ids ?? [])
      .map((cid) => byId.get(cid))
      .filter((c): c is NmStructureNode => Boolean(c))
      .map(toTree);

    return {
      id: `nm:${n.id}`,
      kind: n.node_kind,
      label:
        n.display_label?.trim() ||
        defaultNmLabel(n.node_kind, n.chapter_start, n.chapter_end),
      chapterStart: n.chapter_start,
      chapterEnd: n.chapter_end,
      nmNodeId: n.id,
      children,
    };
  };

  const roots = nodes.filter((n) => !referenced.has(n.id));
  // Prefer global_story first when present among roots
  roots.sort((a, b) => kindRank(a.node_kind) - kindRank(b.node_kind));
  return roots.map(toTree);
}

function kindRank(kind: string): number {
  if (kind === "global_story") return 0;
  if (kind === "volume") return 1;
  if (kind === "story_arc") return 2;
  if (kind === "chapter_state") return 3;
  return 9;
}

function defaultNmLabel(
  kind: string,
  chapterStart: number,
  chapterEnd: number
): string {
  const range =
    chapterStart === chapterEnd
      ? `第 ${chapterStart} 章`
      : `第 ${chapterStart}–${chapterEnd} 章`;
  if (kind === "global_story") return `全书叙事 · ${range}`;
  if (kind === "story_arc") return `故事弧 · ${range}`;
  if (kind === "volume") return `卷 · ${range}`;
  if (kind === "chapter_state") return `章状态 · ${range}`;
  return `${kind} · ${range}`;
}

/** Highest preferred root for default selection (L4 → L3 → first). */
export function pickDefaultTreeNode(
  forest: StructureTreeNode[]
): StructureTreeNode | null {
  if (!forest.length) return null;
  const prefer = ["global_story", "volume", "story_arc", "book", "chapter_state", "chapter"];
  for (const kind of prefer) {
    const hit = forest.find((n) => n.kind === kind);
    if (hit) return hit;
  }
  return forest[0] ?? null;
}

export function treeNodeToSelection(
  node: StructureTreeNode
): import("./structure-types").StructureNodeSelection {
  return {
    id: node.id,
    kind: node.kind,
    chapterStart: node.chapterStart,
    chapterEnd: node.chapterEnd,
    label: node.label,
    nmNodeId: node.nmNodeId,
  };
}
