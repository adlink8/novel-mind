import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  buildChapterFallbackTree,
  buildNmStructureTree,
  pickDefaultTreeNode,
  treeNodeToSelection,
} from "./build-structure-tree";
import { StructureNodePanel } from "./structure-node-panel";
import { StructureTree } from "./structure-tree";
import { StructureWorkspaceShell } from "./structure-workspace-shell";
import {
  clueIntersectsChapterRange,
  countEventsByChapter,
  densifyTimelineForMultiChapter,
  eventInChapterRange,
  isMultiChapterScope,
} from "./structure-types";
import { NM_NODE_BADGE_LABEL } from "@/lib/narrative-memory-api";
import type { NmStructureNode } from "@/lib/narrative-memory-api";

describe("buildChapterFallbackTree", () => {
  it("builds book root plus chapter children", () => {
    const forest = buildChapterFallbackTree(3);
    expect(forest).toHaveLength(1);
    expect(forest[0].id).toBe("book");
    expect(forest[0].chapterStart).toBe(1);
    expect(forest[0].chapterEnd).toBe(3);
    expect(forest[0].children).toHaveLength(3);
    expect(forest[0].children[1].id).toBe("chapter:2");
    expect(forest[0].children[1].chapterStart).toBe(2);
  });

  it("uses titles when provided", () => {
    const forest = buildChapterFallbackTree(1, { titles: { 1: "序章" } });
    expect(forest[0].children[0].label).toContain("序章");
  });

  it("uses the real chapter numbers supplied by the book", () => {
    const forest = buildChapterFallbackTree(2, {
      chapters: [
        { chapter_number: 17, title: "第17章" },
        { chapter_number: 19, title: "第19章" },
      ],
    });

    expect(forest[0].chapterStart).toBe(17);
    expect(forest[0].chapterEnd).toBe(19);
    expect(forest[0].children.map((node) => node.id)).toEqual([
      "chapter:17",
      "chapter:19",
    ]);
  });

  it("strips the leading chapter marker from raw heading titles", () => {
    const forest = buildChapterFallbackTree(2, {
      titles: { 1: "第1章 凯撒", 2: "第三章" },
    });
    expect(forest[0].children[0].label).toBe("第 1 章 · 凯撒");
    // 清理后仍只是章节号 → 视为无名，不重复拼接
    expect(forest[0].children[1].label).toBe("第 2 章");
  });
});

describe("buildNmStructureTree chapter titles", () => {
  const chapterNode = (
    id: number,
    chapter: number,
    displayLabel: string | null
  ): NmStructureNode => ({
    id,
    node_key: `c${id}`,
    node_kind: "chapter_state",
    display_label: displayLabel,
    chapter_start: chapter,
    chapter_end: chapter,
    child_ids: [],
  });

  it("prefers real chapter titles over inconsistent LLM labels", () => {
    const forest = buildNmStructureTree(
      [chapterNode(1, 5, "第5章"), chapterNode(2, 6, null)],
      { chapterTitles: { 5: "第5章 少女与魔王", 6: "第6章 森精灵大作战" } }
    );
    expect(forest[0].label).toBe("第 5 章 · 少女与魔王");
    expect(forest[1].label).toBe("第 6 章 · 森精灵大作战");
  });

  it("treats stage_key labels as missing and falls back to the default label", () => {
    const forest = buildNmStructureTree([chapterNode(1, 7, "chapter_state:7")]);
    expect(forest[0].label).toBe("章状态 · 第 7 章");
  });

  it("keeps model labels when no real title is available", () => {
    const forest = buildNmStructureTree([
      chapterNode(1, 8, "第一卷 第三章 在矮人王国（3/4）"),
    ]);
    expect(forest[0].label).toBe("第一卷 第三章 在矮人王国（3/4）");
  });
});

describe("buildNmStructureTree", () => {
  it("wires child_ids into a global → arc → chapter tree", () => {
    const nodes: NmStructureNode[] = [
      {
        id: 1,
        node_key: "g",
        node_kind: "global_story",
        display_label: "全书",
        chapter_start: 1,
        chapter_end: 10,
        child_ids: [2],
      },
      {
        id: 2,
        node_key: "a1",
        node_kind: "story_arc",
        display_label: "开局弧",
        chapter_start: 1,
        chapter_end: 5,
        child_ids: [3],
      },
      {
        id: 3,
        node_key: "c1",
        node_kind: "chapter_state",
        display_label: null,
        chapter_start: 1,
        chapter_end: 1,
        child_ids: [],
      },
    ];
    const forest = buildNmStructureTree(nodes);
    expect(forest).toHaveLength(1);
    expect(forest[0].kind).toBe("global_story");
    expect(forest[0].children[0].label).toBe("开局弧");
    expect(forest[0].children[0].children[0].chapterEnd).toBe(1);
    expect(pickDefaultTreeNode(forest)?.id).toBe("nm:1");
  });
});

describe("scope helpers", () => {
  it("filters events by chapter range", () => {
    expect(eventInChapterRange(2, 1, 3)).toBe(true);
    expect(eventInChapterRange(5, 1, 3)).toBe(false);
  });

  it("detects clue plant/payoff intersection", () => {
    expect(
      clueIntersectsChapterRange(
        {
          narrative_chapter_number: 2,
          first_cue_chapter: 2,
          payoff_chapter: 8,
        },
        1,
        3
      )
    ).toBe(true);
    expect(
      clueIntersectsChapterRange(
        {
          narrative_chapter_number: 10,
          first_cue_chapter: 10,
          payoff_chapter: 12,
        },
        1,
        3
      )
    ).toBe(false);
  });

  it("flags multi-chapter scope and densifies large multi-chapter sets", () => {
    expect(isMultiChapterScope(1, 1)).toBe(false);
    expect(isMultiChapterScope(1, 4)).toBe(true);

    const events = [
      ...Array.from({ length: 40 }, (_, i) => ({
        id: i + 1,
        narrative_chapter_number: 1,
      })),
      ...Array.from({ length: 30 }, (_, i) => ({
        id: 100 + i,
        narrative_chapter_number: 2,
      })),
      ...Array.from({ length: 20 }, (_, i) => ({
        id: 200 + i,
        narrative_chapter_number: 3,
      })),
    ];
    // Full set in range 1–3 (multi-chapter filter already applied by caller)
    const inRange = events.filter((e) =>
      eventInChapterRange(e.narrative_chapter_number, 1, 3)
    );
    expect(inRange).toHaveLength(90);

    // Single-chapter subset is smaller than multi-chapter full set
    const ch2Only = events.filter((e) =>
      eventInChapterRange(e.narrative_chapter_number, 2, 2)
    );
    expect(ch2Only.length).toBeLessThan(inRange.length);
    expect(ch2Only).toHaveLength(30);

    const densified = densifyTimelineForMultiChapter(inRange, 20);
    expect(densified.total).toBe(90);
    expect(densified.displayEvents.length).toBeLessThanOrEqual(20);
    expect(densified.truncated).toBe(90 - densified.displayEvents.length);
    expect(densified.truncated).toBeGreaterThan(0);
    // All three chapters represented when budget allows
    const chapters = new Set(
      densified.displayEvents.map((e) => e.narrative_chapter_number)
    );
    expect(chapters.has(1)).toBe(true);
    expect(chapters.has(2)).toBe(true);
    expect(chapters.has(3)).toBe(true);
    expect(countEventsByChapter(inRange)).toEqual([
      { chapter: 1, count: 40 },
      { chapter: 2, count: 30 },
      { chapter: 3, count: 20 },
    ]);
  });
});

describe("StructureTree chapters fallback", () => {
  it("renders chapter list and reports selection", () => {
    const forest = buildChapterFallbackTree(3);
    const onSelect = vi.fn();
    render(
      <StructureTree
        forest={forest}
        structureSource="chapters"
        selectedId="book"
        onSelect={onSelect}
      />
    );
    expect(screen.getByText("章节结构")).toBeInTheDocument();
    expect(screen.getByText("全书结构")).toBeInTheDocument();
    // children expanded under book — use data attr (label + range both say 第 N 章)
    fireEvent.click(screen.getByRole("button", { name: /第 2 章/ }));
    expect(onSelect).toHaveBeenCalled();
    const arg = onSelect.mock.calls[0][0];
    expect(arg.id).toBe("chapter:2");
    expect(arg.chapterStart).toBe(2);
    expect(arg.chapterEnd).toBe(2);
  });
});

describe("StructureNodePanel badge", () => {
  it("shows 预览·未发布 when NM node selected", () => {
    render(
      <StructureNodePanel
        structureSource="narrative_memory"
        selected={{
          id: "nm:9",
          kind: "story_arc",
          chapterStart: 1,
          chapterEnd: 4,
          label: "中段弧",
          nmNodeId: 9,
        }}
      />
    );
    expect(screen.getByTestId("nm-node-badge")).toHaveTextContent(
      NM_NODE_BADGE_LABEL
    );
    expect(screen.getByText(/第 1–4 章/)).toBeInTheDocument();
  });

  it("hides NM badge for chapter fallback selection", () => {
    render(
      <StructureNodePanel
        structureSource="chapters"
        selected={{
          id: "chapter:1",
          kind: "chapter",
          chapterStart: 1,
          chapterEnd: 1,
          label: "第 1 章",
        }}
      />
    );
    expect(screen.queryByTestId("nm-node-badge")).not.toBeInTheDocument();
  });

  it("shows honest empty for claims and source-links", () => {
    render(
      <StructureNodePanel
        structureSource="narrative_memory"
        selected={{
          id: "nm:9",
          kind: "story_arc",
          chapterStart: 1,
          chapterEnd: 4,
          label: "中段弧",
          nmNodeId: 9,
        }}
        claims={[]}
        claimsLoading={false}
        selectedClaimId={42}
        sourceLinks={[]}
        sourceLinksLoading={false}
      />
    );
    expect(screen.getByTestId("claims-empty-honesty")).toHaveTextContent(
      "此节点暂无可见声明"
    );
    expect(screen.getByTestId("source-links-empty-honesty")).toHaveTextContent(
      "无叶子证据链接"
    );
  });

  it("lists source-links with chapter and offset when present", () => {
    render(
      <StructureNodePanel
        structureSource="narrative_memory"
        novelId="11"
        selected={{
          id: "nm:9",
          kind: "chapter_state",
          chapterStart: 2,
          chapterEnd: 2,
          label: "章状态",
          nmNodeId: 9,
        }}
        claims={[
          {
            id: 7,
            claim_kind: "event_fact",
            summary: "hero arrives",
            typed_payload: {},
            uncertainty: "likely",
            confidence: 0.9,
            visible_from_chapter: 2,
            node_id: 9,
          },
        ]}
        selectedClaimId={7}
        sourceLinks={[
          {
            id: 100,
            claim_id: 7,
            source_kind: "hierarchy_leaf",
            hierarchy_build_id: "hb1",
            evidence_node_id: "en1",
            chapter_number: 2,
            source_start: 10,
            source_end: 40,
            content_hash: "abcdef0123456789",
          },
        ]}
      />
    );
    expect(screen.getByTestId("source-link-100")).toHaveTextContent("第 2 章");
    expect(screen.getByTestId("source-link-100")).toHaveTextContent(
      "offset 10–40"
    );
    expect(screen.getByTestId("source-link-100")).toHaveTextContent("abcdef01");
    const link = screen.getByTestId("source-link-100").querySelector("a");
    expect(link?.getAttribute("href")).toContain("/novels/11?");
    expect(link?.getAttribute("href")).toContain("chapter=2");
  });
});

describe("StructureWorkspaceShell selection scope", () => {
  it("shows empty-NM banner and updates scope label near facets on select", () => {
    const forest = buildChapterFallbackTree(4);
    const defaultNode = pickDefaultTreeNode(forest)!;
    let selected = treeNodeToSelection(defaultNode);
    const onSelect = vi.fn((node) => {
      selected = treeNodeToSelection(node);
    });

    const { rerender } = render(
      <StructureWorkspaceShell
        structureSource="chapters"
        forest={forest}
        selected={selected}
        onSelect={onSelect}
      >
        <div role="tablist" aria-label="分析切片">
          <button type="button" role="tab">
            时间线
          </button>
        </div>
      </StructureWorkspaceShell>
    );

    expect(screen.getByTestId("nm-empty-banner")).toBeInTheDocument();
    const scope = screen.getByTestId("structure-scope-label");
    expect(scope).toHaveTextContent("视图范围：");
    expect(scope).toHaveTextContent("第 1–4 章");
    // Scope + facet tabs share the same work surface track
    const track = screen.getByTestId("structure-workspace-track");
    expect(track).toContainElement(scope);
    expect(track).toContainElement(
      screen.getByRole("tablist", { name: "分析切片" })
    );

    fireEvent.click(screen.getByRole("button", { name: /第 3 章/ }));
    expect(onSelect).toHaveBeenCalled();
    const next = treeNodeToSelection(onSelect.mock.calls[0][0]);
    expect(next.chapterStart).toBe(3);
    expect(next.chapterEnd).toBe(3);

    rerender(
      <StructureWorkspaceShell
        structureSource="chapters"
        forest={forest}
        selected={next}
        onSelect={onSelect}
      >
        <div role="tablist" aria-label="分析切片">
          <button type="button" role="tab">
            时间线
          </button>
        </div>
      </StructureWorkspaceShell>
    );
    expect(screen.getByTestId("structure-scope-label")).toHaveTextContent(
      "第 3 章"
    );
  });

  it("keeps scope label when structure tree is collapsed", () => {
    const forest = buildChapterFallbackTree(2);
    render(
      <StructureWorkspaceShell
        structureSource="chapters"
        forest={forest}
        selected={treeNodeToSelection(forest[0])}
        onSelect={vi.fn()}
      >
        <div data-testid="facet-body">facets</div>
      </StructureWorkspaceShell>
    );
    fireEvent.click(screen.getByRole("button", { name: "收起结构树" }));
    expect(screen.getByTestId("structure-scope-label")).toHaveTextContent(
      "第 1–2 章"
    );
    expect(screen.getByTestId("facet-body")).toBeInTheDocument();
  });

  it("collapses via horizontal rail slide, not stacking expand control above facets", () => {
    const forest = buildChapterFallbackTree(2);
    render(
      <StructureWorkspaceShell
        structureSource="chapters"
        forest={forest}
        selected={treeNodeToSelection(forest[0])}
        onSelect={vi.fn()}
      >
        <div data-testid="facet-body">facets</div>
      </StructureWorkspaceShell>
    );

    const track = screen.getByTestId("structure-workspace-track");
    const rail = screen.getByTestId("structure-rail");
    expect(rail).toHaveAttribute("data-open", "true");
    expect(track).toContainElement(rail);
    expect(track).toContainElement(screen.getByTestId("facet-body"));

    fireEvent.click(screen.getByRole("button", { name: "收起结构树" }));
    expect(rail).toHaveAttribute("data-open", "false");
    // Expand control is inside the rail (side strip), not a row above the track
    const expand = screen.getByRole("button", { name: "展开结构树" });
    expect(rail).toContainElement(expand);
    expect(track).toContainElement(screen.getByTestId("facet-body"));
    // Facets remain a horizontal sibling of the rail
    expect(track.className).toMatch(/flex/);

    fireEvent.click(expand);
    expect(rail).toHaveAttribute("data-open", "true");
  });

  it("tree branch uses data-open for slide expand state", () => {
    const forest = buildChapterFallbackTree(2);
    render(
      <StructureTree
        forest={forest}
        structureSource="chapters"
        selectedId="book"
        onSelect={vi.fn()}
      />
    );
    const branch = screen.getByTestId("tree-branch-book");
    expect(branch).toHaveAttribute("data-open", "true");
    fireEvent.click(screen.getByRole("button", { name: "折叠" }));
    expect(branch).toHaveAttribute("data-open", "false");
    fireEvent.click(screen.getByRole("button", { name: "展开" }));
    expect(branch).toHaveAttribute("data-open", "true");
  });

  it("long chapter lists scroll inside a fixed-height rail (not page-length tree)", () => {
    const forest = buildChapterFallbackTree(120);
    render(
      <StructureWorkspaceShell
        structureSource="chapters"
        forest={forest}
        selected={treeNodeToSelection(forest[0])}
        onSelect={vi.fn()}
      >
        <div data-testid="facet-body">facets</div>
      </StructureWorkspaceShell>
    );

    const scroll = screen.getByTestId("structure-tree-scroll");
    expect(scroll.className).toMatch(/overflow-y-auto/);
    // Many chapter nodes render, but they live in the scrollport
    expect(
      scroll.querySelectorAll("[data-structure-node-id^='chapter:']").length
    ).toBe(120);

    const track = screen.getByTestId("structure-workspace-track");
    // Fills parent; left/right share height; lists scroll inside
    expect(track.className).toMatch(/flex-1/);
    expect(track.className).toMatch(/min-h-0/);
    expect(screen.getByTestId("structure-rail-panel").className).toMatch(
      /overflow-hidden/
    );
  });

  it("shows candidate preview badge when NM source", () => {
    const forest = buildNmStructureTree([
      {
        id: 1,
        node_key: "g",
        node_kind: "global_story",
        display_label: "全书叙事",
        chapter_start: 1,
        chapter_end: 12,
        child_ids: [],
      },
    ]);
    render(
      <StructureWorkspaceShell
        structureSource="narrative_memory"
        forest={forest}
        selected={treeNodeToSelection(forest[0])}
        onSelect={vi.fn()}
      >
        <span>facets</span>
      </StructureWorkspaceShell>
    );
    expect(screen.getByTestId("nm-preview-badge")).toBeInTheDocument();
  });
});
