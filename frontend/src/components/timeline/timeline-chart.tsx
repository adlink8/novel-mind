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

function buildEventWindows(events: TimelineEvent[]): EventWindow[] {
  const windows: EventWindow[] = [];
  // The overview is deliberately limited to seven visual stages.  This makes a
  // 998-event novel readable at a glance; drilling in still exposes every event.
  const windowCount = Math.min(7, Math.ceil(events.length / OVERVIEW_THRESHOLD));
  const windowSize = Math.ceil(events.length / windowCount);
  for (let start = 0; start < events.length; start += windowSize) {
    const end = Math.min(start + windowSize, events.length);
    const first = events[start];
    const last = events[end - 1];
    windows.push({
      start,
      end,
      firstChapter: first.narrative_chapter_number,
      lastChapter: last.narrative_chapter_number,
      eventCount: end - start,
      previewTitle: first.title,
      previewSummary: first.description,
    });
  }
  return windows;
}

function chapterRangeLabel(window: EventWindow) {
  return window.firstChapter === window.lastChapter
    ? `第 ${window.firstChapter} 章`
    : `第 ${window.firstChapter}–${window.lastChapter} 章`;
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
  const visiblePositions = useMemo(
    () => new Map(visibleEvents.map((event, index) => [event.id, index])),
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
    const scatterData: ScatterDatum[] = visibleEvents.map((event, index) => ({
      value: [index, ((index % 4) - 1.5) * 0.16],
      eventId: event.id,
      itemStyle: {
        color: event.provenance?.title === "manual" ? "#b45309" : "#4f6f52",
      },
      label: {
        show: showEventLabels,
        position: index % 2 === 0 ? "bottom" : "top",
        formatter: event.title || `事件 ${event.id}`,
        width: 110,
        overflow: "truncate",
        fontSize: 11,
      },
    }));

    // 因果边：两端必须在当前可见事件里且索引有效，否则 ECharts getRawIndex 会炸
    const lineSeries = causalEdges.flatMap((edge, edgeIndex) => {
      const fromIdx = visiblePositions.get(edge.source_event_id);
      const toIdx = visiblePositions.get(edge.target_event_id);
      if (
        fromIdx == null ||
        toIdx == null ||
        !Number.isFinite(fromIdx) ||
        !Number.isFinite(toIdx)
      ) {
        return [];
      }
      return [
        {
          id: `causal-${edge.source_event_id}-${edge.target_event_id}-${edgeIndex}`,
          type: "line" as const,
          data: [
            [fromIdx, 0],
            [toIdx, 0],
          ],
          symbol: ["none", "arrow"],
          lineStyle: { color: "#a16207", type: "dashed" as const, width: 2 },
          silent: true,
          z: 1,
          clip: true,
        },
      ];
    });

    return {
      animation: false,
      grid: { left: 28, right: 20, top: 40, bottom: 72, containLabel: true },
      tooltip: {
        trigger: "item",
        formatter: (params: unknown) => {
          const item = params as { data?: ScatterDatum; seriesType?: string };
          if (item.seriesType !== "scatter" || item.data?.eventId == null) {
            return "";
          }
          const event = eventMap.get(item.data.eventId);
          if (!event) return "";
          return `<strong>${event.title}</strong><br/>${chapterLabel(event)} · ${event.time_expression ?? "时间未知"}`;
        },
      },
      xAxis: {
        type: "value",
        min: -0.5,
        max: Math.max(visibleEvents.length - 0.5, 0.5),
        minInterval: 1,
        axisLabel: {
          formatter: (value: number) => {
            const n = Math.round(value);
            if (n < 0 || n >= visibleEvents.length) return "";
            return ordering === "narrative" ? `节点 ${n + 1}` : `序位 ${n + 1}`;
          },
          hideOverlap: true,
        },
        name: ordering === "narrative" ? "叙事推进" : "故事时间",
      },
      yAxis: { type: "value", min: -0.7, max: 0.7, show: false },
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
          symbolSize: 18,
          data: scatterData,
          z: 2,
          clip: true,
        },
      ],
    };
    // dataEpoch：事件变化时重读 zoomRef，避免拖动时 setState
    void dataEpoch;
  }, [causalEdges, dataEpoch, eventMap, ordering, visibleEvents, visiblePositions]);

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
              <p className="inline-flex items-center gap-1.5 rounded-full bg-[#eef4ec] px-3 py-1.5 text-xs font-medium text-[#486d50]"><CircleHelp className="size-3.5" />每块代表一个剧情阶段，而非单个事件</p>
            </div>
            <div className="rounded-2xl border bg-[#fcfdfb] p-4 sm:p-5">
              <div className="mb-4 flex items-end justify-between text-xs text-muted-foreground"><span>{ordering === "narrative" ? "叙事推进" : "故事时间"}</span><span>{chapterRangeLabel(windows[0])} — {chapterRangeLabel(windows[windows.length - 1])}</span></div>
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-7" aria-label="按剧情阶段聚合的全书时间线">
                {windows.map((window, index) => {
                  const density = Math.max(28, Math.round((window.eventCount / Math.max(...windows.map((item) => item.eventCount))) * 100));
                  return <button key={`${window.start}-${window.end}`} type="button" aria-label={`区间 ${index + 1} · ${window.eventCount} 个事件`} onClick={() => setActiveWindow(window)} className="group rounded-xl p-1.5 text-left transition hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
                    <div className="flex h-28 items-end rounded-lg bg-[#edf0e9] px-1.5 pb-1.5"><span className="w-full rounded-md bg-[#567d5c] transition group-hover:brightness-95" style={{ height: `${density}%` }} /></div>
                    <p className="mt-2 truncate text-xs font-semibold text-foreground">{chapterRangeLabel(window)}</p>
                    <p className="mt-0.5 truncate text-[11px] font-medium text-foreground/80">{window.previewTitle}</p>
                    <p className="mt-1 line-clamp-2 min-h-8 text-[10px] leading-4 text-muted-foreground">{window.previewSummary}</p>
                    <p className="mt-1 text-[11px] text-muted-foreground">{window.eventCount} 事件</p>
                  </button>;
                })}
              </div>
            </div>
            <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-amber-200 bg-amber-50/65 px-4 py-3">
              <div><p className="text-sm font-semibold text-amber-950">选择一个剧情阶段，展开查看事件与因果连接</p><p className="mt-0.5 text-xs text-amber-900/75">完整事件标题仅在具体范围内展示，避免全书节点堆叠。</p></div>
              <button type="button" onClick={() => setActiveWindow(windows[0])} className="inline-flex items-center gap-2 rounded-xl bg-[#3d684d] px-3.5 py-2 text-sm font-medium text-white transition hover:bg-[#31563f]"><Expand className="size-4" />展开查看</button>
            </div>
          </section>
        ) : (
          <section className="rounded-3xl border bg-card p-4 shadow-sm sm:p-6">
            <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-3"><button type="button" onClick={() => { setActiveWindow(null); setListOpen(false); }} className="rounded-xl border p-2 text-muted-foreground transition hover:text-foreground" aria-label="返回全书概览"><ArrowLeft className="size-4" /></button><div><p className="text-xs font-semibold uppercase tracking-[0.14em] text-[#a56d21]">Zoomed range</p><h2 className="mt-1 font-serif text-xl font-semibold">{activeWindow ? chapterRangeLabel(activeWindow) : "当前时间线"} <span className="font-sans text-base font-normal text-muted-foreground">· {visibleEvents.length} 个事件</span></h2></div></div>
              <p className="text-xs text-muted-foreground">滚轮缩放 · 拖动平移 · 点击节点查看详情</p>
            </div>
            <div data-testid="timeline-canvas" data-zoom="inside-slider" className="min-w-0 overflow-hidden rounded-2xl border bg-[#fdfefc] p-2 sm:p-4">
              <ReactEChartsCore echarts={echarts} option={option} style={{ height: 420, width: "100%" }} notMerge={false} lazyUpdate opts={{ renderer: "canvas" }} onEvents={{ click: (params: { seriesType?: string; data?: ScatterDatum }) => { try { if (params.seriesType !== "scatter") return; const id = params.data?.eventId; const event = id == null ? undefined : eventMap.get(id); if (event) setSelected(event); } catch { /* ignore chart click glitches */ } }, datazoom: (params: { start?: number; end?: number; batch?: Array<{ start?: number; end?: number }> }) => { try { const batch = params.batch?.[0]; const start = batch?.start ?? params.start; const end = batch?.end ?? params.end; if (typeof start === "number" && typeof end === "number") zoomRef.current = clampZoom({ start, end }); } catch { /* ignore */ } } }} />
            </div>
            <p className="mt-3 text-xs text-muted-foreground">{visibleEvents.length > EVENT_LABEL_THRESHOLD ? "悬停查看标题；点节点在右侧打开详情。" : "点节点在右侧打开详情。"}</p>
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
