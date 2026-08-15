"use client";

import { EmptyState } from "@/components/empty-state";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { EvalRun } from "@/lib/api";
import ReactEChartsCore from "echarts-for-react/lib/core";
import * as echarts from "echarts/core";
import { LineChart, BarChart } from "echarts/charts";
import {
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import { STRATEGY_LABELS } from "./labels";

echarts.use([
  LineChart,
  BarChart,
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  CanvasRenderer,
]);

// ── ECharts 主题（与全局 ink/paper 设计令牌同色系） ───────────────

const CHART_INK = "#3a342c"; // 暖墨：接近 hsl(28 20% 13%) foreground
const CHART_MUTED = "#84786a"; // 暖灰：接近 muted-foreground

const paperTheme = {
  textStyle: { color: CHART_MUTED },
  legend: { textStyle: { color: CHART_MUTED } },
};

/** 给最近 10 条 run 打标签：显示策略名+时间 */
function formatRunsForChart(runs: EvalRun[], metric: keyof EvalRun) {
  return runs
    .slice(-10)
    .map((r) => ({
      name: `${STRATEGY_LABELS[r.strategy] || r.strategy} ${new Date(r.created_at).toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}`,
      value: (r[metric] as number) ?? 0,
    }));
}

export function MetricBadge({
  label,
  value,
  good,
}: {
  label: string;
  value: string;
  good: boolean;
}) {
  return (
    <span
      className={cn(
        "px-1.5 py-0.5 rounded text-xs font-medium",
        good ? "bg-green-100 text-green-700" : "bg-yellow-100 text-yellow-700"
      )}
    >
      {label}: {value}
    </span>
  );
}

export function CompareCharts({ runs }: { runs: EvalRun[] }) {
  if (runs.length === 0) {
    return <EmptyState icon="📊" title="暂无评测数据" description="运行至少 1 次评测后显示图表" />;
  }

  // 取最新 run 按策略分组
  const latestByStrategy = new Map<string, EvalRun>();
  for (const r of [...runs].reverse()) {
    if (!latestByStrategy.has(r.strategy)) {
      latestByStrategy.set(r.strategy, r);
    }
  }
  const strategies = Array.from(latestByStrategy.keys());
  const strategyRuns = strategies.map((s) => latestByStrategy.get(s)!);

  const metrics = ["recall_at_k", "precision_at_k", "mrr", "ndcg_at_k"] as const;
  const metricNames: Record<string, string> = {
    recall_at_k: "Recall@5",
    precision_at_k: "Precision@5",
    mrr: "MRR",
    ndcg_at_k: "NDCG@5",
  };

  const barOption: echarts.EChartsCoreOption = {
    ...paperTheme,
    title: { text: "策略指标对比", left: "center", textStyle: { color: CHART_INK, fontSize: 14 } },
    tooltip: { trigger: "axis", valueFormatter: (v: unknown) => (typeof v === "number" ? (v * 100).toFixed(1) + "%" : String(v)) },
    legend: { bottom: 0, textStyle: { color: CHART_MUTED } },
    grid: { left: "3%", right: "4%", bottom: "12%", top: "15%", containLabel: true },
    xAxis: {
      type: "category",
      data: strategyRuns.map((r) => STRATEGY_LABELS[r.strategy] || r.strategy),
      axisLabel: { color: CHART_MUTED, rotate: 15 },
    },
    yAxis: {
      type: "value",
      name: "得分",
      axisLabel: { color: CHART_MUTED, formatter: (v: number) => (v * 100).toFixed(0) + "%" },
      max: 1,
    },
    series: metrics.map((m, i) => ({
      name: metricNames[m],
      type: "bar",
      data: strategyRuns.map((r) => {
        const v = r[m];
        return v != null ? Number(v) : 0;
      }),
      itemStyle: {
        color: ["#3b82f6", "#22c55e", "#f59e0b", "#ef4444"][i],
      },
    })),
  };

  const latencyOption: echarts.EChartsCoreOption = {
    ...paperTheme,
    title: { text: "延迟对比 (ms)", left: "center", textStyle: { color: CHART_INK, fontSize: 14 } },
    tooltip: { trigger: "axis" },
    grid: { left: "3%", right: "4%", bottom: "12%", top: "15%", containLabel: true },
    xAxis: {
      type: "category",
      data: strategyRuns.map((r) => STRATEGY_LABELS[r.strategy] || r.strategy),
      axisLabel: { color: CHART_MUTED, rotate: 15 },
    },
    yAxis: { type: "value", name: "ms", axisLabel: { color: CHART_MUTED } },
    series: [{
      name: "延迟",
      type: "bar",
      data: strategyRuns.map((r) => r.latency_ms ?? 0),
      itemStyle: { color: "#8b5cf6" },
      label: { show: true, position: "top", color: CHART_MUTED, formatter: (p: { value: number }) => p.value > 1000 ? (p.value / 1000).toFixed(1) + "s" : p.value.toFixed(0) + "ms" },
    }],
  };

  return (
    <div className="space-y-6">
      <Card className="paper-surface rounded-2xl"><CardContent className="p-4"><ReactEChartsCore echarts={echarts} option={barOption} style={{ height: 300 }} /></CardContent></Card>
      <Card className="paper-surface rounded-2xl"><CardContent className="p-4"><ReactEChartsCore echarts={echarts} option={latencyOption} style={{ height: 250 }} /></CardContent></Card>
    </div>
  );
}

export function TrendCharts({ runs }: { runs: EvalRun[] }) {
  if (runs.length < 2) {
    return <EmptyState icon="📈" title="需要至少 2 次评测运行" description="多运行几次评测后查看指标变化趋势" />;
  }

  const sorted = [...runs].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());

  const commonGrid = { left: "3%", right: "4%", bottom: "12%", top: "15%", containLabel: true };
  const xData = sorted.map((r) => new Date(r.created_at).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }));

  const lineOption: echarts.EChartsCoreOption = {
    ...paperTheme,
    title: { text: "Recall / MRR / NDCG 趋势", left: "center", textStyle: { color: CHART_INK, fontSize: 14 } },
    tooltip: { trigger: "axis", valueFormatter: (v: unknown) => typeof v === "number" ? (v * 100).toFixed(1) + "%" : String(v) },
    legend: { bottom: 0, textStyle: { color: CHART_MUTED } },
    grid: { ...commonGrid },
    xAxis: { type: "category", data: xData, axisLabel: { color: CHART_MUTED, rotate: 30, fontSize: 10 } },
    yAxis: { type: "value", name: "得分", axisLabel: { color: CHART_MUTED, formatter: (v: number) => (v * 100).toFixed(0) + "%" }, max: 1 },
    series: [
      {
        name: "Recall@5", type: "line",
        data: sorted.map((r) => r.recall_at_k),
        lineStyle: { color: "#3b82f6" }, itemStyle: { color: "#3b82f6" },
        markLine: { silent: true, data: [{ yAxis: 0.5, label: { formatter: "目标 50%", color: CHART_MUTED } }], lineStyle: { type: "dashed", color: "#64748b" } },
      },
      {
        name: "MRR", type: "line",
        data: sorted.map((r) => r.mrr ?? 0),
        lineStyle: { color: "#f59e0b" }, itemStyle: { color: "#f59e0b" },
      },
      {
        name: "NDCG@5", type: "line",
        data: sorted.map((r) => r.ndcg_at_k ?? 0),
        lineStyle: { color: "#ef4444" }, itemStyle: { color: "#ef4444" },
      },
    ],
  };

  const latencyLineOption: echarts.EChartsCoreOption = {
    ...paperTheme,
    title: { text: "延迟趋势 (ms)", left: "center", textStyle: { color: CHART_INK, fontSize: 14 } },
    tooltip: { trigger: "axis" },
    legend: { bottom: 0, textStyle: { color: CHART_MUTED } },
    grid: { ...commonGrid },
    xAxis: { type: "category", data: xData, axisLabel: { color: CHART_MUTED, rotate: 30, fontSize: 10 } },
    yAxis: { type: "value", name: "ms", axisLabel: { color: CHART_MUTED } },
    series: [{
      name: "延迟", type: "line",
      data: sorted.map((r) => r.latency_ms ?? 0),
      lineStyle: { color: "#8b5cf6" }, itemStyle: { color: "#8b5cf6" },
      areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: "rgba(139,92,246,0.25)" }, { offset: 1, color: "rgba(139,92,246,0.02)" }]) },
    }],
  };

  return (
    <div className="space-y-6">
      <Card className="paper-surface rounded-2xl"><CardContent className="p-4"><ReactEChartsCore echarts={echarts} option={lineOption} style={{ height: 300 }} /></CardContent></Card>
      <Card className="paper-surface rounded-2xl"><CardContent className="p-4"><ReactEChartsCore echarts={echarts} option={latencyLineOption} style={{ height: 250 }} /></CardContent></Card>
    </div>
  );
}
