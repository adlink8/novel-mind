import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import EvalPage, {
  QualityStatusBadge,
  DeprecationBanner,
  QualityJobsPanel,
} from "./page";
import type { DeprecationMeta, QualityJobPublic, EvalDataset, EvalRun } from "@/lib/api";

const evalMocks = vi.hoisted(() => ({
  listDatasets: vi.fn(),
  updateDataset: vi.fn(),
  listRuns: vi.fn(),
  getRun: vi.fn(),
  createQualityRunFromNovel: vi.fn(),
  fetchAll: vi.fn(),
}));

vi.mock("@/hooks/use-eval", () => ({
  useEval: () => ({
    qualityJobs: [],
    selectedJob: null,
    lastDeprecation: null,
    fetchAll: evalMocks.fetchAll,
    createQualityRunFromNovel: evalMocks.createQualityRunFromNovel,
    selectQualityJob: vi.fn(),
    resumeQualityJob: vi.fn(),
  }),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    evalApi: {
      listDatasets: evalMocks.listDatasets,
      updateDataset: evalMocks.updateDataset,
      listRuns: evalMocks.listRuns,
      getRun: evalMocks.getRun,
    },
  };
});

vi.mock("echarts-for-react/lib/core", () => ({
  default: () => null,
}));
vi.mock("echarts/core", () => ({
  use: vi.fn(),
  graphic: { LinearGradient: vi.fn() },
}));
vi.mock("echarts/charts", () => ({ LineChart: {}, BarChart: {} }));
vi.mock("echarts/components", () => ({
  TitleComponent: {},
  TooltipComponent: {},
  LegendComponent: {},
  GridComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

const deprecation: DeprecationMeta = {
  deprecated: true,
  legacy_eval_api: true,
  replacement: {
    create: "POST /api/eval/quality/runs",
    status: "GET /api/eval/quality/runs/{job_id}",
    report: "GET /api/eval/quality/runs/{job_id}",
    resume: "POST /api/eval/quality/runs/{job_id}/resume",
    cancel: "POST /api/eval/quality/runs/{job_id}/cancel",
  },
  migration: "请使用 migrate_legacy_eval.py",
  quality_comparable_default: false,
};

describe("Eval UI components", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders badges for all required quality statuses", () => {
    const statuses = [
      "passed",
      "qualified",
      "failed_policy",
      "quality_regression",
      "blocked_dependency",
      "invalid_fixture",
      "invalid_lineage",
      "quarantined",
    ];
    for (const status of statuses) {
      const { unmount } = render(<QualityStatusBadge status={status} />);
      expect(screen.getByTestId(`quality-status-${status}`)).toBeInTheDocument();
      unmount();
    }
  });

  it("shows deprecation banner with replacement path", () => {
    render(<DeprecationBanner meta={deprecation} />);
    expect(screen.getByTestId("deprecation-banner")).toBeInTheDocument();
    expect(screen.getByText(/Legacy Eval API/i)).toBeInTheDocument();
    expect(screen.getByText(/quality\/runs/i)).toBeInTheDocument();
  });

  it("hides deprecation banner when not deprecated", () => {
    render(
      <DeprecationBanner meta={{ ...deprecation, deprecated: false }} />
    );
    expect(screen.queryByTestId("deprecation-banner")).toBeNull();
  });

  it("renders quality jobs panel with metrics=null for blocked", () => {
    const jobs: QualityJobPublic[] = [
      {
        job_id: "ok-1",
        status: "passed",
        quality_comparable: true,
        metrics: { answer_faithfulness: 0.97, context_recall_at_5: 0.88 },
      },
      {
        job_id: "bad-1",
        status: "blocked_dependency",
        quality_comparable: false,
        metrics: null,
        error: "ollama unavailable",
      },
    ];
    const onResume = vi.fn();
    const onSelect = vi.fn();
    render(
      <QualityJobsPanel
        jobs={jobs}
        onResume={onResume}
        onSelect={onSelect}
        selected={jobs[0]}
      />
    );
    expect(screen.getByTestId("quality-jobs-panel")).toBeInTheDocument();
    expect(screen.getByText("ok-1")).toBeInTheDocument();
    expect(screen.getByText("bad-1")).toBeInTheDocument();
    expect(screen.getAllByText("metrics=null").length).toBeGreaterThan(0);
    expect(screen.getByText(/ollama unavailable/)).toBeInTheDocument();
    expect(screen.getByTestId("quality-job-report")).toBeInTheDocument();

    fireEvent.click(screen.getAllByText("恢复")[1]);
    expect(onResume).toHaveBeenCalledWith("bad-1");
  });

  it("empty quality jobs shows empty state", () => {
    render(<QualityJobsPanel jobs={[]} />);
    expect(screen.getByText(/暂无质量评测任务/)).toBeInTheDocument();
  });
});

const dataset: EvalDataset = {
  id: 1,
  novel_id: 1,
  question_type: "timeline",
  difficulty: "medium",
  status: "candidate",
  question: "主角在第几章第一次登场？",
  gold_chunks: [1, 2],
  expected_points: ["登场章"],
  must_not_say: [],
  created_by: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const run: EvalRun = {
  id: 10,
  run_name: "BM25 第一轮",
  strategy: "bm25",
  novel_id: 1,
  total_questions: 2,
  recall_at_k: 0.5,
  precision_at_k: 0.3,
  mrr: 0.4,
  ndcg_at_k: 0.35,
  latency_ms: 120,
  cost_usd: null,
  config_snapshot: {},
  created_at: "2026-01-01T00:00:00Z",
};

describe("EvalPage main", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    evalMocks.listDatasets.mockResolvedValue({ data: [dataset] });
    evalMocks.listRuns.mockResolvedValue({ data: [run] });
    evalMocks.getRun.mockResolvedValue({
      data: {
        data: { run, error_cases: [] },
        deprecation: null,
        quality_comparable: true,
      },
    });
    evalMocks.fetchAll.mockResolvedValue(undefined);
    evalMocks.createQualityRunFromNovel.mockResolvedValue({ job_id: "q1" });
    evalMocks.updateDataset.mockResolvedValue({ data: dataset });
    vi.spyOn(window, "alert").mockImplementation(() => {});
  });

  it("加载完成后渲染数据集面板与筛选", async () => {
    render(<EvalPage />);
    expect(await screen.findByText("RAG 评测")).toBeInTheDocument();
    expect(screen.getByText("评测数据集")).toBeInTheDocument();
    expect(screen.getByText(/共 1 条/)).toBeInTheDocument();
    expect(screen.getByText(/主角在第几章第一次登场/)).toBeInTheDocument();
    expect(screen.getAllByText("时间线").length).toBeGreaterThan(0);
    // 筛选类型
    fireEvent.change(screen.getAllByRole("combobox")[0], {
      target: { value: "original_text" },
    });
    expect(screen.getByText(/共 0 条/)).toBeInTheDocument();
  });

  it("确认与驳回数据集更新状态", async () => {
    render(<EvalPage />);
    await screen.findByText(/主角在第几章第一次登场/);
    fireEvent.click(screen.getByRole("button", { name: "确认" }));
    await waitFor(() =>
      expect(evalMocks.updateDataset).toHaveBeenCalledWith(1, { status: "confirmed" })
    );
    // 已确认 → 不再显示确认按钮
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "确认" })).not.toBeInTheDocument()
    );
  });

  it("切换到评测运行面板并查看报告", async () => {
    render(<EvalPage />);
    await screen.findByText("RAG 评测");
    fireEvent.click(screen.getByText("评测运行"));
    expect(await screen.findByText("BM25 第一轮")).toBeInTheDocument();
    expect(screen.getByText(/BM25 全文搜索/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看报告" }));
    await waitFor(() => expect(evalMocks.getRun).toHaveBeenCalledWith(10));
    expect(await screen.findByText(/报告: BM25 第一轮/)).toBeInTheDocument();
  });

  it("运行自动评测：无已确认题目时提示", async () => {
    render(<EvalPage />);
    await screen.findByText("RAG 评测");
    fireEvent.click(screen.getByText("评测运行"));
    await screen.findByText("BM25 第一轮");
    fireEvent.click(screen.getByRole("button", { name: "运行自动评测" }));
    expect(window.alert).toHaveBeenCalledWith(
      "没有已确认的测试题，请先在数据集中确认题目"
    );
  });

  it("运行自动评测成功切到质量任务页", async () => {
    evalMocks.listDatasets.mockResolvedValue({
      data: [{ ...dataset, status: "confirmed" }],
    });
    render(<EvalPage />);
    await screen.findByText("RAG 评测");
    fireEvent.click(screen.getByText("评测运行"));
    await screen.findByText("BM25 第一轮");
    fireEvent.click(screen.getByRole("button", { name: "运行自动评测" }));
    await waitFor(() =>
      expect(evalMocks.createQualityRunFromNovel).toHaveBeenCalledWith(
        1,
        expect.any(Array)
      )
    );
    await screen.findByText("质量任务");
  });

  it("加载失败展示错误空状态", async () => {
    evalMocks.listDatasets.mockRejectedValue(new Error("boom"));
    render(<EvalPage />);
    expect(await screen.findByText("数据加载失败")).toBeInTheDocument();
    expect(screen.getByText("加载数据失败，请确认后端服务已启动")).toBeInTheDocument();
  });

  it("切到指标对比与趋势页", async () => {
    evalMocks.listRuns.mockResolvedValue({
      data: [
        run,
        { ...run, id: 11, run_name: "第二轮", created_at: "2026-02-01T00:00:00Z" },
      ],
    });
    render(<EvalPage />);
    await screen.findByText("RAG 评测");
    fireEvent.click(screen.getByText("指标对比"));
    // 图表被 mock 成 null，但卡片容器仍在（有数据时无 EmptyState）
    expect(screen.queryByText(/暂无评测数据/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("趋势分析"));
    expect(screen.queryByText(/需要至少 2 次评测运行/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("质量任务"));
    expect(screen.getByText(/暂无质量评测任务/)).toBeInTheDocument();
  });
});
