"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import ReactEChartsCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { LineChart, ScatterChart } from "echarts/charts";
import { DataZoomComponent, GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { BookOpen, Search, X } from "lucide-react";

import type { TimelineCausalEdge, TimelineEvent, TimelineOrdering } from "@/lib/api";

echarts.use([ScatterChart, LineChart, DataZoomComponent, GridComponent, TooltipComponent, CanvasRenderer]);

type Props = { events: TimelineEvent[]; causalEdges: TimelineCausalEdge[]; ordering: TimelineOrdering; novelId: string };

type TimelineEventWithSourceOffset = TimelineEvent & { source_start?: number | null };

function compareNarrative(a: TimelineEvent, b: TimelineEvent) {
  const sourceStart = (event: TimelineEvent) => (event as TimelineEventWithSourceOffset).source_start ?? event.narrative_index;
  return a.narrative_chapter_number - b.narrative_chapter_number
    || sourceStart(a) - sourceStart(b)
    || a.id - b.id;
}

export function compareTimelineEvents(a: TimelineEvent, b: TimelineEvent, ordering: TimelineOrdering) {
  if (ordering === "story") {
    if (a.story_rank == null && b.story_rank != null) return 1;
    if (a.story_rank != null && b.story_rank == null) return -1;
    if (a.story_rank != null && b.story_rank != null && a.story_rank !== b.story_rank) return a.story_rank - b.story_rank;
  }
  return compareNarrative(a, b);
}

export function TimelineChart({ events, causalEdges, ordering, novelId }: Props) {
  const [selected, setSelected] = useState<TimelineEvent | null>(null);
  const sorted = useMemo(() => [...events].sort((a, b) => compareTimelineEvents(a, b, ordering)), [events, ordering]);
  const positions = useMemo(() => new Map(sorted.map((event, index) => [event.id, index])), [sorted]);
  const option = useMemo<echarts.EChartsCoreOption>(() => ({
    animation: false,
    grid: { left: 30, right: 24, top: 36, bottom: 76, containLabel: true },
    tooltip: { trigger: "item", formatter: (params: unknown) => {
      const item = params as { data?: { event?: TimelineEvent } };
      return item.data?.event ? `<strong>${item.data.event.title}</strong><br/>第 ${item.data.event.narrative_chapter_number} 章 · ${item.data.event.time_expression ?? "时间未知"}` : "";
    } },
    xAxis: { type: "value", minInterval: 1, axisLabel: { formatter: (value: number) => ordering === "narrative" ? `节点 ${value + 1}` : `序位 ${value + 1}`, hideOverlap: true }, name: ordering === "narrative" ? "叙事推进" : "故事时间" },
    yAxis: { type: "value", min: -0.7, max: 0.7, show: false },
    dataZoom: [{ type: "inside", xAxisIndex: 0, filterMode: "none", zoomOnMouseWheel: true, moveOnMouseMove: true, moveOnMouseWheel: false }, { type: "slider", xAxisIndex: 0, height: 22, bottom: 16, brushSelect: false }],
    series: [
      ...causalEdges.flatMap((edge) => {
        const from = events.find((event) => event.id === edge.source_event_id);
        const to = events.find((event) => event.id === edge.target_event_id);
        return from && to ? [{ type: "line" as const, data: [[positions.get(from.id), 0], [positions.get(to.id), 0]], symbol: ["none", "arrow"], lineStyle: { color: "#a16207", type: "dashed", width: 2 }, silent: true }] : [];
      }),
      { type: "scatter", symbolSize: 18, data: sorted.map((event, index) => ({ value: [index, index % 2 ? 0.22 : -0.22], event, itemStyle: { color: event.provenance.title === "manual" ? "#b45309" : "#4f6f52" }, label: { show: true, position: index % 2 ? "top" : "bottom", formatter: event.title, width: 120, overflow: "truncate", fontSize: 12 } })) },
    ],
  }), [causalEdges, events, ordering, positions, sorted]);

  if (!events.length) return <div className="grid min-h-64 place-items-center rounded-3xl border border-dashed text-sm text-muted-foreground">当前筛选没有可见事件。</div>;

  return (
    <section className="grid min-w-0 gap-4" aria-label="交互式小说时间线">
      <div data-testid="timeline-canvas" data-zoom="inside-slider" className="min-w-0 overflow-hidden rounded-3xl border bg-card p-2 sm:p-4">
        <ReactEChartsCore echarts={echarts} option={option} style={{ height: 360, width: "100%" }} notMerge onEvents={{ click: (params: { data?: { event?: TimelineEvent } }) => params.data?.event && setSelected(params.data.event) }} />
        <p className="px-2 pb-2 text-xs text-muted-foreground">滚轮或双指缩放，拖动时间轴平移。下方列表提供完整键盘操作。</p>
      </div>
      <ol aria-label="时间线事件列表" className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {sorted.map((event) => <li key={event.id}><button type="button" onClick={() => setSelected(event)} className="h-full w-full rounded-2xl border bg-card p-4 text-left transition hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"><span className="text-xs text-muted-foreground">第 {event.narrative_chapter_number} 章 · {event.time_expression ?? "时间未知"}</span><h2 className="mt-1 font-serif text-lg font-semibold">{event.title}</h2><p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{event.description}</p></button></li>)}
      </ol>
      {selected && <div role="dialog" aria-modal="true" aria-label={`${selected.title} 事件证据`} className="fixed inset-0 z-50 grid place-items-center bg-black/45 p-4"><article className="relative max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-3xl bg-background p-6 shadow-2xl"><button aria-label="关闭事件详情" className="absolute right-4 top-4 rounded-full border p-2" onClick={() => setSelected(null)}><X className="size-4"/></button><p className="text-xs font-semibold uppercase tracking-wider text-primary">第 {selected.narrative_chapter_number} 章 · 置信度 {Math.round(selected.confidence * 100)}%</p><h2 className="mt-2 pr-10 font-serif text-2xl font-semibold">{selected.title}</h2><p className="mt-3 text-sm leading-6 text-muted-foreground">{selected.description}</p><div className="mt-5 flex flex-wrap gap-2"><Link href={`/search?q=${encodeURIComponent(selected.title)}`} className="inline-flex items-center gap-2 rounded-xl border px-4 py-2 text-sm"><Search className="size-4"/>检索相关证据</Link><Link href={`/novels/${novelId}?chapter=${selected.narrative_chapter_number}`} className="inline-flex items-center gap-2 rounded-xl bg-foreground px-4 py-2 text-sm text-background"><BookOpen className="size-4"/>阅读第 {selected.narrative_chapter_number} 章</Link></div></article></div>}
    </section>
  );
}
