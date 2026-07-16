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
  eventInChapterRange,
  formatChapterRange,
} from "./structure-types";
import {
  NM_EMPTY_BANNER,
  NM_NODE_BADGE_LABEL,
  NM_PREVIEW_BADGE_LABEL,
} from "@/lib/narrative-memory-api";
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
});

describe("StructureWorkspaceShell selection scope", () => {
  it("shows empty-NM banner and updates scope label on select", () => {
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
        <div data-testid="facet-scope">
          {formatChapterRange(selected.chapterStart, selected.chapterEnd)}
        </div>
      </StructureWorkspaceShell>
    );

    expect(screen.getByTestId("nm-empty-banner")).toHaveTextContent(
      NM_EMPTY_BANNER
    );
    expect(screen.getByTestId("structure-scope-label")).toHaveTextContent(
      "第 1–4 章"
    );
    expect(screen.getByTestId("facet-scope")).toHaveTextContent("第 1–4 章");

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
        <div data-testid="facet-scope">
          {formatChapterRange(next.chapterStart, next.chapterEnd)}
        </div>
      </StructureWorkspaceShell>
    );
    expect(screen.getByTestId("structure-scope-label")).toHaveTextContent(
      "第 3 章"
    );
    expect(screen.getByTestId("facet-scope")).toHaveTextContent("第 3 章");
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
    expect(screen.getByTestId("nm-preview-badge")).toHaveTextContent(
      NM_PREVIEW_BADGE_LABEL
    );
    expect(screen.queryByTestId("nm-empty-banner")).not.toBeInTheDocument();
  });
});
