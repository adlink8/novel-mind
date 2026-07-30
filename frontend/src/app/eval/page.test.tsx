import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import {
  QualityStatusBadge,
  DeprecationBanner,
  QualityJobsPanel,
} from "./eval-components";
import type { DeprecationMeta, QualityJobPublic } from "@/lib/api";

vi.mock("@/hooks/use-eval", () => ({
  useEval: () => ({
    qualityJobs: [],
    selectedJob: null,
    lastDeprecation: null,
    fetchAll: vi.fn(),
    selectQualityJob: vi.fn(),
    resumeQualityJob: vi.fn(),
  }),
}));

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
