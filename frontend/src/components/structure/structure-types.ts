/**
 * Structure Workspace selection model (Phase 20).
 * Structure is the spine; timeline / relationships / clues are facets.
 */

export type StructureSource = "chapters" | "narrative_memory";

export type StructureNodeKind =
  | "book"
  | "chapter"
  | "global_story"
  | "story_arc"
  | "volume"
  | "chapter_state"
  | string;

export type StructureNodeSelection = {
  /** Stable UI key: `chapter:3` | `nm:12` | `book` */
  id: string;
  kind: StructureNodeKind;
  chapterStart: number;
  chapterEnd: number;
  label: string;
  nmNodeId?: number;
};

export type StructureTreeNode = {
  id: string;
  kind: StructureNodeKind;
  label: string;
  chapterStart: number;
  chapterEnd: number;
  nmNodeId?: number;
  children: StructureTreeNode[];
};

/** True when selection spans a bounded chapter range (always for real nodes). */
export function hasChapterScope(node: StructureNodeSelection | null): boolean {
  return Boolean(node && node.chapterStart >= 1 && node.chapterEnd >= node.chapterStart);
}

/**
 * Client-side event filter for timeline when structure selection is active.
 * Server timeline lacks range-start params in Phase 20-02 — document limitation.
 */
export function eventInChapterRange(
  chapterNumber: number,
  start: number,
  end: number
): boolean {
  return chapterNumber >= start && chapterNumber <= end;
}

/**
 * Clue intersects [start, end] when plant/payoff (or narrative chapter) overlaps.
 */
export function clueIntersectsChapterRange(
  clue: {
    narrative_chapter_number: number;
    first_cue_chapter?: number | null;
    payoff_chapter?: number | null;
  },
  start: number,
  end: number
): boolean {
  const plant = clue.first_cue_chapter ?? clue.narrative_chapter_number;
  const payoff = clue.payoff_chapter ?? plant;
  const lo = Math.min(plant, payoff);
  const hi = Math.max(plant, payoff);
  return lo <= end && hi >= start;
}

export function formatChapterRange(start: number, end: number): string {
  if (start === end) return `第 ${start} 章`;
  return `第 ${start}–${end} 章`;
}

export function nodeKindLabel(kind: StructureNodeKind): string {
  switch (kind) {
    case "book":
      return "全书";
    case "chapter":
      return "章节";
    case "global_story":
      return "全局";
    case "story_arc":
      return "故事弧";
    case "volume":
      return "卷";
    case "chapter_state":
      return "章状态";
    default:
      return String(kind);
  }
}
