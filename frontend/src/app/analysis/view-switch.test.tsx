/**
 * Phase 25.1-02 — 分析页顶层视图切换（对话默认 | 分析可视化）。
 * 切换只做 CSS 隐藏：对话草稿、facet tab 选择等状态必须保留。
 */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AnalysisPage from "./page";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  getChapters: vi.fn(),
  startOrResume: vi.fn(),
  getTimeline: vi.fn(),
  status: vi.fn(),
  setFullBookPreference: vi.fn(),
  listConversations: vi.fn(),
  createConversation: vi.fn(),
  listMessages: vi.fn(),
  createMessage: vi.fn(),
  clueGetClues: vi.fn(),
  clueStatus: vi.fn(),
  clueStartOrResume: vi.fn(),
  nmListVersions: vi.fn(),
  nmGetTree: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    novelsApi: { list: mocks.list, getChapters: mocks.getChapters },
    timelineApi: {
      startOrResume: mocks.startOrResume,
      getTimeline: mocks.getTimeline,
      status: mocks.status,
      setFullBookPreference: mocks.setFullBookPreference,
      cancel: vi.fn(),
    },
    readerChatApi: {
      listConversations: mocks.listConversations,
      createConversation: mocks.createConversation,
      listMessages: mocks.listMessages,
      createMessage: mocks.createMessage,
      getJob: vi.fn(),
      cancelJob: vi.fn(),
      retryJob: vi.fn(),
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
  };
});

vi.mock("@/lib/narrative-memory-api", async () => {
  const actual = await vi.importActual<
    typeof import("@/lib/narrative-memory-api")
  >("@/lib/narrative-memory-api");
  return {
    ...actual,
    narrativeMemoryApi: {
      listVersions: mocks.nmListVersions,
      getTree: mocks.nmGetTree,
      getClaims: vi.fn().mockResolvedValue({
        data: {
          novel_id: 11,
          version_id: 1,
          node_id: 1,
          through_chapter: 3,
          publication_status: "candidate_preview",
          claims: [],
        },
      }),
      getSourceLinks: vi.fn().mockResolvedValue({
        data: {
          novel_id: 11,
          version_id: 1,
          node_id: 1,
          through_chapter: 3,
          publication_status: "candidate_preview",
          source_links: [],
        },
      }),
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
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}));

vi.mock("echarts-for-react/lib/core", () => ({
  default: () => <div data-testid="timeline-canvas" />,
}));

const activeView = {
  source: "active",
  version_id: 7,
  status: "completed",
  progress: { visible_through_chapter: 3 },
  events: [],
  causal_edges: [],
  counts: { events: 0, participants: 0, causal_edges: 0 },
  aggregates: {},
  previews: [],
};

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
  mocks.getChapters.mockResolvedValue({
    data: [
      { id: 31, novel_id: 11, chapter_number: 1, title: "第一章", content: "", word_count: 1, created_at: "", updated_at: "" },
      { id: 32, novel_id: 11, chapter_number: 2, title: "第二章", content: "", word_count: 1, created_at: "", updated_at: "" },
      { id: 33, novel_id: 11, chapter_number: 3, title: "第三章", content: "", word_count: 1, created_at: "", updated_at: "" },
    ],
  });
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
    data: { active: activeView, running_candidate: null },
  });
  mocks.setFullBookPreference.mockResolvedValue({ data: {} });
  mocks.listConversations.mockResolvedValue({
    data: { items: [], total: 0, skip: 0, limit: 50 },
  });
  mocks.listMessages.mockResolvedValue({
    data: { items: [], total: 0, skip: 0, limit: 200, after_sequence: 0 },
  });
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
  mocks.clueGetClues.mockResolvedValue({
    data: { active: null, running_candidate: null },
  });
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
});

afterEach(() => cleanup());

async function selectNovel() {
  render(<AnalysisPage />);
  fireEvent.change(await screen.findByLabelText("选择小说"), {
    target: { value: "11" },
  });
  await waitFor(() =>
    expect(screen.getByTestId("analysis-chat-panel")).toBeInTheDocument()
  );
}

describe("analysis page top-level view switch (Phase 25.1-02)", () => {
  it("defaults to the chat view and hides visualization via CSS only", async () => {
    await selectNovel();

    expect(screen.getByTestId("analysis-view-tab-chat")).toHaveAttribute(
      "aria-selected",
      "true"
    );
    expect(screen.getByTestId("analysis-view-tab-analysis")).toHaveAttribute(
      "aria-selected",
      "false"
    );
    // 可视化视图保持挂载（状态保留），仅 CSS 隐藏
    expect(
      screen.getByTestId("analysis-visualization-view").className
    ).toMatch(/(?:^| )hidden(?: |$)/);
    expect(screen.getByTestId("analysis-chat-panel").className).not.toMatch(
      /(?:^| )hidden(?: |$)/
    );
  });

  it("shows the spoiler boundary from reading progress in the chat context", async () => {
    await selectNovel();
    await waitFor(() =>
      expect(screen.getByTestId("analysis-chat-boundary")).toHaveTextContent(
        "基于你已读至第 1 章"
      )
    );
    // 锚点=结构：默认选中节点范围出现在上下文提示
    expect(screen.getByTestId("analysis-chat-context")).toHaveTextContent(
      "范围："
    );
  });

  it("preserves chat draft and facet tab selection across view switches", async () => {
    await selectNovel();

    // 对话视图：输入草稿
    fireEvent.change(screen.getByTestId("analysis-chat-input"), {
      target: { value: "主角的动机是什么？" },
    });

    // 切到分析视图，选中「线索与伏笔」facet
    fireEvent.click(screen.getByTestId("analysis-view-tab-analysis"));
    expect(
      screen.getByTestId("analysis-visualization-view").className
    ).not.toMatch(/(?:^| )hidden(?: |$)/);
    expect(screen.getByTestId("analysis-chat-panel").className).toMatch(
      /(?:^| )hidden(?: |$)/
    );
    fireEvent.click(screen.getByRole("tab", { name: "线索与伏笔" }));
    await screen.findByTestId("clue-workspace");

    // 切回对话：草稿仍在（未卸载重建）
    fireEvent.click(screen.getByTestId("analysis-view-tab-chat"));
    expect(
      (screen.getByTestId("analysis-chat-input") as HTMLTextAreaElement).value
    ).toBe("主角的动机是什么？");

    // 再切回分析：facet 仍停留在线索视图
    fireEvent.click(screen.getByTestId("analysis-view-tab-analysis"));
    expect(screen.getByTestId("clue-workspace")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "线索与伏笔" })).toHaveAttribute(
      "aria-selected",
      "true"
    );
  });

  it("keeps the chat empty state honest (no fabricated messages)", async () => {
    await selectNovel();
    await waitFor(() =>
      expect(screen.getByTestId("analysis-chat-empty")).toBeInTheDocument()
    );
    expect(mocks.createMessage).not.toHaveBeenCalled();
  });
});
