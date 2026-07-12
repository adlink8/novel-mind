"use client";

import { useEffect, useState, useMemo } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/empty-state";
import { cn } from "@/lib/utils";
import {
  evalApi,
  QUALITY_STATUS_LABELS,
  qualityStatusTone,
  type EvalDataset,
  type EvalRun,
  type EvalReport,
  type QualityJobPublic,
  type DeprecationMeta,
} from "@/lib/api";
import { useEval } from "@/hooks/use-eval";
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
import { Check, ClipboardList, RefreshCw, TriangleAlert, X } from "lucide-react";
import { PageContainer, PageHeader } from "@/components/page-header";

echarts.use([
  LineChart,
  BarChart,
  TitleComponent,
  TooltipComponent,
  LegendComponent,
  GridComponent,
  CanvasRenderer,
]);

// ── Constants ──────────────────────────────────────────────────────

const TYPE_LABELS: Record<string, string> = {
  original_text: "原文定位",
  character_relation: "人物关系",
  event_causality: "事件因果",
  timeline: "时间线",
  foreshadowing: "伏笔/回收",
};

const STATUS_LABELS: Record<string, string> = {
  candidate: "候选",
  confirmed: "确认",
  rejected: "驳回",
};

const DIFFICULTY_LABELS: Record<string, string> = {
  easy: "简单",
  medium: "中等",
  hard: "困难",
};

const STRATEGY_LABELS: Record<string, string> = {
  bm25: "BM25 全文搜索",
  baseline_vector: "纯向量搜索",
  hybrid_search: "混合搜索",
};

const TONE_CLASS: Record<string, string> = {
  success: "bg-green-100 text-green-700",
  warning: "bg-yellow-100 text-yellow-800",
  danger: "bg-red-100 text-red-700",
  muted: "bg-gray-100 text-gray-600",
  info: "bg-blue-100 text-blue-700",
};

// ── API Helpers (legacy + quality via evalApi) ─────────────────────

async function fetchDatasets(
  novelId?: number,
  status?: string,
  type?: string
): Promise<EvalDataset[]> {
  const res = await evalApi.listDatasets({
    novel_id: novelId,
    status,
    question_type: type,
  });
  return res.data;
}

async function updateDatasetStatus(
  id: number,
  status: string
): Promise<EvalDataset> {
  const res = await evalApi.updateDataset(id, { status });
  return res.data;
}

async function fetchRuns(novelId?: number): Promise<EvalRun[]> {
  const res = await evalApi.listRuns(novelId);
  return res.data;
}

async function fetchRunReport(runId: number): Promise<{
  report: EvalReport;
  deprecation?: DeprecationMeta;
  quality_comparable?: boolean;
}> {
  const res = await evalApi.getRun(runId);
  return {
    report: res.data.data as EvalReport,
    deprecation: res.data.deprecation,
    quality_comparable: res.data.quality_comparable,
  };
}

async function triggerEvalRun(
  runName: string,
  strategy: string,
  novelId: number,
  datasetIds: number[]
): Promise<DeprecationMeta | undefined> {
  const res = await evalApi.createRun({
    run_name: runName,
    strategy,
    novel_id: novelId,
    dataset_ids: datasetIds,
  });
  return res.data.deprecation;
}

// ── Quality status badge (exported for tests) ──────────────────────

export function QualityStatusBadge({ status }: { status: string }) {
  const tone = qualityStatusTone(status);
  const label = QUALITY_STATUS_LABELS[status] || status;
  return (
    <Badge
      data-testid={`quality-status-${status}`}
      className={cn("text-xs", TONE_CLASS[tone])}
    >
      {label}
    </Badge>
  );
}

export function DeprecationBanner({ meta }: { meta: DeprecationMeta }) {
  if (!meta?.deprecated) return null;
  return (
    <div
      data-testid="deprecation-banner"
      className="rounded-xl border border-amber-300/60 bg-amber-50 px-4 py-3 text-xs text-amber-900"
    >
      <p className="font-semibold">Legacy Eval API 已弃用</p>
      <p className="mt-1">
        请迁移到 {meta.replacement?.status || "GET /api/eval/quality/runs/{job_id}"}。
        {meta.migration}
      </p>
    </div>
  );
}

export function QualityJobsPanel({
  jobs,
  onResume,
  onSelect,
  selected,
}: {
  jobs: QualityJobPublic[];
  onResume?: (jobId: string) => void;
  onSelect?: (jobId: string) => void;
  selected?: QualityJobPublic | null;
}) {
  if (jobs.length === 0) {
    return (
      <EmptyState
        icon={<ClipboardList className="size-6" />}
        title="暂无质量评测任务"
        description="通过 POST /api/eval/quality/runs 创建 durable quality job"
      />
    );
  }
  return (
    <div className="space-y-3" data-testid="quality-jobs-panel">
      {jobs.map((job) => (
        <Card key={job.job_id} className="paper-surface rounded-2xl">
          <CardContent className="flex items-start justify-between gap-3 p-4">
            <div className="min-w-0 flex-1">
              <div className="mb-1 flex flex-wrap items-center gap-2">
                <QualityStatusBadge status={String(job.status)} />
                <span className="text-xs text-muted-foreground font-mono">
                  {job.job_id}
                </span>
                {!job.quality_comparable && (
                  <Badge variant="outline" className="text-xs">
                    metrics=null
                  </Badge>
                )}
              </div>
              {job.quality_comparable && job.metrics && (
                <p className="text-xs text-muted-foreground">
                  faithfulness={(job.metrics.answer_faithfulness ?? 0).toFixed(2)} ·
                  recall@5={(job.metrics.context_recall_at_5 ?? 0).toFixed(2)}
                </p>
              )}
              {job.error && (
                <p className="mt-1 text-xs text-red-600">{String(job.error)}</p>
              )}
            </div>
            <div className="flex shrink-0 gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => onSelect?.(job.job_id)}
              >
                查看报告
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => onResume?.(job.job_id)}
              >
                恢复
              </Button>
            </div>
          </CardContent>
        </Card>
      ))}
      {selected && (
        <Card className="border-primary/30" data-testid="quality-job-report">
          <CardHeader>
            <CardTitle className="text-base">
              质量报告: {selected.job_id}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <QualityStatusBadge status={String(selected.status)} />
            <p>
              quality_comparable:{" "}
              {selected.quality_comparable ? "true" : "false"}
            </p>
            <pre className="overflow-x-auto rounded-lg bg-muted/50 p-3 text-xs">
              {JSON.stringify(
                selected.quality_comparable ? selected.metrics : null,
                null,
                2
              )}
            </pre>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

// ── Page Component ─────────────────────────────────────────────────

export default function EvalPage() {
  const {
    qualityJobs,
    selectedJob,
    lastDeprecation,
    fetchAll,
    selectQualityJob,
    resumeQualityJob,
  } = useEval({ autoFetch: true });

  const [activeTab, setActiveTab] = useState("datasets");
  const [datasets, setDatasets] = useState<EvalDataset[]>([]);
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [selectedRun, setSelectedRun] = useState<EvalReport | null>(null);
  const [legacyDeprecation, setLegacyDeprecation] =
    useState<DeprecationMeta | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 筛选状态
  const [filterType, setFilterType] = useState<string>("all");
  const [filterStatus, setFilterStatus] = useState<string>("all");

  // 运行创建
  const [runName, setRunName] = useState("");
  const [runStrategy, setRunStrategy] = useState("bm25");
  const [runNovelId, setRunNovelId] = useState(1);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    loadData();
  }, []);

  async function loadData() {
    setLoading(true);
    setError(null);
    try {
      const [ds, rs] = await Promise.all([fetchDatasets(), fetchRuns()]);
      setDatasets(ds);
      setRuns(rs);
      await fetchAll();
    } catch {
      setError("加载数据失败，请确认后端服务已启动");
    } finally {
      setLoading(false);
    }
  }

  // 筛选数据集
  const filteredDatasets = useMemo(() => {
    let result = datasets;
    if (filterType !== "all") {
      result = result.filter((d) => d.question_type === filterType);
    }
    if (filterStatus !== "all") {
      result = result.filter((d) => d.status === filterStatus);
    }
    return result;
  }, [datasets, filterType, filterStatus]);

  // 确认/驳回
  async function handleStatusChange(id: number, newStatus: string) {
    try {
      await updateDatasetStatus(id, newStatus);
      setDatasets((prev) =>
        prev.map((d) => (d.id === id ? { ...d, status: newStatus } : d))
      );
    } catch (e) {
      console.error("状态更新失败", e);
    }
  }

  // 触发评测
  async function handleRunEval() {
    const confirmedIds = datasets
      .filter((d) => d.status === "confirmed")
      .map((d) => d.id);
    if (confirmedIds.length === 0) {
      alert("没有已确认的测试题，请先在数据集中确认题目");
      return;
    }
    setRunning(true);
    try {
      const dep = await triggerEvalRun(
        runName || `评测_${Date.now()}`,
        runStrategy,
        runNovelId,
        confirmedIds
      );
      if (dep) setLegacyDeprecation(dep);
      await loadData();
      setActiveTab("runs");
    } catch {
      alert("评测运行失败");
    } finally {
      setRunning(false);
    }
  }

  // 查看报告
  async function handleViewReport(runId: number) {
    try {
      const { report, deprecation } = await fetchRunReport(runId);
      setSelectedRun(report);
      if (deprecation) setLegacyDeprecation(deprecation);
    } catch (e) {
      console.error("加载报告失败", e);
    }
  }

  if (loading) {
    return (
      <div className="flex-1 p-8">
        <div className="animate-pulse space-y-4">
          <div className="h-8 w-48 bg-muted rounded" />
          <div className="h-64 bg-muted rounded" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex-1 p-8">
        <EmptyState icon={<TriangleAlert className="size-6" />} title="数据加载失败" description={error} />
      </div>
    );
  }

  return (
    <PageContainer className="space-y-7">
      <PageHeader eyebrow="Retrieval quality" title="RAG 评测" description="用统一数据集比较关键词、向量与混合检索策略，持续校准故事记忆的召回质量。" action={<Button onClick={loadData} variant="outline" className="rounded-full"><RefreshCw className="mr-2 size-4" />刷新数据</Button>} />
      {(legacyDeprecation || lastDeprecation) && (
        <DeprecationBanner meta={(legacyDeprecation || lastDeprecation)!} />
      )}
      {/* ── 上边栏导航 ── */}
      <div className="paper-surface sticky top-3 z-10 overflow-x-auto rounded-2xl p-1.5">
          <div className="flex min-w-max items-center justify-between">
            <div className="flex items-center gap-1">
              {[
                { key: "datasets", label: "评测数据集", count: datasets.length },
                { key: "runs", label: "评测运行", count: runs.length },
                { key: "quality", label: "质量任务", count: qualityJobs.length },
                { key: "compare", label: "指标对比" },
                { key: "trend", label: "趋势分析" },
              ].map((tab) => (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key)}
                  className={cn(
                    "relative cursor-pointer rounded-xl px-4 py-2.5 text-sm font-medium transition-colors",
                    activeTab === tab.key
                      ? "text-primary bg-primary/10"
                      : "text-muted-foreground hover:text-foreground hover:bg-accent"
                  )}
                >
                  {tab.label}
                  {tab.count !== undefined && (
                    <span className={cn(
                      "ml-1.5 text-xs rounded-full px-1.5 py-0.5",
                      activeTab === tab.key ? "bg-primary/20" : "bg-muted"
                    )}>
                      {tab.count}
                    </span>
                  )}
                  {activeTab === tab.key && (
                    <span className="absolute bottom-0 left-1/2 -translate-x-1/2 w-8 h-0.5 bg-primary rounded-full" />
                  )}
                </button>
              ))}
            </div>
          </div>
      </div>

      {/* ── 内容区 ── */}
      <div>

        {/* ── 数据集面板 ── */}
        {activeTab === "datasets" && (
          <div className="space-y-4">
            {/* 筛选栏 */}
            <div className="flex items-center gap-3">
              <select
                value={filterType}
                onChange={(e) => setFilterType(e.target.value)}
                className="px-3 py-1.5 rounded-lg border bg-background text-sm"
              >
                <option value="all">全部类型</option>
                {Object.entries(TYPE_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
              <select
                value={filterStatus}
                onChange={(e) => setFilterStatus(e.target.value)}
                className="px-3 py-1.5 rounded-lg border bg-background text-sm"
              >
                <option value="all">全部状态</option>
                {Object.entries(STATUS_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>{v}</option>
                ))}
              </select>
              <span className="text-xs text-muted-foreground">
                共 {filteredDatasets.length} 条
              </span>
            </div>

            {filteredDatasets.length === 0 ? (
              <EmptyState
                icon={<ClipboardList className="size-6" />}
                title="暂无评测数据"
                description="请先运行 generate_eval_candidates.py 生成候选测试题"
              />
            ) : (
              <div className="grid gap-3">
                {filteredDatasets.map((ds) => (
                  <Card key={ds.id} className="paper-surface rounded-2xl">
                    <CardContent className="p-4 flex items-start gap-4">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1 flex-wrap">
                          <Badge variant="outline" className="text-xs">
                            {TYPE_LABELS[ds.question_type] || ds.question_type}
                          </Badge>
                          <Badge
                            className={cn(
                              "text-xs",
                              ds.difficulty === "hard"
                                ? "bg-red-100 text-red-700"
                                : ds.difficulty === "medium"
                                ? "bg-yellow-100 text-yellow-700"
                                : "bg-green-100 text-green-700"
                            )}
                          >
                            {DIFFICULTY_LABELS[ds.difficulty] || ds.difficulty}
                          </Badge>
                          <Badge
                            className={cn(
                              "text-xs",
                              ds.status === "confirmed"
                                ? "bg-green-100 text-green-700"
                                : ds.status === "rejected"
                                ? "bg-red-100 text-red-700"
                                : "bg-gray-100 text-gray-600"
                            )}
                          >
                            {STATUS_LABELS[ds.status] || ds.status}
                          </Badge>
                        </div>
                        <p className="text-sm font-medium line-clamp-2">
                          {ds.question}
                        </p>
                        <div className="flex gap-4 mt-2 text-xs text-muted-foreground">
                          <span>gold_chunks: [{ds.gold_chunks.join(", ")}]</span>
                          {ds.expected_points && ds.expected_points.length > 0 && (
                            <span>
                              期望要点: {ds.expected_points.join(" / ")}
                            </span>
                          )}
                        </div>
                      </div>
                      <div className="flex gap-2 shrink-0">
                        {ds.status !== "confirmed" && (
                          <Button
                            size="sm"
                            variant="outline"
                            onClick={() => handleStatusChange(ds.id, "confirmed")}
                          >
                            <Check className="size-3.5" />确认
                          </Button>
                        )}
                        {ds.status !== "rejected" && (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => handleStatusChange(ds.id, "rejected")}
                          >
                            <X className="size-3.5" />驳回
                          </Button>
                        )}
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            )}
          </div>
        )}

        {/* ── 评测运行面板 ── */}
        {activeTab === "runs" && (
          <div className="space-y-6">
            {/* 创建 Run */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base">触发评测运行</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="flex items-center gap-3 flex-wrap">
                  <Input
                    placeholder="运行名称"
                    value={runName}
                    onChange={(e) => setRunName(e.target.value)}
                    className="w-48"
                  />
                  <select
                    value={runStrategy}
                    onChange={(e) => setRunStrategy(e.target.value)}
                    className="px-3 py-1.5 rounded-lg border bg-background text-sm"
                  >
                    {Object.entries(STRATEGY_LABELS).map(([k, v]) => (
                      <option key={k} value={k}>{v}</option>
                    ))}
                  </select>
                  <Input
                    type="number"
                    placeholder="小说 ID"
                    value={runNovelId}
                    onChange={(e) => setRunNovelId(Number(e.target.value))}
                    className="w-24"
                    min={1}
                  />
                  <Button onClick={handleRunEval} disabled={running} size="sm">
                    {running ? "执行中..." : "▶ 运行评测"}
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  将使用所有 &quot;已确认&quot; 状态的测试题 (
                  {datasets.filter((d) => d.status === "confirmed").length} 条)
                </p>
              </CardContent>
            </Card>

            {/* 历史运行 */}
            {runs.length === 0 ? (
              <EmptyState
                icon="🏃"
                title="暂无评测运行"
                description="创建一次评测运行以查看结果"
              />
            ) : (
              <div className="space-y-3">
                <h3 className="text-sm font-semibold">历史运行</h3>
                {runs.map((run) => (
                  <Card key={run.id}>
                    <CardContent className="p-4">
                      <div className="flex items-center justify-between">
                        <div>
                          <p className="text-sm font-medium">{run.run_name}</p>
                          <p className="text-xs text-muted-foreground">
                            {STRATEGY_LABELS[run.strategy] || run.strategy} ·{" "}
                            {run.total_questions} 题 ·{" "}
                            {new Date(run.created_at).toLocaleString("zh-CN")}
                          </p>
                          <div className="flex gap-3 mt-2 text-xs">
                            <MetricBadge
                              label="Recall@5"
                              value={(run.recall_at_k * 100).toFixed(1) + "%"}
                              good={run.recall_at_k > 0.5}
                            />
                            {run.precision_at_k != null && (
                              <MetricBadge
                                label="Prec@5"
                                value={(run.precision_at_k * 100).toFixed(1) + "%"}
                                good={run.precision_at_k > 0.5}
                              />
                            )}
                            {run.mrr != null && (
                              <MetricBadge
                                label="MRR"
                                value={run.mrr.toFixed(4)}
                                good={run.mrr > 0.5}
                              />
                            )}
                            {run.latency_ms != null && (
                              <span className="text-muted-foreground">
                                {run.latency_ms.toFixed(0)}ms
                              </span>
                            )}
                          </div>
                        </div>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => handleViewReport(run.id)}
                        >
                          查看报告
                        </Button>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            )}

            {/* 选中的报告详情 */}
            {selectedRun && (
              <Card className="border-primary/30">
                <CardHeader>
                  <div className="flex items-center justify-between">
                    <CardTitle className="text-base">
                      报告: {selectedRun.run.run_name}
                    </CardTitle>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setSelectedRun(null)}
                    >
                      关闭
                    </Button>
                  </div>
                </CardHeader>
                <CardContent className="space-y-4">
                  {/* 汇总 */}
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <div className="p-3 rounded-lg bg-muted/50">
                      <div className="text-xs text-muted-foreground">Recall@5</div>
                      <div className="text-lg font-bold">
                        {(selectedRun.run.recall_at_k * 100).toFixed(1)}%
                      </div>
                    </div>
                    <div className="p-3 rounded-lg bg-muted/50">
                      <div className="text-xs text-muted-foreground">Precision@5</div>
                      <div className="text-lg font-bold">
                        {selectedRun.run.precision_at_k != null
                          ? (selectedRun.run.precision_at_k * 100).toFixed(1) + "%"
                          : "-"}
                      </div>
                    </div>
                    <div className="p-3 rounded-lg bg-muted/50">
                      <div className="text-xs text-muted-foreground">MRR</div>
                      <div className="text-lg font-bold">
                        {selectedRun.run.mrr?.toFixed(4) ?? "-"}
                      </div>
                    </div>
                    <div className="p-3 rounded-lg bg-muted/50">
                      <div className="text-xs text-muted-foreground">错误案例</div>
                      <div className="text-lg font-bold text-red-600">
                        {selectedRun.error_cases.length}
                      </div>
                    </div>
                  </div>

                  {/* 错误案例 */}
                  {selectedRun.error_cases.length > 0 && (
                    <div>
                      <h4 className="text-sm font-semibold mb-2 text-red-600">
                        错误案例 (recall=0)
                      </h4>
                      <div className="space-y-2 max-h-48 overflow-y-auto">
                        {selectedRun.error_cases.map((ec) => {
                          const ds = datasets.find((d) => d.id === ec.dataset_id);
                          return (
                            <div
                              key={ec.id}
                              className="p-2 rounded bg-red-50 text-xs"
                            >
                              <p className="font-medium text-red-700">
                                {ds?.question || `dataset_id=${ec.dataset_id}`}
                              </p>
                              {ds && (
                                <p className="text-red-600 mt-0.5">
                                  期望 chunk: [{ds.gold_chunks.join(", ")}] · 实际召回:{" "}
                                  {ec.recalled_chunks.length > 0
                                    ? `[${ec.recalled_chunks.join(", ")}]`
                                    : "无"}
                                </p>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>
            )}
          </div>
        )}

        {/* ── 质量任务面板 (06-04/05) ── */}
        {activeTab === "quality" && (
          <QualityJobsPanel
            jobs={qualityJobs}
            selected={selectedJob}
            onSelect={(id) => void selectQualityJob(id)}
            onResume={(id) => void resumeQualityJob(id)}
          />
        )}

        {/* ── 指标对比面板 ── */}
        {activeTab === "compare" && (
          <CompareCharts runs={runs} />
        )}

        {/* ── 趋势分析面板 ── */}
        {activeTab === "trend" && (
          <TrendCharts runs={runs} />
        )}
      </div>
    </PageContainer>
  );
}

// ── Helper Components ──────────────────────────────────────────────

function MetricBadge({
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

// ── Chart Components ───────────────────────────────────────────────

/** 深色主题 ECharts 公共配置 */
const darkTheme = {
  textStyle: { color: "#94a3b8" },
  legend: { textStyle: { color: "#94a3b8" } },
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

function CompareCharts({ runs }: { runs: EvalRun[] }) {
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
    ...darkTheme,
    title: { text: "策略指标对比", left: "center", textStyle: { color: "#e2e8f0", fontSize: 14 } },
    tooltip: { trigger: "axis", valueFormatter: (v: unknown) => (typeof v === "number" ? (v * 100).toFixed(1) + "%" : String(v)) },
    legend: { bottom: 0, textStyle: { color: "#94a3b8" } },
    grid: { left: "3%", right: "4%", bottom: "12%", top: "15%", containLabel: true },
    xAxis: {
      type: "category",
      data: strategyRuns.map((r) => STRATEGY_LABELS[r.strategy] || r.strategy),
      axisLabel: { color: "#94a3b8", rotate: 15 },
    },
    yAxis: {
      type: "value",
      name: "得分",
      axisLabel: { color: "#94a3b8", formatter: (v: number) => (v * 100).toFixed(0) + "%" },
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
    ...darkTheme,
    title: { text: "延迟对比 (ms)", left: "center", textStyle: { color: "#e2e8f0", fontSize: 14 } },
    tooltip: { trigger: "axis" },
    grid: { left: "3%", right: "4%", bottom: "12%", top: "15%", containLabel: true },
    xAxis: {
      type: "category",
      data: strategyRuns.map((r) => STRATEGY_LABELS[r.strategy] || r.strategy),
      axisLabel: { color: "#94a3b8", rotate: 15 },
    },
    yAxis: { type: "value", name: "ms", axisLabel: { color: "#94a3b8" } },
    series: [{
      name: "延迟",
      type: "bar",
      data: strategyRuns.map((r) => r.latency_ms ?? 0),
      itemStyle: { color: "#8b5cf6" },
      label: { show: true, position: "top", color: "#94a3b8", formatter: (p: { value: number }) => p.value > 1000 ? (p.value / 1000).toFixed(1) + "s" : p.value.toFixed(0) + "ms" },
    }],
  };

  return (
    <div className="space-y-6">
      <Card><CardContent className="p-4"><ReactEChartsCore echarts={echarts} option={barOption} style={{ height: 300 }} /></CardContent></Card>
      <Card><CardContent className="p-4"><ReactEChartsCore echarts={echarts} option={latencyOption} style={{ height: 250 }} /></CardContent></Card>
    </div>
  );
}

function TrendCharts({ runs }: { runs: EvalRun[] }) {
  if (runs.length < 2) {
    return <EmptyState icon="📈" title="需要至少 2 次评测运行" description="多运行几次评测后查看指标变化趋势" />;
  }

  const sorted = [...runs].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());

  const commonGrid = { left: "3%", right: "4%", bottom: "12%", top: "15%", containLabel: true };
  const xData = sorted.map((r) => new Date(r.created_at).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }));

  const lineOption: echarts.EChartsCoreOption = {
    ...darkTheme,
    title: { text: "Recall / MRR / NDCG 趋势", left: "center", textStyle: { color: "#e2e8f0", fontSize: 14 } },
    tooltip: { trigger: "axis", valueFormatter: (v: unknown) => typeof v === "number" ? (v * 100).toFixed(1) + "%" : String(v) },
    legend: { bottom: 0, textStyle: { color: "#94a3b8" } },
    grid: { ...commonGrid },
    xAxis: { type: "category", data: xData, axisLabel: { color: "#94a3b8", rotate: 30, fontSize: 10 } },
    yAxis: { type: "value", name: "得分", axisLabel: { color: "#94a3b8", formatter: (v: number) => (v * 100).toFixed(0) + "%" }, max: 1 },
    series: [
      {
        name: "Recall@5", type: "line",
        data: sorted.map((r) => r.recall_at_k),
        lineStyle: { color: "#3b82f6" }, itemStyle: { color: "#3b82f6" },
        markLine: { silent: true, data: [{ yAxis: 0.5, label: { formatter: "目标 50%", color: "#94a3b8" } }], lineStyle: { type: "dashed", color: "#64748b" } },
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
    ...darkTheme,
    title: { text: "延迟趋势 (ms)", left: "center", textStyle: { color: "#e2e8f0", fontSize: 14 } },
    tooltip: { trigger: "axis" },
    legend: { bottom: 0, textStyle: { color: "#94a3b8" } },
    grid: { ...commonGrid },
    xAxis: { type: "category", data: xData, axisLabel: { color: "#94a3b8", rotate: 30, fontSize: 10 } },
    yAxis: { type: "value", name: "ms", axisLabel: { color: "#94a3b8" } },
    series: [{
      name: "延迟", type: "line",
      data: sorted.map((r) => r.latency_ms ?? 0),
      lineStyle: { color: "#8b5cf6" }, itemStyle: { color: "#8b5cf6" },
      areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: "rgba(139,92,246,0.25)" }, { offset: 1, color: "rgba(139,92,246,0.02)" }]) },
    }],
  };

  return (
    <div className="space-y-6">
      <Card><CardContent className="p-4"><ReactEChartsCore echarts={echarts} option={lineOption} style={{ height: 300 }} /></CardContent></Card>
      <Card><CardContent className="p-4"><ReactEChartsCore echarts={echarts} option={latencyLineOption} style={{ height: 250 }} /></CardContent></Card>
    </div>
  );
}
