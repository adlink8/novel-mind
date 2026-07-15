import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ClueWorkspace } from "./clue-workspace";
import type {
  ClueDetailPanels,
  ClueEnvelope,
  ClueRun,
  ClueVersionView,
  VisibleClue,
} from "@/lib/clue-api";

const mocks = vi.hoisted(() => ({
  getClues: vi.fn(),
  status: vi.fn(),
  startOrResume: vi.fn(),
  cancel: vi.fn(),
  getDetail: vi.fn(),
  action: vi.fn(),
  onFullBookRequest: vi.fn(),
}));

vi.mock("@/lib/clue-api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/clue-api")>(
    "@/lib/clue-api"
  );
  return {
    ...actual,
    clueApi: {
      getClues: mocks.getClues,
      status: mocks.status,
      startOrResume: mocks.startOrResume,
      cancel: mocks.cancel,
      resume: vi.fn(),
      reanalyze: vi.fn(),
      getVersion: vi.fn(),
      getDetail: mocks.getDetail,
      compare: vi.fn(),
      rollback: vi.fn(),
      action: mocks.action,
    },
  };
});

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) => <a href={href}>{children}</a>,
}));

function makeClue(
  partial: Partial<VisibleClue> & Pick<VisibleClue, "logical_clue_id" | "title">
): VisibleClue {
  return {
    derived_state: "active",
    narrative_chapter_number: 1,
    source_start: 0,
    confidence: 0.8,
    evidence_count: 1,
    link_count: 0,
    provenance: {},
    ...partial,
  };
}

function makeView(
  source: "active" | "running_candidate",
  clues: VisibleClue[],
  versionId = 7
): ClueVersionView {
  const by_state: Partial<Record<string, number>> = {};
  for (const c of clues) {
    by_state[c.derived_state] = (by_state[c.derived_state] ?? 0) + 1;
  }
  return {
    novel_id: 11,
    version_id: versionId,
    source,
    through_chapter: 3,
    full_book: false,
    cutoff_chapter: 3,
    clues,
    counts: { clues: clues.length, by_state, status: "completed" },
    available_states: Object.keys(by_state) as ClueVersionView["available_states"],
    available_character_ids: [10, 11],
  };
}

const activeClues = [
  makeClue({
    logical_clue_id: "c-late",
    title: "晚章铃铛",
    narrative_chapter_number: 3,
    source_start: 5,
    derived_state: "paid_off",
  }),
  makeClue({
    logical_clue_id: "c-early",
    title: "早章雾号",
    narrative_chapter_number: 1,
    source_start: 20,
    derived_state: "active",
  }),
  makeClue({
    logical_clue_id: "c-mid",
    title: "中章回响",
    narrative_chapter_number: 1,
    source_start: 40,
    derived_state: "reinforced",
  }),
];

const completedRun: ClueRun = {
  id: 1,
  novel_id: 11,
  version_id: 7,
  status: "completed",
  status_reason: null,
  progress: {},
  cancel_requested: false,
  updated_at: "2026-07-15T00:00:00Z",
};

const detail: ClueDetailPanels = {
  clue: activeClues[1],
  evidence: [
    {
      evidence_id: "e1",
      role: "cue",
      chapter_id: 101,
      narrative_chapter_number: 1,
      source_start: 20,
      source_end: 40,
      content_hash: "a".repeat(64),
      excerpt: "雾中传来铃铛",
    },
    {
      evidence_id: "e2",
      role: "reinforcement",
      chapter_id: 102,
      narrative_chapter_number: 2,
      source_start: 0,
      source_end: 10,
      content_hash: "b".repeat(64),
      excerpt: "再次听见铃铛",
    },
    {
      evidence_id: "e3",
      role: "payoff",
      chapter_id: 103,
      narrative_chapter_number: 3,
      source_start: 5,
      source_end: 15,
      content_hash: "c".repeat(64),
      excerpt: "铃铛主人现身",
    },
  ],
  links: [
    {
      target_kind: "character",
      character_id: 10,
      timeline_event_id: null,
      relationship_observation_ref: null,
      validation_status: "valid",
    },
  ],
  lifecycle: [
    {
      from_status: "candidate",
      to_status: "active",
      actor_source: "machine",
      reason: "gate",
      event_key: "k1",
    },
    {
      from_status: "active",
      to_status: "reinforced",
      actor_source: "machine",
      reason: "more",
      event_key: "k2",
    },
    {
      from_status: "reinforced",
      to_status: "paid_off",
      actor_source: "machine",
      reason: "payoff",
      event_key: "k3",
    },
  ],
  payoff_chain: [
    { to_status: "active", event_key: "k1" },
    { to_status: "reinforced", event_key: "k2" },
    { to_status: "paid_off", event_key: "k3" },
  ],
};

function envelopeWith(
  active: ClueVersionView | null,
  running: ClueVersionView | null = null
): ClueEnvelope {
  return { active, running_candidate: running };
}

describe("ClueWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.status.mockResolvedValue({ data: completedRun });
    mocks.getClues.mockResolvedValue({
      data: envelopeWith(makeView("active", activeClues)),
    });
    mocks.getDetail.mockResolvedValue({ data: detail });
    mocks.action.mockResolvedValue({
      data: {
        override_id: 1,
        action: "confirm",
        logical_clue_id: "c-early",
        version_id: 7,
        status: "active",
        supersedes_id: null,
      },
    });
    mocks.startOrResume.mockResolvedValue({
      data: { ...completedRun, status: "running" },
    });
  });

  it("renders empty state when no envelope", async () => {
    mocks.status.mockRejectedValue(new Error("404"));
    mocks.getClues.mockResolvedValue({
      data: envelopeWith(null, null),
    });
    render(
      <ClueWorkspace
        novelId="11"
        fullBook={false}
        onFullBookRequest={mocks.onFullBookRequest}
      />
    );
    expect(await screen.findByText("暂无线索结果。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始线索分析" })).toBeInTheDocument();
  });

  it("shows loading then completed band/list with identical ordered IDs", async () => {
    render(
      <ClueWorkspace
        novelId="11"
        fullBook={false}
        onFullBookRequest={mocks.onFullBookRequest}
      />
    );
    await screen.findByTestId("clue-band");

    const bandButtons = within(screen.getByLabelText("线索时间带")).getAllByRole(
      "button"
    );
    const listOptions = within(screen.getByTestId("clue-keyboard-list")).getAllByRole(
      "option"
    );
    // Stable order: ch1@20, ch1@40, ch3@5
    const bandTitles = bandButtons.map((b) => b.textContent);
    expect(bandTitles[0]).toContain("早章雾号");
    expect(bandTitles[1]).toContain("中章回响");
    expect(bandTitles[2]).toContain("晚章铃铛");

    const listTitles = listOptions.map((o) => o.textContent);
    expect(listTitles[0]).toContain("早章雾号");
    expect(listTitles[1]).toContain("中章回响");
    expect(listTitles[2]).toContain("晚章铃铛");
    expect(listOptions).toHaveLength(bandButtons.length);
  });

  it("filters use only server-visible states and character ids", async () => {
    render(
      <ClueWorkspace
        novelId="11"
        fullBook={false}
        onFullBookRequest={mocks.onFullBookRequest}
      />
    );
    await screen.findByTestId("clue-band");

    const statusSelect = screen.getByLabelText("筛选线索状态");
    expect(within(statusSelect).getByRole("option", { name: /活跃/ })).toBeInTheDocument();
    expect(within(statusSelect).getByRole("option", { name: /强化/ })).toBeInTheDocument();
    expect(within(statusSelect).getByRole("option", { name: /已回收/ })).toBeInTheDocument();
    // dismissed not in available_states
    expect(
      within(statusSelect).queryByRole("option", { name: /已驳回/ })
    ).not.toBeInTheDocument();

    fireEvent.change(statusSelect, { target: { value: "active" } });
    await waitFor(() =>
      expect(mocks.getClues).toHaveBeenLastCalledWith(
        "11",
        expect.objectContaining({ status: "active", full_book: false })
      )
    );

    fireEvent.change(screen.getByLabelText("筛选关联人物"), {
      target: { value: "10" },
    });
    await waitFor(() =>
      expect(mocks.getClues).toHaveBeenLastCalledWith(
        "11",
        expect.objectContaining({ character_id: 10 })
      )
    );
  });

  it("opens evidence panel with server payoff chain and all four actions", async () => {
    render(
      <ClueWorkspace
        novelId="11"
        fullBook={false}
        onFullBookRequest={mocks.onFullBookRequest}
      />
    );
    await screen.findByTestId("clue-band");

    fireEvent.click(screen.getByRole("option", { name: /早章雾号/ }));
    const panel = await screen.findByTestId("clue-evidence-panel");
    expect(mocks.getDetail).toHaveBeenCalledWith("11", 7, "c-early", {
      full_book: false,
    });

    expect(within(panel).getByTestId("panel-payoff-chain")).toHaveTextContent(
      "活跃"
    );
    expect(within(panel).getByTestId("panel-payoff-chain")).toHaveTextContent(
      "强化"
    );
    expect(within(panel).getByTestId("panel-payoff-chain")).toHaveTextContent(
      "已回收"
    );
    expect(within(panel).getByText("雾中传来铃铛")).toBeInTheDocument();
    expect(within(panel).getAllByText("跳转原文章节").length).toBeGreaterThan(0);

    fireEvent.change(within(panel).getByLabelText("动作原因"), {
      target: { value: "证据充分" },
    });

    // confirm
    fireEvent.click(within(panel).getByRole("button", { name: "确认" }));
    await waitFor(() =>
      expect(mocks.action).toHaveBeenCalledWith("11", "c-early", {
        action: "confirm",
        reason: "证据充分",
      })
    );

    // annotate
    fireEvent.change(within(panel).getByLabelText("注释内容"), {
      target: { value: "跨章呼应" },
    });
    fireEvent.click(within(panel).getByRole("button", { name: "保存注释" }));
    await waitFor(() =>
      expect(mocks.action).toHaveBeenCalledWith("11", "c-early", {
        action: "annotate",
        reason: "证据充分",
        note: "跨章呼应",
      })
    );

    // reject requires confirmation
    fireEvent.click(within(panel).getByRole("button", { name: "驳回" }));
    expect(screen.getByRole("alertdialog", { name: "确认驳回" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认驳回" }));
    await waitFor(() =>
      expect(mocks.action).toHaveBeenCalledWith("11", "c-early", {
        action: "reject",
        reason: "证据充分",
      })
    );

    // adjust link requires confirmation
    fireEvent.change(within(panel).getByLabelText("关联目标值"), {
      target: { value: "42" },
    });
    fireEvent.click(within(panel).getByRole("button", { name: "提交关联调整" }));
    expect(
      screen.getByRole("alertdialog", { name: "确认关联调整" })
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认提交" }));
    await waitFor(() =>
      expect(mocks.action).toHaveBeenCalledWith("11", "c-early", {
        action: "adjust_link",
        reason: "证据充分",
        link: { target_kind: "character", character_id: 42 },
      })
    );

    // refresh after action (no optimistic fabrication)
    expect(mocks.getClues.mock.calls.length).toBeGreaterThan(1);
  });

  it("surfaces running candidate and partial progress", async () => {
    const running: ClueRun = {
      ...completedRun,
      status: "running",
      progress: { completed_chapters: 1, total_chapters: 5 },
    };
    mocks.status.mockResolvedValue({ data: running });
    mocks.getClues.mockResolvedValue({
      data: envelopeWith(
        makeView("active", [activeClues[1]], 7),
        makeView(
          "running_candidate",
          [
            makeClue({
              logical_clue_id: "c-new",
              title: "候选新线索",
              derived_state: "candidate",
            }),
          ],
          8
        )
      ),
    });

    render(
      <ClueWorkspace
        novelId="11"
        fullBook={false}
        onFullBookRequest={mocks.onFullBookRequest}
      />
    );

    expect(await screen.findByText("正在分析线索")).toBeInTheDocument();
    const candTab = await screen.findByRole("tab", { name: /正在生成/ });
    expect(candTab).toHaveAttribute("aria-selected", "true");
    expect(
      (await screen.findAllByText("候选新线索")).length
    ).toBeGreaterThan(0);
  });

  it("forwards full-book toggle to parent without a clue preference call", async () => {
    render(
      <ClueWorkspace
        novelId="11"
        fullBook={false}
        onFullBookRequest={mocks.onFullBookRequest}
      />
    );
    await screen.findByTestId("clue-band");
    fireEvent.click(screen.getByLabelText("显示全书（可能剧透）"));
    expect(mocks.onFullBookRequest).toHaveBeenCalledWith(true);
  });

  it("handles error state", async () => {
    mocks.getClues.mockRejectedValue(new Error("network"));
    render(
      <ClueWorkspace
        novelId="11"
        fullBook={false}
        onFullBookRequest={mocks.onFullBookRequest}
      />
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("加载线索失败");
  });

  it("starts analysis from empty and does not invent status", async () => {
    mocks.status.mockRejectedValue(new Error("404"));
    mocks.getClues.mockResolvedValue({ data: envelopeWith(null) });
    mocks.startOrResume.mockResolvedValue({
      data: { ...completedRun, status: "running" },
    });
    mocks.status
      .mockRejectedValueOnce(new Error("404"))
      .mockResolvedValue({ data: { ...completedRun, status: "running" } });

    render(
      <ClueWorkspace
        novelId="11"
        fullBook={false}
        onFullBookRequest={mocks.onFullBookRequest}
      />
    );
    await screen.findByText("暂无线索结果。");
    fireEvent.click(screen.getByRole("button", { name: "开始线索分析" }));
    await waitFor(() => expect(mocks.startOrResume).toHaveBeenCalledWith("11"));
  });
});
