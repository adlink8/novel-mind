import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AnalysisPage from "./page";
import { RelationshipGraph } from "@/components/relationships/relationship-graph";
import type {
  RelationshipGraphEdge,
  RelationshipGraphEnvelope,
  RelationshipGraphNode,
} from "@/lib/api";

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  startOrResume: vi.fn(),
  getTimeline: vi.fn(),
  status: vi.fn(),
  setFullBookPreference: vi.fn(),
  getGraph: vi.fn(),
  getEvidence: vi.fn(),
  cyDestroy: vi.fn(),
  cyOn: vi.fn(),
  cyRemoveAllListeners: vi.fn(),
  cyElements: vi.fn(),
  cyGetElementById: vi.fn(),
  cyZoom: vi.fn(),
  cyFit: vi.fn(),
  cyWidth: vi.fn(() => 400),
  cyHeight: vi.fn(() => 420),
  layoutRun: vi.fn(),
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
    getGraph: mocks.getGraph,
    getEvidence: mocks.getEvidence,
  },
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("echarts-for-react/lib/core", () => ({
  default: () => <div data-testid="timeline-canvas" />,
}));

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) => <a href={href}>{children}</a>,
}));

vi.mock("cytoscape", () => {
  const factory = vi.fn(() => {
    const unselect = vi.fn();
    const select = vi.fn();
    mocks.cyElements.mockReturnValue({ unselect });
    mocks.cyGetElementById.mockReturnValue({ select });
    return {
      on: mocks.cyOn,
      destroy: mocks.cyDestroy,
      removeAllListeners: mocks.cyRemoveAllListeners,
      elements: mocks.cyElements,
      getElementById: mocks.cyGetElementById,
      zoom: mocks.cyZoom,
      fit: mocks.cyFit,
      width: mocks.cyWidth,
      height: mocks.cyHeight,
      layout: () => ({ run: mocks.layoutRun }),
    };
  });
  return { default: factory, __esModule: true };
});

const nodes: RelationshipGraphNode[] = [
  {
    character_id: 1,
    name: "林墨",
    aliases: [],
    first_visible_chapter: 1,
  },
  {
    character_id: 2,
    name: "顾遥",
    aliases: [],
    first_visible_chapter: 1,
  },
];

const edges: RelationshipGraphEdge[] = [
  {
    observation_id: 101,
    source_character_id: 1,
    target_character_id: 2,
    relation_type: "ally",
    transition: "establish",
    confidence: 0.92,
    valid_from_chapter: 1,
    valid_to_chapter: null,
    provenance: "machine",
    evidence_preview: "并肩作战",
    evidence_count: 1,
  },
];

function makeEnvelope(
  overrides: Partial<RelationshipGraphEnvelope> = {}
): RelationshipGraphEnvelope {
  return {
    novel_id: 11,
    version_id: 7,
    source: "active",
    through_chapter: 1,
    full_book: false,
    cutoff_chapter: 1,
    nodes,
    edges,
    counts: { nodes: 2, edges: 1, relation_types: { ally: 1 } },
    available_relation_types: ["ally", "enemy"],
    available_character_ids: [1, 2],
    degradation: {
      mode: "normal",
      node_count: 2,
      edge_count: 1,
      hard_node_cap: 500,
      hard_edge_cap: 1500,
      message: null,
    },
    generated_at: null,
    ...overrides,
  };
}

const activeTimeline = {
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
      narrative_chapter_number: 2,
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

const candidateTimeline = {
  ...activeTimeline,
  source: "running_candidate",
  version_id: 8,
  status: "running",
  events: [
    {
      ...activeTimeline.events[0],
      id: 2,
      logical_event_id: "b",
      title: "候选事件",
      participants: [{ mention: "顾遥" }],
    },
  ],
  previews: ["候选事件"],
};

async function selectNovelAndOpenRelationships() {
  render(<AnalysisPage />);
  fireEvent.change(await screen.findByLabelText("选择小说"), {
    target: { value: "11" },
  });
  await waitFor(() => expect(mocks.status).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("tab", { name: "人物关系" }));
  await waitFor(() => expect(mocks.getGraph).toHaveBeenCalled());
}

describe("analysis relationship workspace (09-04)", () => {
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
      data: { active: activeTimeline, running_candidate: candidateTimeline },
    });
    mocks.setFullBookPreference.mockResolvedValue({ data: {} });
    mocks.getGraph.mockResolvedValue({ data: makeEnvelope() });
    mocks.getEvidence.mockResolvedValue({
      data: {
        observation_id: 101,
        novel_id: 11,
        version_id: 7,
        through_chapter: 1,
        relation_type: "ally",
        source_character_id: 1,
        target_character_id: 2,
        provenance: "machine",
        evidence: [
          {
            evidence_id: "ev-1",
            chapter_id: 2,
            source_start: 0,
            source_end: 10,
            content_hash: "b".repeat(64),
            excerpt: "并肩作战的证据",
          },
        ],
      },
    });
  });

  afterEach(() => {
    cleanup();
  });

  it("switches workspace without intermediate summary modes", async () => {
    await selectNovelAndOpenRelationships();
    expect(screen.getByTestId("relationship-workspace")).toBeInTheDocument();
    expect(screen.getByTestId("relationship-canvas")).toBeInTheDocument();
    const body = document.body.textContent ?? "";
    expect(body).not.toMatch(/剧情摘要|主题分析|文风|节奏|章节摘要|plot summary|theme|pace/i);
    expect(screen.queryByRole("tab", { name: /剧情|主题|文风|节奏/ })).toBeNull();
  });

  it("requests graph with source/version/full_book and omits owner_id", async () => {
    await selectNovelAndOpenRelationships();
    await waitFor(() => {
      expect(mocks.getGraph).toHaveBeenCalledWith(
        "11",
        expect.objectContaining({
          source: "active",
          version_id: 7,
          full_book: false,
        })
      );
    });
    const params = mocks.getGraph.mock.calls[0][1] as Record<string, unknown>;
    expect(params).not.toHaveProperty("owner_id");
    // Default path does not opt into provisional co-occurrence layer.
    expect(params.include_provisional).not.toBe(true);
  });

  it("toggle 显示临时共现 refetches with include_provisional=true", async () => {
    await selectNovelAndOpenRelationships();
    const toggle = await screen.findByTestId("relationship-include-provisional");
    expect(toggle).not.toBeChecked();

    mocks.getGraph.mockResolvedValue({
      data: makeEnvelope({
        edges: [
          {
            ...edges[0],
            observation_id: 101,
            relation_type: "ally",
            edge_kind: "accepted_observation",
          },
          {
            observation_id: 202,
            source_character_id: 1,
            target_character_id: 2,
            relation_type: "cooccur",
            transition: "establish",
            confidence: 0.4,
            valid_from_chapter: 1,
            valid_to_chapter: null,
            provenance: "machine",
            evidence_preview: "共现 · 非已确认关系",
            evidence_count: 2,
            edge_kind: "provisional_cooccurrence",
            suggested_type: "ally",
          },
        ],
        counts: {
          nodes: 2,
          edges: 2,
          relation_types: { ally: 1, cooccur: 1 },
        },
        available_relation_types: ["ally", "enemy", "cooccur"],
      }),
    });

    fireEvent.click(toggle);
    await waitFor(() => {
      const last = mocks.getGraph.mock.calls.at(-1)?.[1] as {
        include_provisional?: boolean;
      };
      expect(last.include_provisional).toBe(true);
    });
    const banner = await screen.findByTestId("relationship-provisional-banner");
    expect(banner).toBeInTheDocument();
    expect(banner.textContent).toMatch(/临时共现/);
  });

  it("shows honesty banner when only provisional edges are returned", async () => {
    mocks.getGraph.mockResolvedValue({
      data: makeEnvelope({
        edges: [
          {
            observation_id: 303,
            source_character_id: 1,
            target_character_id: 2,
            relation_type: "cooccur",
            transition: "establish",
            confidence: 0.3,
            valid_from_chapter: 1,
            valid_to_chapter: null,
            provenance: "machine",
            evidence_preview: "共现 · 非已确认关系",
            evidence_count: 1,
            edge_kind: "provisional_cooccurrence",
            suggested_type: "enemy",
          },
        ],
        counts: { nodes: 2, edges: 1, relation_types: { cooccur: 1 } },
        available_relation_types: ["cooccur"],
      }),
    });
    await selectNovelAndOpenRelationships();
    const banner = await screen.findByTestId("relationship-provisional-banner");
    expect(banner.textContent).toMatch(/临时共现/);
    expect(banner.textContent).toMatch(/不是已确认/);
    const list = screen.getByTestId("relationship-companion-list");
    expect(within(list).getByText(/临时共现/)).toBeInTheDocument();
  });

  it("evidence panel uses non-assertive copy for provisional edges", async () => {
    mocks.getGraph.mockResolvedValue({
      data: makeEnvelope({
        edges: [
          {
            observation_id: 404,
            source_character_id: 1,
            target_character_id: 2,
            relation_type: "cooccur",
            transition: "establish",
            confidence: 0.35,
            valid_from_chapter: 1,
            valid_to_chapter: null,
            provenance: "machine",
            evidence_preview: "同场出场",
            evidence_count: 1,
            edge_kind: "provisional_cooccurrence",
            suggested_type: "ally",
          },
        ],
        counts: { nodes: 2, edges: 1, relation_types: { cooccur: 1 } },
      }),
    });
    mocks.getEvidence.mockResolvedValue({
      data: {
        observation_id: 404,
        novel_id: 11,
        version_id: 7,
        through_chapter: 1,
        relation_type: "ally",
        source_character_id: 1,
        target_character_id: 2,
        provenance: "machine",
        evidence: [],
      },
    });
    await selectNovelAndOpenRelationships();
    fireEvent.click(await screen.findByText(/林墨 → 顾遥/));
    const panel = await screen.findByTestId("relationship-evidence-panel");
    expect(
      within(panel).getByTestId("relationship-evidence-provisional-note")
    ).toHaveTextContent(/不是已确认/);
    expect(within(panel).getByText(/临时共现/)).toBeInTheDocument();
    expect(within(panel).queryByText(/^机器推断$/)).toBeNull();
  });

  it("renders normal mode with canvas and same companion list set", async () => {
    await selectNovelAndOpenRelationships();
    expect(await screen.findByTestId("relationship-companion-list")).toBeInTheDocument();
    const list = screen.getByTestId("relationship-companion-list");
    expect(within(list).getByText("林墨")).toBeInTheDocument();
    expect(within(list).getByText("顾遥")).toBeInTheDocument();
    expect(within(list).getByText(/林墨 → 顾遥/)).toBeInTheDocument();
  });

  it("large mode still mounts canvas with degradation notice", async () => {
    mocks.getGraph.mockResolvedValue({
      data: makeEnvelope({
        degradation: {
          mode: "large",
          node_count: 300,
          edge_count: 800,
          hard_node_cap: 500,
          hard_edge_cap: 1500,
          message: "large",
        },
      }),
    });
    await selectNovelAndOpenRelationships();
    expect(await screen.findByTestId("relationship-canvas")).toBeInTheDocument();
    expect(screen.getByText(/大图模式/)).toBeInTheDocument();
  });

  it("filters_required does not mount cytoscape canvas with partial elements", async () => {
    mocks.getGraph.mockResolvedValue({
      data: makeEnvelope({
        nodes: [],
        edges: [],
        counts: { nodes: 900, edges: 2000, relation_types: {} },
        degradation: {
          mode: "filters_required",
          node_count: 900,
          edge_count: 2000,
          hard_node_cap: 500,
          hard_edge_cap: 1500,
          message: "narrow filters",
        },
      }),
    });
    await selectNovelAndOpenRelationships();
    expect(
      await screen.findByTestId("relationship-filters-required")
    ).toBeInTheDocument();
    expect(screen.queryByTestId("relationship-canvas")).toBeNull();
    expect(screen.getByText(/超过上限|请先用人物/)).toBeInTheDocument();
  });

  it("active/candidate switch refetches and clears stale selection", async () => {
    await selectNovelAndOpenRelationships();
    const edgeBtn = await screen.findByText(/林墨 → 顾遥/);
    fireEvent.click(edgeBtn);
    await waitFor(() => expect(mocks.getEvidence).toHaveBeenCalled());

    mocks.getGraph.mockResolvedValue({
      data: makeEnvelope({
        version_id: 8,
        source: "running_candidate",
        edges: [
          {
            ...edges[0],
            observation_id: 202,
            relation_type: "enemy",
            evidence_preview: "候选敌对",
          },
        ],
        counts: { nodes: 2, edges: 1, relation_types: { enemy: 1 } },
      }),
    });

    fireEvent.click(screen.getByRole("tab", { name: /候选结果|正在生成/ }));
    await waitFor(() => {
      const last = mocks.getGraph.mock.calls.at(-1)?.[1] as {
        source: string;
        version_id: number;
      };
      expect(last.source).toBe("running_candidate");
      expect(last.version_id).toBe(8);
    });
    // Evidence panel for old edge should close when selection cleared
    await waitFor(() => {
      expect(screen.queryByTestId("relationship-evidence-panel")).toBeNull();
    });
  });

  it("edge selection opens evidence panel with chapter navigation", async () => {
    await selectNovelAndOpenRelationships();
    fireEvent.click(await screen.findByText(/林墨 → 顾遥/));
    const panel = await screen.findByTestId("relationship-evidence-panel");
    expect(within(panel).getAllByText(/机器推断/).length).toBeGreaterThan(0);
    await waitFor(() => {
      expect(within(panel).getByText("并肩作战的证据")).toBeInTheDocument();
    });
    const link = within(panel).getByRole("link", { name: /跳转章节阅读/ });
    expect(link).toHaveAttribute(
      "href",
      "/novels/11?chapter=2&from=relationships"
    );
  });

  it("person filter refetches server-filtered graph", async () => {
    await selectNovelAndOpenRelationships();
    fireEvent.change(screen.getByLabelText("筛选人物"), {
      target: { value: "1" },
    });
    await waitFor(() => {
      const last = mocks.getGraph.mock.calls.at(-1)?.[1] as {
        character_id?: number;
      };
      expect(last.character_id).toBe(1);
    });
  });

  it("destroys cytoscape instance on unmount / workspace leave", async () => {
    await selectNovelAndOpenRelationships();
    await waitFor(() => expect(screen.getByTestId("relationship-canvas")).toBeInTheDocument());
    // allow cytoscape dynamic import + mount
    await waitFor(() => expect(mocks.cyOn).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("tab", { name: "时间线" }));
    await waitFor(() => {
      expect(screen.queryByTestId("relationship-workspace")).toBeNull();
    });
    expect(mocks.cyDestroy.mock.calls.length).toBeGreaterThan(0);
    expect(mocks.cyRemoveAllListeners.mock.calls.length).toBeGreaterThan(0);
  });

  it("keeps 390px layout without document horizontal overflow outside canvas", async () => {
    const originalInner = window.innerWidth;
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: 390,
    });
    await selectNovelAndOpenRelationships();
    const workspace = await screen.findByTestId("relationship-workspace");
    // simulate narrow container
    Object.defineProperty(workspace, "scrollWidth", {
      configurable: true,
      value: 390,
    });
    Object.defineProperty(workspace, "clientWidth", {
      configurable: true,
      value: 390,
    });
    expect(workspace.scrollWidth).toBeLessThanOrEqual(workspace.clientWidth + 1);
    Object.defineProperty(window, "innerWidth", {
      configurable: true,
      value: originalInner,
    });
  });
});

describe("RelationshipGraph degradation unit", () => {
  afterEach(() => cleanup());

  it("does not create canvas under filters_required", () => {
    render(
      <RelationshipGraph
        nodes={[]}
        edges={[]}
        mode="filters_required"
        selected={null}
        onSelect={() => undefined}
      />
    );
    expect(screen.getByTestId("relationship-filters-required")).toBeInTheDocument();
    expect(screen.queryByTestId("relationship-canvas")).toBeNull();
  });

  it("companion list uses the same node and edge arrays", () => {
    render(
      <RelationshipGraph
        nodes={nodes}
        edges={edges}
        mode="normal"
        selected={null}
        onSelect={() => undefined}
      />
    );
    const list = screen.getByTestId("relationship-companion-list");
    expect(within(list).getAllByRole("button")).toHaveLength(
      nodes.length + edges.length
    );
  });

  it("labels provisional edges as 共现 not fiction types", () => {
    const provisional: RelationshipGraphEdge = {
      observation_id: 9,
      source_character_id: 1,
      target_character_id: 2,
      relation_type: "cooccur",
      transition: "establish",
      confidence: 0.2,
      valid_from_chapter: 1,
      valid_to_chapter: null,
      provenance: "machine",
      evidence_preview: "共现",
      evidence_count: 1,
      edge_kind: "provisional_cooccurrence",
      suggested_type: "romantic",
    };
    render(
      <RelationshipGraph
        nodes={nodes}
        edges={[provisional]}
        mode="normal"
        selected={null}
        onSelect={() => undefined}
      />
    );
    const list = screen.getByTestId("relationship-companion-list");
    expect(within(list).getByText(/临时共现/)).toBeInTheDocument();
    // Must not present suggested romantic as the primary edge type claim.
    expect(within(list).queryByText(/^关系 · 爱慕$/)).toBeNull();
    expect(screen.getByText(/灰色虚线=临时共现/)).toBeInTheDocument();
  });
});
