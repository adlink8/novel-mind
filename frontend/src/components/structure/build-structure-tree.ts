/**
 * Pure helpers: build StructureTreeNode forests from NM nodes or chapter counts.
 */

import type { NmStructureNode } from "@/lib/narrative-memory-api";
import type { StructureTreeNode } from "./structure-types";

const CHAPTER_MARKER =
  /^第\s*[零一二三四五六七八九十百千万两\d]+\s*[章节回卷集篇部幕]\s*/;

/** 去掉标题行首的章节序号标记（"第1章 凯撒" → "凯撒"；无标记则原样）。 */
export function cleanChapterTitle(raw: string): string {
  return raw
    .trim()
    .replace(CHAPTER_MARKER, "")
    .replace(/^[:：·\-—–.\s]+/, "")
    .trim();
}

/**
 * 章节节点统一标签：有真实章节名则「第 X 章 · 名称」，否则「第 X 章」。
 * 清理后若仍只是个章节号（如「第三章」），视为无名。
 */
export function formatChapterLabel(
  chapterNumber: number,
  rawTitle?: string | null
): string {
  const clean = rawTitle ? cleanChapterTitle(rawTitle) : "";
  // 清理后仍只是个章节号（如「第三章」），视为无名
  const bareMarker =
    /^第\s*[零一二三四五六七八九十百千万两\d]+\s*[章节回卷集篇部幕]$/.test(
      clean
    );
  const name = bareMarker ? "" : clean;
  return name ? `第 ${chapterNumber} 章 · ${name}` : `第 ${chapterNumber} 章`;
}

/** LLM 缺省时后端会把 stage_key 存进 display_label（"chapter_state:12"），识别为无标签。 */
function isStageKeyLabel(label: string): boolean {
  return /^[a-z_]+:\d+(-\d+)?$/.test(label);
}

/** Fallback tree when no NM candidate: book root + one node per chapter. */
export function buildChapterFallbackTree(
  chapterCount: number,
  options?: {
    titles?: Record<number, string>;
    chapters?: ReadonlyArray<{
      chapter_number: number;
      title?: string | null;
    }>;
  }
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

  const actualChapters = [...(options?.chapters ?? [])]
    .filter(
      (chapter) =>
        Number.isInteger(chapter.chapter_number) && chapter.chapter_number >= 1
    )
    .sort((a, b) => a.chapter_number - b.chapter_number);
  const chapterNumbers = actualChapters.length
    ? [...new Set(actualChapters.map((chapter) => chapter.chapter_number))]
    : Array.from({ length: n }, (_, index) => index + 1);
  const actualTitles = new Map(
    actualChapters.map((chapter) => [chapter.chapter_number, chapter.title])
  );
  const children: StructureTreeNode[] = chapterNumbers.map((chapterNumber) => ({
    id: `chapter:${chapterNumber}`,
    kind: "chapter",
    label: formatChapterLabel(
      chapterNumber,
      actualTitles.get(chapterNumber) ?? options?.titles?.[chapterNumber]
    ),
    chapterStart: chapterNumber,
    chapterEnd: chapterNumber,
    children: [],
  }));
  const firstChapter = chapterNumbers[0];
  const lastChapter = chapterNumbers[chapterNumbers.length - 1];

  return [
    {
      id: "book",
      kind: "book",
      label: "全书结构",
      chapterStart: firstChapter,
      chapterEnd: lastChapter,
      children,
    },
  ];
}

/**
 * Build forest from NM structure nodes (global → arc/volume → chapter_state).
 * Roots are nodes that are not referenced as anyone's child.
 *
 * chapter_state（单章）节点优先使用 chapters 表的真实章节名 ——
 * LLM 生成的 display_label 有的带章节名有的只有「第X章」，展示不一致；
 * 以原文标题为准保证每章都有名。无标题时回退 display_label → 默认标签。
 */
export function buildNmStructureTree(
  nodes: NmStructureNode[],
  options?: { chapterTitles?: Record<number, string> }
): StructureTreeNode[] {
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

    const rawLabel = n.display_label?.trim() || "";
    const modelLabel = rawLabel && !isStageKeyLabel(rawLabel) ? rawLabel : "";
    const realTitle =
      n.node_kind === "chapter_state" && n.chapter_start === n.chapter_end
        ? options?.chapterTitles?.[n.chapter_start]
        : undefined;
    const label = realTitle
      ? formatChapterLabel(n.chapter_start, realTitle)
      : modelLabel || defaultNmLabel(n.node_kind, n.chapter_start, n.chapter_end);

    return {
      id: `nm:${n.id}`,
      kind: n.node_kind,
      label,
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

/** Depth-first find by NM node id (preserve selection after tree re-fetch). */
export function findTreeNodeByNmId(
  forest: StructureTreeNode[],
  nmNodeId: number
): StructureTreeNode | null {
  for (const node of forest) {
    if (node.nmNodeId === nmNodeId) return node;
    const hit = findTreeNodeByNmId(node.children, nmNodeId);
    if (hit) return hit;
  }
  return null;
}

/** Depth-first find by UI id (`chapter:3` | `nm:12` | `book`). */
export function findTreeNodeById(
  forest: StructureTreeNode[],
  id: string
): StructureTreeNode | null {
  for (const node of forest) {
    if (node.id === id) return node;
    const hit = findTreeNodeById(node.children, id);
    if (hit) return hit;
  }
  return null;
}
