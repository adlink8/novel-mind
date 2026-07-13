import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnalysisPage from "./page";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  startOrResume: vi.fn(),
  getTimeline: vi.fn(),
  status: vi.fn(),
  setFullBookPreference: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  novelsApi: { list: mocks.list },
  timelineApi: {
    startOrResume: mocks.startOrResume,
    getTimeline: mocks.getTimeline,
    status: mocks.status,
    setFullBookPreference: mocks.setFullBookPreference,
  },
}));

vi.mock("@/components/timeline/timeline-chart", () => ({
  TimelineChart: ({ events }: { events: unknown[] }) => <div data-testid="timeline-chart">{events.length} events</div>,
}));

const active = {
  source: "active",
  version_id: 7,
  status: "completed",
  progress: { visible_through_chapter: 1 },
  events: [{ id: 1, logical_event_id: "a", title: "旧版事件", description: "active only", event_type: "plot", narrative_chapter_number: 1, narrative_index: 0, story_rank: 2, time_precision: "unknown", confidence: 0.8, participants: [{ mention: "林墨" }], provenance: {} }],
  causal_edges: [], counts: { events: 1, participants: 1, causal_edges: 0 }, aggregates: { plot: 1 }, previews: ["旧版事件"],
};
const candidate = {
  ...active, source: "running_candidate", version_id: 8, status: "running",
  events: [{ ...active.events[0], id: 2, logical_event_id: "b", title: "候选事件" }], previews: ["候选事件"],
};

describe("global analysis timeline workspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.list.mockResolvedValue({ data: { items: [{ id: 11, title: "雾城", author: null, description: null, genre: null, word_count: 10, chapter_count: 3, status: "ready", reading_progress: null, created_at: "", updated_at: "" }], total: 1 } });
    mocks.startOrResume.mockResolvedValue({ data: { id: 3, novel_id: 11, status: "running", progress: {}, cancel_requested: false, updated_at: "2026-07-13T04:00:00Z" } });
    mocks.status.mockResolvedValue({ data: { id: 3, novel_id: 11, status: "running", progress: { completed_chapters: 1, total_chapters: 3 }, cancel_requested: false, updated_at: "2026-07-13T04:00:00Z" } });
    mocks.getTimeline.mockResolvedValue({ data: { active, running_candidate: candidate } });
    mocks.setFullBookPreference.mockResolvedValue({ data: {} });
  });

  it("starts on first selection and keeps active and candidate events separate", async () => {
    render(<AnalysisPage />);
    fireEvent.change(await screen.findByLabelText("选择小说"), { target: { value: "11" } });
    await waitFor(() => expect(mocks.startOrResume).toHaveBeenCalledWith("11"));
    expect(await screen.findByText("当前版本")).toBeInTheDocument();
    expect(screen.getByText("旧版事件")).toBeInTheDocument();
    expect(screen.queryByText("候选事件")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: /正在生成/ }));
    expect(screen.getByText("候选事件")).toBeInTheDocument();
    expect(screen.queryByText("旧版事件")).not.toBeInTheDocument();
    expect(screen.getByText(/无阅读进度.*第一章/)).toBeInTheDocument();
  });

  it("queries dual ordering, person and causal controls without intermediate modes", async () => {
    render(<AnalysisPage />);
    fireEvent.change(await screen.findByLabelText("选择小说"), { target: { value: "11" } });
    await screen.findByText("旧版事件");
    fireEvent.click(screen.getByRole("button", { name: "故事时间" }));
    fireEvent.change(screen.getByLabelText("筛选人物"), { target: { value: "林墨" } });
    fireEvent.click(screen.getByRole("checkbox", { name: "显示因果关系" }));
    await waitFor(() => expect(mocks.getTimeline).toHaveBeenLastCalledWith("11", expect.objectContaining({ ordering: "story", person: "林墨", causal: true, full_book: false })));
    expect(screen.queryByText(/剧情摘要|节拍|主题|节奏|章节总结|关系图/)).not.toBeInTheDocument();
  });

  it("requires confirmation before persisting full-book disclosure", async () => {
    render(<AnalysisPage />);
    fireEvent.change(await screen.findByLabelText("选择小说"), { target: { value: "11" } });
    await screen.findByText("旧版事件");
    fireEvent.click(screen.getByRole("checkbox", { name: "显示全书（可能剧透）" }));
    expect(screen.getByRole("dialog", { name: "确认显示全书" })).toBeInTheDocument();
    expect(mocks.setFullBookPreference).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认显示全书" }));
    await waitFor(() => expect(mocks.setFullBookPreference).toHaveBeenCalledWith("11", true));
  });
});
