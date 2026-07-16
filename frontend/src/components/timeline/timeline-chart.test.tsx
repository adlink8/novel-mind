import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("echarts-for-react/lib/core", () => ({
  default: () => <div data-testid="mock-echarts" />,
}));

import { buildEventWindows, TimelineChart } from "./timeline-chart";
import type { TimelineEvent } from "@/lib/api";

function event(index: number, chapter?: number): TimelineEvent {
  return {
    id: index + 1,
    logical_event_id: `event-${index + 1}`,
    title: `事件 ${index + 1}`,
    description: "事件摘要",
    event_type: "plot",
    narrative_chapter_number: chapter ?? Math.floor(index / 3) + 1,
    narrative_index: index,
    story_rank: index + 1,
    time_precision: "unknown",
    time_expression: null,
    confidence: 0.9,
    participants: [],
    provenance: {},
  };
}

describe("buildEventWindows plot segmentation", () => {
  it("does not hard-cap overview stages at 7", () => {
    // ~400 events → preferred stages ≈ ceil(400/80)=5, not forced to 7 equal slices
    const events = Array.from({ length: 400 }, (_, i) => event(i));
    const windows = buildEventWindows(events);
    expect(windows.length).toBeGreaterThanOrEqual(3);
    expect(windows.length).toBeLessThanOrEqual(14);
    const covered = windows.reduce((n, w) => n + w.eventCount, 0);
    expect(covered).toBe(400);
  });

  it("snaps cuts toward quieter chapter valleys when density varies", () => {
    // Dense early chapters, sparse middle (valley), dense late — cuts should prefer sparse zone.
    const events: TimelineEvent[] = [];
    let id = 0;
    for (let ch = 1; ch <= 10; ch++) {
      const count = ch >= 5 && ch <= 6 ? 2 : 12; // valley at 5–6
      for (let k = 0; k < count; k++) {
        events.push(event(id++, ch));
      }
    }
    const windows = buildEventWindows(events);
    expect(windows.length).toBeGreaterThanOrEqual(2);
    // At least one boundary chapter should land near the valley region.
    const boundaryChapters = windows.flatMap((w) => [
      w.firstChapter,
      w.lastChapter,
    ]);
    expect(boundaryChapters.some((c) => c >= 4 && c <= 7)).toBe(true);
  });

  it("keeps small timelines as a single stage", () => {
    const events = Array.from({ length: 20 }, (_, i) => event(i));
    const windows = buildEventWindows(events);
    expect(windows).toHaveLength(1);
    expect(windows[0].eventCount).toBe(20);
  });
});

describe("TimelineChart progressive disclosure", () => {
  it("aggregates a large timeline and opens a bounded event window", () => {
    const events = Array.from({ length: 49 }, (_, index) => event(index));
    render(
      <TimelineChart
        events={events}
        causalEdges={[]}
        ordering="narrative"
        novelId="1"
      />
    );

    expect(screen.getByRole("heading", { name: /全书概览/ })).toBeInTheDocument();
    expect(screen.getByText(/按剧情节奏分段/)).toBeInTheDocument();
    expect(screen.queryByTestId("timeline-canvas")).not.toBeInTheDocument();

    // First stage button — label is plot-based, not fixed "区间 1 · 25"
    fireEvent.click(screen.getByRole("button", { name: /阶段 1/ }));

    expect(screen.getByTestId("timeline-canvas")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "返回全书概览" })).toBeInTheDocument();
  });
});
