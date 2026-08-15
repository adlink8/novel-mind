"use client";

import { useCallback, useEffect, useState, useMemo } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/empty-state";
import { cn } from "@/lib/utils";
import {
  evalApi,
  type EvalDataset,
  type EvalRun,
  type EvalReport,
  type DeprecationMeta,
} from "@/lib/api";
import { useEval } from "@/hooks/use-eval";
import { Check, ClipboardList, RefreshCw, TriangleAlert, X } from "lucide-react";
import { PageContainer, PageHeader } from "@/components/page-header";
import { ChapterOrnament } from "@/components/chapter-ornament";
import { Skeleton } from "@/components/ui/skeleton";
import {
  DeprecationBanner,
  QualityJobsPanel,
} from "@/components/eval-quality-panels";
import {
  TYPE_LABELS,
  STATUS_LABELS,
  DIFFICULTY_LABELS,
  STRATEGY_LABELS,
} from "@/components/eval/labels";
import {
  CompareCharts,
  TrendCharts,
  MetricBadge,
} from "@/components/eval/charts";

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

// ── Page Component ─────────────────────────────────────────────────

export default function EvalPage() {
  const {
    qualityJobs,
    selectedJob,
    lastDeprecation,
    fetchAll,
    createQualityRunFromNovel,
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
  const [runNovelId, setRunNovelId] = useState(1);
  const [running, setRunning] = useState(false);

  const loadData = useCallback(async () => {
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
  }, [fetchAll]);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) void loadData();
    });
    return () => {
      cancelled = true;
    };
  }, [loadData]);

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

  // 从服务端可信 lineage 触发自动质量评测；legacy runs 仅保留历史展示。
  async function handleRunEval() {
    const confirmedIds = datasets
      .filter(
        (d) => d.novel_id === runNovelId && d.status === "confirmed"
      )
      .map((d) => d.id);
    if (confirmedIds.length === 0) {
      alert("没有已确认的测试题，请先在数据集中确认题目");
      return;
    }
    setRunning(true);
    try {
      const created = await createQualityRunFromNovel(runNovelId, confirmedIds);
      if (!created) {
        throw new Error("质量任务创建失败");
      }
      await fetchAll();
      setActiveTab("quality");
    } catch {
      alert("自动评测启动失败：请确认题目已确认，且小说已有可信 active chunk/source lineage");
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
      <PageContainer className="space-y-7">
        <div className="space-y-3 border-b border-border/70 pb-7">
          <Skeleton className="h-3 w-28" />
          <Skeleton className="h-9 w-56" />
          <Skeleton className="h-4 w-96 max-w-full" />
        </div>
        <Skeleton className="h-12 w-full rounded-2xl" />
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full rounded-2xl" />
          ))}
        </div>
      </PageContainer>
    );
  }

  if (error) {
    return (
      <PageContainer>
        <EmptyState icon={<TriangleAlert className="size-6" />} title="数据加载失败" description={error} />
      </PageContainer>
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
      <ChapterOrnament />
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
            <Card className="paper-surface rounded-2xl">
              <CardHeader>
                <CardTitle className="text-base">运行自动评测</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="flex items-center gap-3 flex-wrap">
                  <Input
                    type="number"
                    placeholder="小说 ID"
                    value={runNovelId}
                    onChange={(e) => setRunNovelId(Number(e.target.value))}
                    className="w-24"
                    min={1}
                  />
                  <Button onClick={handleRunEval} disabled={running} size="sm">
                    {running ? "启动中..." : "运行自动评测"}
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  服务端将验证所有已确认题目、active chunk build、冻结 fixture 与模型校准谱系；
                  缺少可信谱系时会拒绝创建，不生成伪可比较指标。（
                  {datasets.filter(
                    (d) => d.novel_id === runNovelId && d.status === "confirmed"
                  ).length} 条已确认）
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
                <h3 className="text-sm font-semibold">Legacy 历史运行</h3>
                {runs.map((run) => (
                  <Card key={run.id} className="paper-surface rounded-2xl">
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
              <Card className="paper-surface rounded-2xl border-primary/30">
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
