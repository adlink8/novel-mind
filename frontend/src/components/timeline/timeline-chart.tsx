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
import { BookOpen, ChevronDown, ChevronUp, Search, X } from "lucide-react";

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

export function TimelineChart({
  events,
  causalEdges,
  ordering,
  novelId,
  onNarrativePositionChange,
}: Props) {
  const [selected, setSelected] = useState<TimelineEvent | null>(null);
  const [listOpen, setListOpen] = useState(false);
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

  const sorted = useMemo(
    () => [...events].sort((a, b) => compareTimelineEvents(a, b, ordering)),
    [events, ordering]
  );
  const positions = useMemo(
    () => new Map(sorted.map((event, index) => [event.id, index])),
    [sorted]
  );

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
    const scatterData: ScatterDatum[] = sorted.map((event, index) => ({
      value: [index, index % 2 === 0 ? -0.22 : 0.22],
      eventId: event.id,
      itemStyle: {
        color: event.provenance?.title === "manual" ? "#b45309" : "#4f6f52",
      },
      label: {
        show: true,
        position: index % 2 === 0 ? "bottom" : "top",
        formatter: event.title || `事件 ${event.id}`,
        width: 110,
        overflow: "truncate",
        fontSize: 11,
      },
    }));

    // 因果边：两端必须在当前可见事件里且索引有效，否则 ECharts getRawIndex 会炸
    const lineSeries = causalEdges.flatMap((edge, edgeIndex) => {
      const fromIdx = positions.get(edge.source_event_id);
      const toIdx = positions.get(edge.target_event_id);
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
        max: Math.max(sorted.length - 0.5, 0.5),
        minInterval: 1,
        axisLabel: {
          formatter: (value: number) => {
            const n = Math.round(value);
            if (n < 0 || n >= sorted.length) return "";
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
  }, [causalEdges, dataEpoch, eventMap, ordering, positions, sorted]);

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

  return (
    <section className="grid min-w-0 gap-3" aria-label="交互式小说时间线">
      <div
        data-testid="timeline-canvas"
        data-zoom="inside-slider"
        className="min-w-0 overflow-hidden rounded-3xl border bg-card p-2 sm:p-4"
      >
        <ReactEChartsCore
          echarts={echarts}
          option={option}
          style={{ height: 420, width: "100%" }}
          // notMerge=false + 稳定 series id，避免 notMerge 全量替换导致 getRawIndex
          notMerge={false}
          lazyUpdate
          opts={{ renderer: "canvas" }}
          onEvents={{
            click: (params: {
              seriesType?: string;
              data?: ScatterDatum;
            }) => {
              try {
                if (params.seriesType !== "scatter") return;
                const id = params.data?.eventId;
                if (id == null) return;
                const event = eventMap.get(id);
                if (event) setSelected(event);
              } catch {
                /* ignore chart click glitches */
              }
            },
            datazoom: (params: {
              start?: number;
              end?: number;
              batch?: Array<{ start?: number; end?: number }>;
            }) => {
              try {
                const batch = params.batch?.[0];
                const start = batch?.start ?? params.start;
                const end = batch?.end ?? params.end;
                if (typeof start === "number" && typeof end === "number") {
                  zoomRef.current = clampZoom({ start, end });
                }
              } catch {
                /* ignore */
              }
            },
          }}
        />
        <p className="px-2 pb-1 text-xs text-muted-foreground">
          滚轮缩放、拖动平移。点节点打开右侧详情。
        </p>
      </div>

      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">共 {sorted.length} 个事件</p>
        <button
          type="button"
          onClick={() => setListOpen((v) => !v)}
          className="inline-flex items-center gap-1 rounded-lg border bg-card px-3 py-1.5 text-xs font-medium"
        >
          {listOpen ? (
            <>
              收起列表 <ChevronUp className="size-3.5" />
            </>
          ) : (
            <>
              展开全部列表 <ChevronDown className="size-3.5" />
            </>
          )}
        </button>
      </div>

      {listOpen && (
        <ol
          aria-label="时间线事件列表"
          className="grid max-h-[320px] gap-2 overflow-y-auto sm:grid-cols-2 xl:grid-cols-3"
        >
          {sorted.map((event) => (
            <li key={event.id}>
              <button
                type="button"
                onClick={() => setSelected(event)}
                className={`h-full w-full rounded-2xl border p-3 text-left transition hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary ${
                  selected?.id === event.id
                    ? "border-primary bg-primary/5"
                    : "bg-card"
                }`}
              >
                <span className="text-xs text-muted-foreground">
                  {chapterLabel(event)} · {event.time_expression ?? "时间未知"}
                </span>
                <h2 className="mt-1 line-clamp-1 font-serif text-base font-semibold">
                  {event.title}
                </h2>
                <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                  {event.description}
                </p>
              </button>
            </li>
          ))}
        </ol>
      )}

      {selected && (
        <>
          <button
            type="button"
            aria-label="关闭详情遮罩"
            className="fixed inset-0 z-40 bg-black/30"
            onClick={() => setSelected(null)}
          />
          <aside
            role="dialog"
            aria-modal="true"
            aria-label={`${selected.title} 事件详情`}
            className="fixed bottom-0 right-0 top-0 z-50 flex w-full max-w-md flex-col border-l bg-background shadow-2xl sm:bottom-auto sm:top-0 sm:h-full"
          >
            <div className="flex items-start justify-between gap-3 border-b px-5 py-4">
              <div className="min-w-0">
                <p className="text-xs font-semibold uppercase tracking-wider text-primary">
                  {chapterLabel(selected)} · 置信度{" "}
                  {Math.round(selected.confidence * 100)}%
                </p>
                <h2 className="mt-1 font-serif text-xl font-semibold leading-snug">
                  {selected.title}
                </h2>
              </div>
              <button
                type="button"
                aria-label="关闭事件详情"
                className="shrink-0 rounded-full border p-2"
                onClick={() => setSelected(null)}
              >
                <X className="size-4" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto px-5 py-4">
              <p className="text-sm leading-7 text-muted-foreground">
                {selected.description}
              </p>
              {selected.participants?.length > 0 && (
                <div className="mt-4">
                  <p className="text-xs font-medium text-muted-foreground">
                    参与人物
                  </p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {selected.participants.map((p) => (
                      <span
                        key={p.mention}
                        className="rounded-full bg-muted px-2.5 py-1 text-xs"
                      >
                        {p.mention}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              <p className="mt-4 text-xs text-muted-foreground">
                时间：{selected.time_expression ?? "未知"}（
                {selected.time_precision}）
              </p>
            </div>
            <div className="flex flex-wrap gap-2 border-t px-5 py-4">
              <Link
                href={`/search?q=${encodeURIComponent(selected.title)}`}
                className="inline-flex flex-1 items-center justify-center gap-2 rounded-xl border px-4 py-2.5 text-sm"
              >
                <Search className="size-4" />
                检索证据
              </Link>
              <Link
                href={readerHref(selected)}
                className="inline-flex flex-1 items-center justify-center gap-2 rounded-xl bg-foreground px-4 py-2.5 text-sm text-background"
              >
                <BookOpen className="size-4" />
                阅读此章
              </Link>
            </div>
          </aside>
        </>
      )}
    </section>
  );
}
