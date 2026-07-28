import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import AnalysisPage from "./page";
import { TimelineStatus } from "@/components/timeline/timeline-status";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  startOrResume: vi.fn(),
  getTimeline: vi.fn(),
  status: vi.fn(),
  setFullBookPreference: vi.fn(),
  clueGetClues: vi.fn(),
  clueStatus: vi.fn(),
  clueStartOrResume: vi.fn(),
  nmListVersions: vi.fn(),
  nmGetTree: vi.fn(),
  nmGetClaims: vi.fn(),
  nmGetSourceLinks: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  novelsApi: { list: mocks.list },
  timelineApi: {
    startOrResume: mocks.startOrResume,
    getTimeline: mocks.getTimeline,
    status: mocks.status,
    setFullBookPreference: mocks.setFullBookPreference,
    cancel: vi.fn(),
  },
  relationshipsApi: {
    getGraph: vi.fn().mockResolvedValue({
      data: {
        novel_id: 11,
        version_id: 7,
        source: "active",
        through_chapter: 1,
        full_book: false,
        cutoff_chapter: 1,
        nodes: [],
        edges: [],
        counts: { nodes: 0, edges: 0, relation_types: {} },
        available_relation_types: [],
        available_character_ids: [],
        degradation: {
          mode: "normal",
          node_count: 0,
          edge_count: 0,
          hard_node_cap: 500,
          hard_edge_cap: 1500,
          message: null,
        },
        generated_at: null,
      },
    }),
    getEvidence: vi.fn(),
  },
}));

vi.mock("@/lib/narrative-memory-api", async () => {
  const actual = await vi.importActual<
    typeof import("@/lib/narrative-memory-api")
  >("@/lib/narrative-memory-api");
  return {
    ...actual,
    narrativeMemoryApi: {
      listVersions: mocks.nmListVersions,
      getTree: mocks.nmGetTree,
      getClaims: mocks.nmGetClaims,
      getSourceLinks: mocks.nmGetSourceLinks,
    },
  };
});

vi.mock("@/lib/clue-api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/clue-api")>(
    "@/lib/clue-api"
  );
  return {
    ...actual,
    clueApi: {
      getClues: mocks.clueGetClues,
      status: mocks.clueStatus,
      startOrResume: mocks.clueStartOrResume,
      cancel: vi.fn(),
      resume: vi.fn(),
      reanalyze: vi.fn(),
      getVersion: vi.fn(),
      getDetail: vi.fn(),
      compare: vi.fn(),
      rollback: vi.fn(),
      action: vi.fn(),
    },
  };
});

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("echarts-for-react/lib/core", () => ({
  default: () => <div data-testid="timeline-canvas" />,
}));

const active = {
  source: "active",
  version_id: 7,
  status: "completed",
  progress: { visible_through_chapter: 1 },
  events: [
    {
      id: 1,
      logical_event_id: "a",
      title: "旧版事件",
      description: "active only",
      event_type: "plot",
      narrative_chapter_number: 1,
      source_start: 0,
      narrative_index: 0,
      story_rank: 2,
      time_precision: "unknown",
      confidence: 0.8,
      participants: [{ mention: "林墨" }],
      provenance: {},
    },
  ],
  causal_edges: [],
  counts: { events: 1, participants: 1, causal_edges: 0 },
  aggregates: { plot: 1 },
  previews: ["旧版事件"],
};
const candidate = {
  ...active,
  source: "running_candidate",
  version_id: 8,
  status: "running",
  events: [
    {
      ...active.events[0],
      id: 2,
      logical_event_id: "b",
      title: "候选事件",
      participants: [{ mention: "顾遥" }],
    },
  ],
  previews: ["候选事件"],
};

async function expandEventList() {
  const btn = await screen.findByRole("button", { name: /展开全部列表/ });
  fireEvent.click(btn);
}

describe("global analysis timeline workspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.list.mockResolvedValue({
      data: {
        items: [
          {
            id: 11,
            title: "雾城",
            author: null,
            description: null,
            genre: null,
            word_count: 10,
            chapter_count: 3,
            status: "ready",
            reading_progress: null,
            created_at: "",
            updated_at: "",
          },
        ],
        total: 1,
      },
    });
    mocks.startOrResume.mockResolvedValue({
      data: {
        id: 3,
        novel_id: 11,
        status: "running",
        progress: {},
        cancel_requested: false,
        updated_at: "2026-07-13T04:00:00Z",
      },
    });
    mocks.status.mockResolvedValue({
      data: {
        id: 3,
        novel_id: 11,
        status: "running",
        progress: { completed_chapters: 1, total_chapters: 3 },
        cancel_requested: false,
        updated_at: "2026-07-13T04:00:00Z",
      },
    });
    mocks.getTimeline.mockResolvedValue({
      data: { active, running_candidate: candidate },
    });
    mocks.setFullBookPreference.mockResolvedValue({ data: {} });
    mocks.clueStatus.mockResolvedValue({
      data: {
        id: 1,
        novel_id: 11,
        version_id: 7,
        status: "completed",
        status_reason: null,
        progress: {},
        cancel_requested: false,
        updated_at: "2026-07-15T00:00:00Z",
      },
    });
    mocks.clueStartOrResume.mockResolvedValue({
      data: {
        id: 2,
        novel_id: 11,
        version_id: null,
        status: "pending",
        status_reason: null,
        progress: {},
        cancel_requested: false,
        updated_at: "2026-07-15T00:00:00Z",
      },
    });
    mocks.clueGetClues.mockResolvedValue({
      data: {
        active: {
          novel_id: 11,
          version_id: 7,
          source: "active",
          through_chapter: 1,
          full_book: false,
          cutoff_chapter: 1,
          clues: [
            {
              logical_clue_id: "c1",
              title: "雾中铃铛",
              derived_state: "active",
              narrative_chapter_number: 1,
              source_start: 0,
              confidence: 0.9,
              evidence_count: 1,
              link_count: 0,
              provenance: {},
            },
          ],
          counts: { clues: 1, by_state: { active: 1 } },
          available_states: ["active"],
          available_character_ids: [],
        },
        running_candidate: null,
      },
    });
    // Default: no NM candidate → chapter structure fallback
    mocks.nmListVersions.mockResolvedValue({
      data: {
        novel_id: 11,
        versions: [],
        publication_status: "candidate_preview",
      },
    });
    mocks.nmGetTree.mockResolvedValue({
      data: {
        novel_id: 11,
        version_id: 1,
        through_chapter: 3,
        publication_status: "candidate_preview",
        readiness: "empty",
        nodes: [],
      },
    });
    mocks.nmGetClaims.mockResolvedValue({
      data: {
        novel_id: 11,
        version_id: 1,
        node_id: 1,
        through_chapter: 3,
        publication_status: "candidate_preview",
        claims: [],
      },
    });
  });

  it("does not auto-start; live run prefers candidate events for chart/list", async () => {
    render(<AnalysisPage />);
    fireEvent.change(await screen.findByLabelText("选择小说"), {
      target: { value: "11" },
    });
    await waitFor(() => expect(mocks.status).toHaveBeenCalled());
    expect(mocks.startOrResume).not.toHaveBeenCalled();

    expect(await screen.findByRole("tab", { name: /正在生成/ })).toHaveAttribute(
      "aria-selected",
      "true"
    );
    expect(screen.getByRole("option", { name: "顾遥" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "林墨" })).not.toBeInTheDocument();

    await expandEventList();
    expect((await screen.findAllByText("候选事件")).length).toBeGreaterThan(0);
    expect(screen.queryByText("旧版事件")).not.toBeInTheDocument();
  });

  it("starts the timeline and clue pipelines from one analysis action", async () => {
    mocks.status.mockResolvedValue({
      data: { id: 3, novel_id: 11, status: "completed", progress: {}, cancel_requested: false, updated_at: "2026-07-13T04:00:00Z" },
    });
    render(<AnalysisPage />);
    fireEvent.change(await screen.findByLabelText("选择小说"), { target: { value: "11" } });
    fireEvent.click(await screen.findByRole("button", { name: "重新分析" }));

    await waitFor(() => expect(mocks.startOrResume).toHaveBeenCalledWith("11"));
    expect(mocks.clueStartOrResume).toHaveBeenCalledWith("11");
  });

  it("orders non-contiguous chapters by chapter, source offset and event id", async () => {
    // Structure scope uses chapter_count; must cover ch.9 for events to remain visible.
    mocks.list.mockResolvedValue({
      data: {
        items: [
          {
            id: 11,
            title: "雾城",
            author: null,
            description: null,
            genre: null,
            word_count: 10,
            chapter_count: 12,
            status: "ready",
            reading_progress: null,
            created_at: "",
            updated_at: "",
          },
        ],
        total: 1,
      },
    });
    const laterChapter = {
      ...active.events[0],
      id: 4,
      logical_event_id: "later",
      title: "第九章事件",
      narrative_chapter_number: 9,
      narrative_index: 0,
      source_start: 10,
    };
    const laterOffset = {
      ...active.events[0],
      id: 8,
      logical_event_id: "later-offset",
      title: "第二章后事件",
      narrative_chapter_number: 2,
      narrative_index: 0,
      source_start: 80,
    };
    const earlierOffset = {
      ...active.events[0],
      id: 7,
      logical_event_id: "earlier-offset",
      title: "第二章前事件",
      narrative_chapter_number: 2,
      narrative_index: 9,
      source_start: 20,
    };
    mocks.status.mockResolvedValue({
      data: {
        id: 3,
        novel_id: 11,
        status: "completed",
        progress: {},
        cancel_requested: false,
        updated_at: "2026-07-13T04:00:00Z",
      },
    });
    mocks.getTimeline.mockResolvedValue({
      data: {
        active: {
          ...active,
          events: [laterChapter, laterOffset, earlierOffset],
        },
        running_candidate: null,
      },
    });

    render(<AnalysisPage />);
    fireEvent.change(await screen.findByLabelText("选择小说"), {
      target: { value: "11" },
    });
    await expandEventList();
    await screen.findByText("第九章事件");

    const titles = screen
      .getAllByRole("heading", { level: 3 })
      .map((heading) => heading.textContent);
    expect(titles).toEqual(["第二章前事件", "第二章后事件", "第九章事件"]);
  });

  it("derives participant filters only from the selected version", async () => {
    mocks.status.mockResolvedValue({
      data: {
        id: 3,
        novel_id: 11,
        status: "completed",
        progress: {},
        cancel_requested: false,
        updated_at: "2026-07-13T04:00:00Z",
      },
    });
    render(<AnalysisPage />);
    fireEvent.change(await screen.findByLabelText("选择小说"), {
      target: { value: "11" },
    });
    await waitFor(() =>
      expect(screen.getByRole("option", { name: "林墨" })).toBeInTheDocument()
    );
    expect(screen.queryByRole("option", { name: "顾遥" })).not.toBeInTheDocument();

    // completed runs label the non-active tab as 候选结果 (not 正在生成)
    fireEvent.click(screen.getByRole("tab", { name: /候选结果|正在生成/ }));
    expect(screen.getByRole("option", { name: "顾遥" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "林墨" })).not.toBeInTheDocument();
  });

  it("queries dual ordering, person and causal controls without intermediate modes", async () => {
    mocks.status.mockResolvedValue({
      data: {
        id: 3,
        novel_id: 11,
        status: "completed",
        progress: {},
        cancel_requested: false,
        updated_at: "2026-07-13T04:00:00Z",
      },
    });
    render(<AnalysisPage />);
    fireEvent.change(await screen.findByLabelText("选择小说"), {
      target: { value: "11" },
    });
    await waitFor(() =>
      expect(screen.getByRole("option", { name: "林墨" })).toBeInTheDocument()
    );
    fireEvent.click(screen.getByRole("button", { name: "故事时间" }));
    fireEvent.change(screen.getByLabelText("筛选人物"), {
      target: { value: "林墨" },
    });
    fireEvent.click(screen.getByRole("checkbox", { name: "显示因果关系" }));
    await waitFor(() =>
      expect(mocks.getTimeline).toHaveBeenLastCalledWith(
        "11",
        expect.objectContaining({
          ordering: "story",
          person: "林墨",
          causal: true,
          full_book: false,
        })
      )
    );
    // Intermediate analysis modes must stay hidden (not the plot-segmentation copy "剧情节奏").
    expect(screen.queryByText(/剧情摘要|节拍|主题分析|章节总结|文风/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: /^(剧情摘要|节拍|主题|节奏|章节总结)$/ })
    ).not.toBeInTheDocument();
  });

  it("requires confirmation before persisting full-book disclosure", async () => {
    mocks.status.mockResolvedValue({
      data: {
        id: 3,
        novel_id: 11,
        status: "completed",
        progress: {},
        cancel_requested: false,
        updated_at: "2026-07-13T04:00:00Z",
      },
    });
    render(<AnalysisPage />);
    fireEvent.change(await screen.findByLabelText("选择小说"), {
      target: { value: "11" },
    });
    await waitFor(() =>
      expect(
        screen.getByRole("checkbox", { name: "显示全书（可能剧透）" })
      ).toBeInTheDocument()
    );
    fireEvent.click(screen.getByRole("checkbox", { name: "显示全书（可能剧透）" }));
    expect(screen.getByRole("dialog", { name: "确认显示全书" })).toBeInTheDocument();
    expect(mocks.setFullBookPreference).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认显示全书" }));
    await waitFor(() =>
      expect(mocks.setFullBookPreference).toHaveBeenCalledWith("11", true)
    );
  });

  it.each([
    ["empty", "尚未生成"],
    ["running", "正在分析"],
    ["partial", "已有部分结果"],
    ["paused_budget", "预算不足，已暂停"],
    ["failed", "分析失败"],
    ["completed", "分析完成"],
  ])("renders the %s progressive state", (status, label) => {
    render(
      <TimelineStatus
        hasEvents={false}
        run={{
          id: 1,
          novel_id: 11,
          status,
          progress: {},
          cancel_requested: false,
        }}
      />
    );
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("switches to clue workspace under one novel selector without intermediate menus", async () => {
    mocks.status.mockResolvedValue({
      data: {
        id: 3,
        novel_id: 11,
        status: "completed",
        progress: {},
        cancel_requested: false,
        updated_at: "2026-07-13T04:00:00Z",
      },
    });
    render(<AnalysisPage />);
    fireEvent.change(await screen.findByLabelText("选择小说"), {
      target: { value: "11" },
    });
    await waitFor(() =>
      expect(screen.getByRole("tab", { name: "线索与伏笔" })).toBeInTheDocument()
    );

    expect(screen.getByRole("tab", { name: "时间线" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "人物关系" })).toBeInTheDocument();
    // Avoid bare "节奏" — product copy uses "剧情节奏" for plot-based windows.
    expect(screen.queryByText(/剧情摘要|节拍|主题分析|章节总结|文风/)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: /^(剧情摘要|节拍|主题|节奏|章节总结)$/ })
    ).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "线索与伏笔" }));
    expect(await screen.findByTestId("clue-workspace")).toBeInTheDocument();
    expect((await screen.findAllByText("雾中铃铛")).length).toBeGreaterThan(0);
    // timeline canvas not required in clue view
    expect(screen.queryByTestId("timeline-canvas")).not.toBeInTheDocument();
    // still one novel selector
    expect(screen.getAllByLabelText("选择小说")).toHaveLength(1);
    // no top-level /clues route link
    expect(screen.queryByRole("link", { name: /线索/ })).not.toBeInTheDocument();
  });

  it("shares Phase 08 full-book confirmation from the clue workspace", async () => {
    mocks.status.mockResolvedValue({
      data: {
        id: 3,
        novel_id: 11,
        status: "completed",
        progress: {},
        cancel_requested: false,
        updated_at: "2026-07-13T04:00:00Z",
      },
    });
    render(<AnalysisPage />);
    fireEvent.change(await screen.findByLabelText("选择小说"), {
      target: { value: "11" },
    });
    fireEvent.click(await screen.findByRole("tab", { name: "线索与伏笔" }));
    await screen.findByTestId("clue-workspace");

    fireEvent.click(screen.getByLabelText("显示全书（可能剧透）"));
    expect(screen.getByRole("dialog", { name: "确认显示全书" })).toBeInTheDocument();
    expect(mocks.setFullBookPreference).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认显示全书" }));
    await waitFor(() =>
      expect(mocks.setFullBookPreference).toHaveBeenCalledWith("11", true)
    );
  });
});
