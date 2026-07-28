import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  ClueCard,
  resolvePayoffChapter,
  resolvePlantChapter,
  spanPositions,
} from "./clue-card";
import type { VisibleClue } from "@/lib/clue-api";

function makeClue(partial: Partial<VisibleClue> & Pick<VisibleClue, "logical_clue_id" | "title">): VisibleClue {
  return {
    derived_state: "active",
    narrative_chapter_number: 2,
    source_start: 0,
    confidence: 0.8,
    evidence_count: 2,
    link_count: 0,
    provenance: {},
    ...partial,
  };
}

describe("resolvePlantChapter / resolvePayoffChapter", () => {
  it("prefers first_cue_chapter for plant", () => {
    const clue = makeClue({
      logical_clue_id: "a",
      title: "t",
      first_cue_chapter: 1,
      narrative_chapter_number: 5,
    });
    expect(resolvePlantChapter(clue)).toBe(1);
  });

  it("falls back to narrative_chapter_number when first_cue missing", () => {
    const clue = makeClue({
      logical_clue_id: "a",
      title: "t",
      narrative_chapter_number: 4,
    });
    expect(resolvePlantChapter(clue)).toBe(4);
  });

  it("does not invent payoff when null or missing", () => {
    expect(
      resolvePayoffChapter(
        makeClue({ logical_clue_id: "a", title: "t", payoff_chapter: null })
      )
    ).toBeNull();
    expect(
      resolvePayoffChapter(makeClue({ logical_clue_id: "a", title: "t" }))
    ).toBeNull();
    expect(
      resolvePayoffChapter(
        makeClue({ logical_clue_id: "a", title: "t", payoff_chapter: 0 })
      )
    ).toBeNull();
  });

  it("returns payoff_chapter when positive", () => {
    expect(
      resolvePayoffChapter(
        makeClue({ logical_clue_id: "a", title: "t", payoff_chapter: 9 })
      )
    ).toBe(9);
  });
});

describe("spanPositions", () => {
  it("places plant only when payoff unknown", () => {
    const p = spanPositions(3, null);
    expect(p.plantPct).toBeGreaterThan(0);
    expect(p.payoffPct).toBeNull();
  });

  it("places plant left of payoff when span known", () => {
    const p = spanPositions(2, 10);
    expect(p.payoffPct).not.toBeNull();
    expect(p.plantPct).toBeLessThan(p.payoffPct!);
  });
});

describe("ClueCard", () => {
  it("renders title, summary, state chip, plant/payoff span", () => {
    const onSelect = vi.fn();
    render(
      <ClueCard
        selected={false}
        onSelect={onSelect}
        clue={makeClue({
          logical_clue_id: "c1",
          title: "雾中铃铛",
          summary: "第一章埋下铃声",
          first_cue_chapter: 1,
          payoff_chapter: 8,
          derived_state: "reinforced",
          evidence_count: 3,
        })}
      />
    );

    expect(screen.getByTestId("clue-card")).toHaveTextContent("雾中铃铛");
    expect(screen.getByTestId("clue-summary")).toHaveTextContent("第一章埋下铃声");
    expect(screen.getByTestId("clue-state-chip")).toHaveTextContent("强化");
    expect(screen.getByTestId("clue-plant-chapter")).toHaveTextContent("埋设 第1章");
    expect(screen.getByTestId("clue-payoff-chapter")).toHaveTextContent("兑现 第8章");
    expect(screen.getByTestId("clue-span-bar")).toBeInTheDocument();
    expect(screen.getByText("证据 3")).toBeInTheDocument();
  });

  it("shows 兑现未公开 when payoff_chapter is null (spoiler-safe)", () => {
    render(
      <ClueCard
        selected={false}
        onSelect={vi.fn()}
        clue={makeClue({
          logical_clue_id: "c2",
          title: "未回收线索",
          first_cue_chapter: 2,
          payoff_chapter: null,
          derived_state: "active",
        })}
      />
    );
    expect(screen.getByTestId("clue-payoff-unknown")).toHaveTextContent(
      "兑现未公开"
    );
    expect(screen.queryByTestId("clue-payoff-chapter")).not.toBeInTheDocument();
  });

  it("is not a horizontal multi-clue event strip", () => {
    const { container } = render(
      <ClueCard
        selected={false}
        onSelect={vi.fn()}
        clue={makeClue({ logical_clue_id: "c3", title: "单卡" })}
      />
    );
    // Single card root is a button option — not an ol flex strip of events
    expect(container.querySelector("ol")).toBeNull();
    expect(screen.getByRole("option")).toHaveAttribute("data-testid", "clue-card");
  });
});
