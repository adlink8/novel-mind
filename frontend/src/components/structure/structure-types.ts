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
 * Server also accepts optional chapter_start/chapter_end (intersects spoiler).
 * Keep this as defense-in-depth for densify / people chips.
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

/** True when selected range covers more than one chapter (arc/global). */
export function isMultiChapterScope(start: number, end: number): boolean {
  return end > start;
}

export type ChapterEventCount = { chapter: number; count: number };

/** Per-chapter event counts in narrative order (for multi-chapter density notes). */
export function countEventsByChapter(
  events: { narrative_chapter_number: number }[]
): ChapterEventCount[] {
  const map = new Map<number, number>();
  for (const e of events) {
    const ch = e.narrative_chapter_number;
    map.set(ch, (map.get(ch) ?? 0) + 1);
  }
  return [...map.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([chapter, count]) => ({ chapter, count }));
}

/**
 * Cap scatter/list density for multi-chapter scopes.
 * Samples proportionally per chapter so early chapters do not monopolize the cap.
 * Single-chapter callers should skip this and keep full Phase 19 UX.
 */
export function densifyTimelineForMultiChapter<
  T extends { narrative_chapter_number: number },
>(
  events: T[],
  maxPoints = 120
): {
  displayEvents: T[];
  total: number;
  truncated: number;
  byChapter: ChapterEventCount[];
} {
  const byChapter = countEventsByChapter(events);
  const total = events.length;
  if (total <= maxPoints) {
    return { displayEvents: events, total, truncated: 0, byChapter };
  }

  const groups = new Map<number, T[]>();
  for (const e of events) {
    const list = groups.get(e.narrative_chapter_number);
    if (list) list.push(e);
    else groups.set(e.narrative_chapter_number, [e]);
  }
  const chapters = [...groups.keys()].sort((a, b) => a - b);

  // Floor allocation: at least 1 per non-empty chapter when budget allows
  const quotas = new Map<number, number>();
  let allocated = 0;
  for (const ch of chapters) {
    const count = groups.get(ch)!.length;
    const share = Math.max(1, Math.floor((count / total) * maxPoints));
    const q = Math.min(count, share);
    quotas.set(ch, q);
    allocated += q;
  }
  // Distribute remainder to densest chapters
  let remainder = maxPoints - allocated;
  const byDensity = [...chapters].sort(
    (a, b) =>
      (groups.get(b)?.length ?? 0) - (groups.get(a)?.length ?? 0) || a - b
  );
  while (remainder > 0) {
    let progressed = false;
    for (const ch of byDensity) {
      if (remainder <= 0) break;
      const have = quotas.get(ch) ?? 0;
      const cap = groups.get(ch)!.length;
      if (have < cap) {
        quotas.set(ch, have + 1);
        remainder -= 1;
        progressed = true;
      }
    }
    if (!progressed) break;
  }

  const displayEvents: T[] = [];
  for (const ch of chapters) {
    const list = groups.get(ch) ?? [];
    const q = quotas.get(ch) ?? 0;
    displayEvents.push(...list.slice(0, q));
  }

  return {
    displayEvents,
    total,
    truncated: Math.max(0, total - displayEvents.length),
    byChapter,
  };
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
