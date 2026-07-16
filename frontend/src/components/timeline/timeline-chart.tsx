"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import ReactEChartsCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { LineChart, ScatterChart } from "echarts/charts";
import {
  DataZoomComponent,
  GridComponent,
  TooltipComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import {
  ArrowLeft,
  BookOpen,
  ChevronDown,
  ChevronUp,
  CircleHelp,
  Expand,
  Search,
} from "lucide-react";

import type {
  TimelineCausalEdge,
  TimelineEvent,
  TimelineOrdering,
} from "@/lib/api";

echarts.use([
  ScatterChart,
  LineChart,
  DataZoomComponent,
  GridComponent,
  TooltipComponent,
  CanvasRenderer,
]);

type Props = {
  events: TimelineEvent[];
  causalEdges: TimelineCausalEdge[];
  ordering: TimelineOrdering;
  novelId: string;
  /** Shared narrative position for relationship workspace (chapter number). */
  onNarrativePositionChange?: (chapter: number | null) => void;
};

type ZoomState = { start: number; end: number };

type EventWindow = {
  start: number;
  end: number;
  firstChapter: number;
  lastChapter: number;
  eventCount: number;
  previewTitle: string;
  previewSummary: string;
};

const OVERVIEW_THRESHOLD = 48;
const EVENT_LABEL_THRESHOLD = 16;
/** Draw causal edges only when the visible window is small enough to stay readable. */
const CAUSAL_EDGE_THRESHOLD = 24;
/** Soft target events per overview stage — not a fixed "7 blocks" UI. */
const SOFT_EVENTS_PER_STAGE = 80;
const MIN_STAGES = 3;
const MAX_STAGES = 14;
const MIN_EVENTS_PER_STAGE = 10;

/** Fixed Y swimlanes for drill-down (top → bottom). */
export const EVENT_TYPE_LANES = ["plot", "conflict", "character", "world"] as const;
export type EventTypeLane = (typeof EVENT_TYPE_LANES)[number];

const LANE_LABELS: Record<EventTypeLane, string> = {
  plot: "情节",
  conflict: "冲突",
  character: "人物",
  world: "世界观",
};

const LANE_COLORS: Record<EventTypeLane, string> = {
  plot: "#4f6f52",
  conflict: "#b45309",
  character: "#3b6ea5",
  world: "#6b5b95",
};

type ScatterDatum = {
  value: [number, number];
  eventId: number;
  itemStyle: { color: string };
  label: {
    show: boolean;
    position: "top" | "bottom";
    formatter: string;
    width: number;
    overflow: "truncate";
    fontSize: number;
  };
};

/** Map free-form event_type strings onto the four fixed lanes. */
export function normalizeEventType(raw: string | undefined | null): EventTypeLane {
  const t = (raw ?? "plot").toLowerCase().trim();
  if (t === "plot" || t === "conflict" || t === "character" || t === "world") {
    return t;
  }
  // Common aliases / Chinese labels from older extracts
  if (t.includes("conflict") || t.includes("冲突")) return "conflict";
  if (t.includes("character") || t.includes("人物") || t.includes("角色"))
    return "character";
  if (t.includes("world") || t.includes("世界") || t.includes("设定")) return "world";
  return "plot";
}

/** Y coordinate for a type lane (plot at top). */
export function eventTypeLaneY(eventType: string | undefined | null): number {
  const lane = normalizeEventType(eventType);
  return EVENT_TYPE_LANES.length - 1 - EVENT_TYPE_LANES.indexOf(lane);
}

/**
 * Chapter-scaled X with micro-offset inside each chapter.
 * Primary scale = narrative_chapter_number; within-chapter order uses
 * source_start then narrative_index/id — never index%4 jitter as layout.
 */
export function buildChapterXPositions(
  events: TimelineEvent[]
): Map<number, number> {
  const byChapter = new Map<number, TimelineEvent[]>();
  for (const event of events) {
    const ch = event.narrative_chapter_number;
    const list = byChapter.get(ch);
    if (list) list.push(event);
    else byChapter.set(ch, [event]);
  }

  const result = new Map<number, number>();
  for (const [ch, list] of byChapter) {
    const ordered = [...list].sort(
      (a, b) =>
        (a.source_start ?? 0) - (b.source_start ?? 0) ||
        a.narrative_index - b.narrative_index ||
        a.id - b.id
    );
    const n = ordered.length;
    const starts = ordered.map((e) => e.source_start ?? 0);
    const minS = Math.min(...starts);
    const maxS = Math.max(...starts);
    ordered.forEach((event, i) => {
      let micro: number;
      if (n === 1) {
        micro = 0.5;
      } else if (maxS > minS) {
        const fromStart = (starts[i] - minS) / (maxS - minS);
        const fromIndex = i / (n - 1);
        // Prefer text offset; blend rank so equal source_start still separates.
        micro = 0.08 + 0.84 * (0.7 * fromStart + 0.3 * fromIndex);
      } else {
        micro = 0.08 + 0.84 * (i / (n - 1));
      }
      result.set(event.id, ch + micro);
    });
  }
  return result;
}

/** Full swimlane points: [chapter+micro, typeLaneY] per event id. */
export function buildSwimlanePoints(
  events: TimelineEvent[]
): Map<number, [number, number]> {
  const xs = buildChapterXPositions(events);
  const out = new Map<number, [number, number]>();
  for (const event of events) {
    out.set(event.id, [
      xs.get(event.id) ?? event.narrative_chapter_number + 0.5,
      eventTypeLaneY(event.event_type),
    ]);
  }
  return out;
}

function compareNarrative(a: TimelineEvent, b: TimelineEvent) {
  return (
    a.narrative_chapter_number - b.narrative_chapter_number ||
    a.source_start - b.source_start ||
    a.id - b.id
  );
}

export function compareTimelineEvents(
  a: TimelineEvent,
  b: TimelineEvent,
  ordering: TimelineOrdering
) {
  if (ordering === "story") {
    if (a.story_rank == null && b.story_rank != null) return 1;
    if (a.story_rank != null && b.story_rank == null) return -1;
    if (
      a.story_rank != null &&
      b.story_rank != null &&
      a.story_rank !== b.story_rank
    )
      return a.story_rank - b.story_rank;
  }
  return compareNarrative(a, b);
}

function chapterLabel(event: TimelineEvent) {
  return `章节 #${event.narrative_chapter_number}`;
}

function clampZoom(z: ZoomState | null): ZoomState {
  if (!z) return { start: 0, end: 100 };
  let start = Number.isFinite(z.start) ? z.start : 0;
  let end = Number.isFinite(z.end) ? z.end : 100;
  start = Math.max(0, Math.min(100, start));
  end = Math.max(0, Math.min(100, end));
  if (end - start < 1) {
    end = Math.min(100, start + 1);
  }
  return { start, end };
}

function makeWindow(events: TimelineEvent[], start: number, end: number): EventWindow {
  const first = events[start];
  const last = events[end - 1];
  return {
    start,
    end,
    firstChapter: first.narrative_chapter_number,
    lastChapter: last.narrative_chapter_number,
    eventCount: end - start,
    previewTitle: first.title,
    previewSummary: first.description,
  };
}

/** Chapters with local event-density minima — natural plot-arc seams. */
function detectDensityValleys(events: TimelineEvent[]): Set<number> {
  const countByChapter = new Map<number, number>();
  for (const event of events) {
    const ch = event.narrative_chapter_number;
    countByChapter.set(ch, (countByChapter.get(ch) ?? 0) + 1);
  }
  const chapters = [...countByChapter.keys()].sort((a, b) => a - b);
  const valleys = new Set<number>();
  for (let i = 1; i < chapters.length - 1; i++) {
    const prev = countByChapter.get(chapters[i - 1]) ?? 0;
    const cur = countByChapter.get(chapters[i]) ?? 0;
    const next = countByChapter.get(chapters[i + 1]) ?? 0;
    if (cur <= prev && cur <= next) {
      valleys.add(chapters[i]);
    }
  }
  return valleys;
}

/**
 * Snap an ideal event-index cut to a nearby **chapter boundary**, preferring
 * density valleys so stages follow plot rhythm rather than equal event quotas.
 */
function snapToPlotBoundary(
  events: TimelineEvent[],
  ideal: number,
  valleys: Set<number>
): number {
  const n = events.length;
  if (ideal <= 0) return 0;
  if (ideal >= n) return n;
  const radius = Math.max(16, Math.floor(n * 0.08));
  let best = ideal;
  let bestScore = Number.POSITIVE_INFINITY;
  const lo = Math.max(1, ideal - radius);
  const hi = Math.min(n - 1, ideal + radius);
  for (let i = lo; i <= hi; i++) {
    const prevCh = events[i - 1].narrative_chapter_number;
    const ch = events[i].narrative_chapter_number;
    if (ch === prevCh) continue; // only cut between chapters
    let score = Math.abs(i - ideal);
    if (valleys.has(ch) || valleys.has(prevCh)) score -= 28;
    // Prefer quieter hand-off chapters slightly.
    const gap = Math.abs(ch - prevCh);
    if (gap >= 2) score -= Math.min(12, gap);
    if (score < bestScore) {
      bestScore = score;
      best = i;
    }
  }
  return best;
}

/**
 * Overview stages from **plot density / chapter seams**, not a fixed 7-way split.
 * Stage count scales with book length; cut points snap to narrative valleys.
 */
export function buildEventWindows(events: TimelineEvent[]): EventWindow[] {
  if (!events.length) return [];
  if (events.length <= OVERVIEW_THRESHOLD) {
    return [makeWindow(events, 0, events.length)];
  }

  const valleys = detectDensityValleys(events);
  const preferredStages = Math.min(
    MAX_STAGES,
    Math.max(MIN_STAGES, Math.ceil(events.length / SOFT_EVENTS_PER_STAGE))
  );

  const idealCuts: number[] = [];
  for (let s = 1; s < preferredStages; s++) {
    idealCuts.push(Math.round((events.length * s) / preferredStages));
  }

  const snapped = idealCuts
    .map((ideal) => snapToPlotBoundary(events, ideal, valleys))
    .filter((cut) => cut > 0 && cut < events.length);

  // Unique sorted boundaries
  const boundaries = [0, ...new Set(snapped)].sort((a, b) => a - b);
  if (boundaries[boundaries.length - 1] !== events.length) {
    boundaries.push(events.length);
  }

  // Merge tiny fragments so overview cards stay meaningful.
  const merged: number[] = [0];
  for (let i = 1; i < boundaries.length; i++) {
    const end = boundaries[i];
    const prev = merged[merged.length - 1];
    const isLast = i === boundaries.length - 1;
    if (!isLast && end - prev < MIN_EVENTS_PER_STAGE) {
      continue; // absorb into next segment
    }
    merged.push(end);
  }
  if (merged[merged.length - 1] !== events.length) {
    merged.push(events.length);
  }
  // If last absorb left a tiny tail, fold into previous.
  if (
    merged.length >= 3 &&
    merged[merged.length - 1] - merged[merged.length - 2] < MIN_EVENTS_PER_STAGE
  ) {
    merged.splice(merged.length - 2, 1);
  }

  const windows: EventWindow[] = [];
  for (let i = 0; i < merged.length - 1; i++) {
    windows.push(makeWindow(events, merged[i], merged[i + 1]));
  }
  return windows.length ? windows : [makeWindow(events, 0, events.length)];
}

function chapterRangeLabel(window: EventWindow) {
  return window.firstChapter === window.lastChapter
    ? `第 ${window.firstChapter} 章`
    : `第 ${window.firstChapter}–${window.lastChapter} 章`;
}

function overviewGridClass(stageCount: number): string {
  // Adaptive columns — no longer locked to 7.
  if (stageCount <= 3) return "grid grid-cols-1 gap-2 sm:grid-cols-3";
  if (stageCount <= 6) return "grid grid-cols-2 gap-2 sm:grid-cols-3";
  if (stageCount <= 9) return "grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4";
  return "grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5";
}

export function TimelineChart({
  events,
  causalEdges,
  ordering,
  novelId,
  onNarrativePositionChange,
}: Props) {
  const [selected, setSelected] = useState<TimelineEvent | null>(null);
  const [listOpen, setListOpen] = useState(false);
  const [activeWindow, setActiveWindow] = useState<EventWindow | null>(null);
  const onNarrativeRef = useRef(onNarrativePositionChange);
  /** 仅在数据/排序变化时递增，拖动缩放只写 ref 不触发渲染 */
  const [dataEpoch, setDataEpoch] = useState(0);
  const zoomRef = useRef<ZoomState>({ start: 0, end: 100 });

  useEffect(() => {
    onNarrativeRef.current = onNarrativePositionChange;
  }, [onNarrativePositionChange]);
  const eventMap = useMemo(
    () => new Map(events.map((e) => [e.id, e])),
    [events]
  );

  // Progressive poll: animate only first-seen event identities once.
  const seenEventIdsRef = useRef<Set<number>>(new Set());
  const [freshEventIds, setFreshEventIds] = useState<Set<number>>(
    () => new Set()
  );
  useEffect(() => {
    const nextFresh = new Set<number>();
    for (const event of events) {
      if (!seenEventIdsRef.current.has(event.id)) {
        seenEventIdsRef.current.add(event.id);
        nextFresh.add(event.id);
      }
    }
    // Drop ids no longer present (novel switch / version replace).
    for (const id of [...seenEventIdsRef.current]) {
      if (!events.some((e) => e.id === id)) {
        seenEventIdsRef.current.delete(id);
      }
    }
    if (nextFresh.size === 0) return;
    // Defer so we do not setState synchronously inside the effect body.
    const start = window.setTimeout(() => {
      setFreshEventIds(nextFresh);
      window.setTimeout(() => setFreshEventIds(new Set()), 220);
    }, 0);
    return () => window.clearTimeout(start);
  }, [events]);

  const sorted = useMemo(
    () => [...events].sort((a, b) => compareTimelineEvents(a, b, ordering)),
    [events, ordering]
  );
  const windows = useMemo(() => buildEventWindows(sorted), [sorted]);
  const visibleEvents = useMemo(
    () =>
      activeWindow
        ? sorted.slice(activeWindow.start, activeWindow.end)
        : sorted,
    [activeWindow, sorted]
  );
  const swimlanePoints = useMemo(
    () => buildSwimlanePoints(visibleEvents),
    [visibleEvents]
  );
  // Every novel enters through the same overview-first layout.  Small books
  // simply render one stage, so their page does not fall back to the legacy
  // chart-only presentation.
  const showOverview = !activeWindow;

  // 排序维度变化时重置视野；事件集合变化时保留缩放并重建 option
  const orderingRef = useRef(ordering);
  const eventsKeyRef = useRef("");
  useEffect(() => {
    const key = `${ordering}:${events.map((e) => e.id).join(",")}`;
    if (orderingRef.current !== ordering) {
      orderingRef.current = ordering;
      zoomRef.current = { start: 0, end: 100 };
    }
    if (eventsKeyRef.current !== key) {
      eventsKeyRef.current = key;
      setDataEpoch((n) => n + 1);
    }
  }, [events, ordering]);

  const option = useMemo<echarts.EChartsCoreOption>(() => {
    // zoomRef is intentionally not React state: pan/zoom must not re-render the chart tree.
    // eslint-disable-next-line react-hooks/refs -- read latest zoom snapshot when rebuilding option
    const z = clampZoom(zoomRef.current);
    const showEventLabels = visibleEvents.length <= EVENT_LABEL_THRESHOLD;
    const showCausalEdges =
      causalEdges.length > 0 && visibleEvents.length <= CAUSAL_EDGE_THRESHOLD;

    const chapters = visibleEvents.map((e) => e.narrative_chapter_number);
    const minChapter = chapters.length ? Math.min(...chapters) : 1;
    const maxChapter = chapters.length ? Math.max(...chapters) : 1;

    const scatterData: ScatterDatum[] = visibleEvents.map((event) => {
      const point = swimlanePoints.get(event.id) ?? [
        event.narrative_chapter_number + 0.5,
        eventTypeLaneY(event.event_type),
      ];
      const lane = normalizeEventType(event.event_type);
      const baseColor = LANE_COLORS[lane];
      return {
        value: point,
        eventId: event.id,
        itemStyle: {
          color:
            event.provenance?.title === "manual" ? "#b45309" : baseColor,
        },
        label: {
          show: showEventLabels,
          position: point[1] >= 2 ? "bottom" : "top",
          formatter: event.title || `事件 ${event.id}`,
          width: 110,
          overflow: "truncate",
          fontSize: 11,
        },
      };
    });

    // Causal edges: only in sparse windows; endpoints must both be in view.
    const lineSeries = showCausalEdges
      ? causalEdges.flatMap((edge, edgeIndex) => {
          const from = swimlanePoints.get(edge.source_event_id);
          const to = swimlanePoints.get(edge.target_event_id);
          if (
            !from ||
            !to ||
            !Number.isFinite(from[0]) ||
            !Number.isFinite(from[1]) ||
            !Number.isFinite(to[0]) ||
            !Number.isFinite(to[1])
          ) {
            return [];
          }
          return [
            {
              id: `causal-${edge.source_event_id}-${edge.target_event_id}-${edgeIndex}`,
              type: "line" as const,
              data: [from, to],
              symbol: ["none", "arrow"],
              lineStyle: {
                color: "#a16207",
                type: "dashed" as const,
                width: 1.5,
                opacity: 0.75,
              },
              silent: true,
              z: 1,
              clip: true,
            },
          ];
        })
      : [];

    return {
      animation: false,
      grid: { left: 56, right: 20, top: 40, bottom: 72, containLabel: true },
      tooltip: {
        trigger: "item",
        formatter: (params: unknown) => {
          const item = params as { data?: ScatterDatum; seriesType?: string };
          if (item.seriesType !== "scatter" || item.data?.eventId == null) {
            return "";
          }
          const event = eventMap.get(item.data.eventId);
          if (!event) return "";
          const lane = normalizeEventType(event.event_type);
          return `<strong>${event.title}</strong><br/>${chapterLabel(event)} · ${LANE_LABELS[lane]} · ${event.time_expression ?? "时间未知"}`;
        },
      },
      xAxis: {
        type: "value",
        min: minChapter,
        max: maxChapter + 1,
        minInterval: 1,
        axisLabel: {
          formatter: (value: number) => {
            const n = Math.round(value);
            // Chapter ticks only (skip micro-offset fractions).
            if (Math.abs(value - n) > 0.02) return "";
            if (n < minChapter || n > maxChapter) return "";
            return `第 ${n} 章`;
          },
          hideOverlap: true,
        },
        name: ordering === "story" ? "章节（故事序视图）" : "章节",
        nameGap: 28,
      },
      yAxis: {
        type: "value",
        min: -0.5,
        max: EVENT_TYPE_LANES.length - 0.5,
        interval: 1,
        axisTick: { show: false },
        axisLine: { show: false },
        splitLine: {
          show: true,
          lineStyle: { type: "dashed", color: "#d5ddd6", opacity: 0.9 },
        },
        axisLabel: {
          formatter: (value: number) => {
            const n = Math.round(value);
            if (Math.abs(value - n) > 0.02) return "";
            const idx = EVENT_TYPE_LANES.length - 1 - n;
            if (idx < 0 || idx >= EVENT_TYPE_LANES.length) return "";
            return LANE_LABELS[EVENT_TYPE_LANES[idx]];
          },
        },
      },
      dataZoom: [
        {
          id: "inside-x",
          type: "inside",
          xAxisIndex: 0,
          filterMode: "none",
          zoomOnMouseWheel: true,
          moveOnMouseMove: true,
          moveOnMouseWheel: false,
          start: z.start,
          end: z.end,
        },
        {
          id: "slider-x",
          type: "slider",
          xAxisIndex: 0,
          height: 22,
          bottom: 14,
          brushSelect: false,
          start: z.start,
          end: z.end,
        },
      ],
      series: [
        ...lineSeries,
        {
          id: "events-scatter",
          type: "scatter" as const,
          symbolSize: 16,
          data: scatterData,
          z: 2,
          clip: true,
        },
      ],
    };
    // dataEpoch：事件变化时重读 zoomRef，避免拖动时 setState
    void dataEpoch;
  }, [
    causalEdges,
    dataEpoch,
    eventMap,
    ordering,
    swimlanePoints,
    visibleEvents,
  ]);

  useEffect(() => {
    if (selected && !eventMap.has(selected.id)) {
      queueMicrotask(() => setSelected(null));
    }
  }, [eventMap, selected]);

  useEffect(() => {
    const chapter = selected ? selected.narrative_chapter_number : null;
    queueMicrotask(() => onNarrativeRef.current?.(chapter));
  }, [selected]);

  if (!events.length) {
    return (
      <div className="grid min-h-64 place-items-center rounded-3xl border border-dashed text-sm text-muted-foreground">
        当前筛选没有可见事件。
      </div>
    );
  }

  const readerHref = (event: TimelineEvent) =>
    `/novels/${novelId}?chapter=${event.narrative_chapter_number}&from=timeline`;
  const detailEvent = selected ?? visibleEvents[0];
  const displayedEvents = listOpen ? visibleEvents : visibleEvents.slice(0, 3);

  return (
    <section className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_330px]" aria-label="交互式小说时间线">
      <div className="min-w-0 space-y-4">
        {showOverview ? (
          <section aria-labelledby="timeline-overview-title" className="overflow-hidden rounded-3xl border bg-card p-4 shadow-sm sm:p-6">
            <div className="mb-6 flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#53745a]">Whole book overview</p>
                <h2 id="timeline-overview-title" className="mt-1 font-serif text-xl font-semibold">全书概览 <span className="font-sans text-base font-normal text-muted-foreground">· {sorted.length} 个事件</span></h2>
              </div>
              <p className="inline-flex items-center gap-1.5 rounded-full bg-[#eef4ec] px-3 py-1.5 text-xs font-medium text-[#486d50]">
                <CircleHelp className="size-3.5" />
                按剧情节奏分段 · 非固定七等分
              </p>
            </div>
            <div className="rounded-2xl border bg-[#fcfdfb] p-4 sm:p-5">
              <div className="mb-4 flex flex-wrap items-end justify-between gap-2 text-xs text-muted-foreground">
                <span>{ordering === "narrative" ? "叙事推进" : "故事时间"}</span>
                <span>
                  {chapterRangeLabel(windows[0])} —{" "}
                  {chapterRangeLabel(windows[windows.length - 1])} · 共{" "}
                  {windows.length} 个剧情阶段
                </span>
              </div>
              <div
                className={overviewGridClass(windows.length)}
                aria-label="按剧情阶段聚合的全书时间线"
              >
                {windows.map((window, index) => {
                  const maxCount = Math.max(
                    ...windows.map((item) => item.eventCount),
                    1
                  );
                  const density = Math.max(
                    28,
                    Math.round((window.eventCount / maxCount) * 100)
                  );
                  const chapterSpan =
                    window.lastChapter - window.firstChapter + 1;
                  return (
                    <button
                      key={`${window.start}-${window.end}`}
                      type="button"
                      aria-label={`阶段 ${index + 1} · ${chapterRangeLabel(window)} · ${window.eventCount} 个事件`}
                      onClick={() => setActiveWindow(window)}
                      className="group rounded-xl p-1.5 text-left transition-[background-color,transform,box-shadow] duration-200 ease-out hover:-translate-y-0.5 hover:bg-muted hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                    >
                      <div className="flex h-28 items-end rounded-lg bg-[#edf0e9] px-1.5 pb-1.5">
                        <span
                          className="w-full rounded-md bg-[#567d5c] transition-[height,filter] duration-300 ease-out group-hover:brightness-95"
                          style={{ height: `${density}%` }}
                        />
                      </div>
                      <p className="mt-2 text-[10px] font-medium uppercase tracking-wide text-[#53745a]">
                        阶段 {index + 1}
                        {chapterSpan > 1 ? ` · ${chapterSpan} 章` : ""}
                      </p>
                      <p className="mt-0.5 truncate text-xs font-semibold text-foreground">
                        {chapterRangeLabel(window)}
                      </p>
                      <p className="mt-0.5 truncate text-[11px] font-medium text-foreground/80">
                        {window.previewTitle}
                      </p>
                      <p className="mt-1 line-clamp-2 min-h-8 text-[10px] leading-4 text-muted-foreground">
                        {window.previewSummary}
                      </p>
                      <p className="mt-1 text-[11px] text-muted-foreground">
                        {window.eventCount} 事件
                      </p>
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-amber-200 bg-amber-50/65 px-4 py-3">
              <div>
                <p className="text-sm font-semibold text-amber-950">
                  选择一个剧情阶段，展开查看事件与因果连接
                </p>
                <p className="mt-0.5 text-xs text-amber-900/75">
                  阶段按章节事件密度谷值切分（跟随剧情节奏），不是固定七等分。
                </p>
              </div>
              <button
                type="button"
                onClick={() => setActiveWindow(windows[0])}
                className="inline-flex items-center gap-2 rounded-xl bg-[#3d684d] px-3.5 py-2 text-sm font-medium text-white transition hover:bg-[#31563f]"
              >
                <Expand className="size-4" />
                展开首阶段
              </button>
            </div>
          </section>
        ) : (
          <section className="rounded-3xl border bg-card p-4 shadow-sm sm:p-6">
            <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-3"><button type="button" onClick={() => { setActiveWindow(null); setListOpen(false); }} className="rounded-xl border p-2 text-muted-foreground transition hover:text-foreground" aria-label="返回全书概览"><ArrowLeft className="size-4" /></button><div><p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#a56d21]">Zoomed range</p><h2 className="mt-1 font-serif text-xl font-semibold">{activeWindow ? chapterRangeLabel(activeWindow) : "当前时间线"} <span className="font-sans text-base font-normal text-muted-foreground">· {visibleEvents.length} 个事件</span></h2></div></div>
              <p className="text-xs text-muted-foreground">横轴章节 · 纵轴类型泳道 · 滚轮缩放</p>
            </div>
            <div data-testid="timeline-canvas" data-zoom="inside-slider" data-layout="chapter-swimlane" className="min-w-0 overflow-hidden rounded-2xl border bg-[#fdfefc] p-2 sm:p-4">
              <ReactEChartsCore echarts={echarts} option={option} style={{ height: 420, width: "100%" }} notMerge={false} lazyUpdate opts={{ renderer: "canvas" }} onEvents={{ click: (params: { seriesType?: string; data?: ScatterDatum }) => { try { if (params.seriesType !== "scatter") return; const id = params.data?.eventId; const event = id == null ? undefined : eventMap.get(id); if (event) setSelected(event); } catch { /* ignore chart click glitches */ } }, datazoom: (params: { start?: number; end?: number; batch?: Array<{ start?: number; end?: number }> }) => { try { const batch = params.batch?.[0]; const start = batch?.start ?? params.start; const end = batch?.end ?? params.end; if (typeof start === "number" && typeof end === "number") zoomRef.current = clampZoom({ start, end }); } catch { /* ignore */ } } }} />
            </div>
            <p className="mt-3 text-xs text-muted-foreground">
              {visibleEvents.length > EVENT_LABEL_THRESHOLD
                ? "事件较多：悬停查看标题；点节点在右侧打开详情。因果边在密集区间自动隐藏。"
                : "泳道按情节 / 冲突 / 人物 / 世界观分层；点节点在右侧打开详情。"}
            </p>
          </section>
        )}

        <section className="rounded-3xl border bg-card p-4 shadow-sm sm:p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-serif text-xl font-semibold">当前范围事件</h2><p className="mt-0.5 text-xs text-muted-foreground">{activeWindow ? `仅显示 ${chapterRangeLabel(activeWindow)} 内 ${visibleEvents.length} 个事件` : `共 ${sorted.length} 个事件`}</p></div><button type="button" aria-label={listOpen ? "收起列表" : activeWindow ? "展开当前范围列表" : "展开全部列表"} onClick={() => setListOpen((value) => !value)} className="inline-flex items-center gap-1.5 text-sm font-medium text-primary">{listOpen ? "收起列表" : "查看全部"}{listOpen ? <ChevronUp className="size-4" /> : <ChevronDown className="size-4" />}</button></div>
          <ol aria-label="时间线事件列表" className="grid min-h-[6rem] gap-3 md:grid-cols-3">{displayedEvents.map((event) => <li key={event.id} data-event-id={event.id} data-insertion={freshEventIds.has(event.id) ? "fresh" : "stable"} className={freshEventIds.has(event.id) ? "motion-transition-content opacity-100" : undefined}><button type="button" onClick={() => setSelected(event)} className={`h-full w-full rounded-2xl border p-4 text-left transition-[border-color,box-shadow,background-color,color] motion-duration-standard motion-ease-enter hover:border-primary hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${selected?.id === event.id ? "border-primary/60 bg-primary/5" : "bg-card"}`}><p className="text-xs text-muted-foreground">{chapterLabel(event)} · {event.time_expression ?? "时间未知"}</p><h3 className="mt-1.5 line-clamp-1 font-serif text-base font-semibold">{event.title}</h3><p className="mt-2 line-clamp-2 text-xs leading-5 text-muted-foreground">{event.description}</p></button></li>)}</ol>
        </section>
      </div>

      <aside className="h-fit rounded-3xl border bg-card p-5 shadow-sm xl:sticky xl:top-5" aria-label="选中事件详情">
        <div className="mb-5 flex items-center justify-between"><span className="rounded-full bg-primary/10 px-2.5 py-1 text-xs font-semibold text-primary">{chapterLabel(detailEvent)}</span>{selected && <button type="button" onClick={() => setSelected(null)} className="rounded-lg p-1.5 text-muted-foreground transition hover:bg-muted" aria-label="关闭详情">×</button>}</div>
        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#a56d21]">Selected event</p><h2 className="mt-1 font-serif text-2xl font-semibold leading-snug">{detailEvent.title}</h2>
        <p className="mt-4 text-sm leading-7 text-muted-foreground">{detailEvent.description}</p>
        {detailEvent.participants?.length > 0 && <div className="mt-5 border-t pt-4"><p className="text-xs font-semibold text-muted-foreground">参与人物</p><div className="mt-2 flex flex-wrap gap-2">{detailEvent.participants.map((participant) => <span key={participant.mention} className="rounded-full bg-muted px-2.5 py-1 text-xs">{participant.mention}</span>)}</div></div>}
        <div className="mt-5 rounded-xl bg-[#f2f6f0] p-3"><p className="text-xs font-medium text-[#53745a]">为什么在这里显示？</p><p className="mt-1 text-xs leading-5 text-muted-foreground">它位于当前可见范围内；全书概览不会堆叠展示事件标题。</p></div>
        <div className="mt-5 grid gap-2"><Link href={`/search?q=${encodeURIComponent(detailEvent.title)}`} className="inline-flex items-center justify-center gap-2 rounded-xl border bg-card px-3 py-2.5 text-sm font-medium transition hover:border-primary"><Search className="size-4" />检索证据</Link><Link href={readerHref(detailEvent)} className="inline-flex items-center justify-center gap-2 rounded-xl bg-foreground px-3 py-2.5 text-sm font-medium text-background transition hover:bg-foreground/85"><BookOpen className="size-4" />阅读此章</Link></div>
      </aside>
    </section>
  );
}
