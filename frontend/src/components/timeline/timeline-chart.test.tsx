import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("echarts-for-react/lib/core", () => ({
  default: () => <div data-testid="mock-echarts" />,
}));

import {
  buildChapterXPositions,
  buildEventWindows,
  buildSwimlanePoints,
  eventTypeLaneY,
  normalizeEventType,
  TimelineChart,
} from "./timeline-chart";
import type { TimelineEvent } from "@/lib/api";

function event(
  index: number,
  chapter?: number,
  overrides?: Partial<TimelineEvent>
): TimelineEvent {
  return {
    id: index + 1,
    logical_event_id: `event-${index + 1}`,
    title: `事件 ${index + 1}`,
    description: "事件摘要",
    event_type: "plot",
    narrative_chapter_number: chapter ?? Math.floor(index / 3) + 1,
    source_start: index * 100,
    narrative_index: index,
    story_rank: index + 1,
    time_precision: "unknown",
    time_expression: null,
    confidence: 0.9,
    participants: [],
    provenance: {},
    ...overrides,
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

describe("swimlane layout (chapter X + event_type Y)", () => {
  it("maps event types onto four distinct Y lanes", () => {
    expect(eventTypeLaneY("plot")).not.toBe(eventTypeLaneY("conflict"));
    expect(eventTypeLaneY("conflict")).not.toBe(eventTypeLaneY("character"));
    expect(eventTypeLaneY("character")).not.toBe(eventTypeLaneY("world"));
    const ys = ["plot", "conflict", "character", "world"].map(eventTypeLaneY);
    expect(new Set(ys).size).toBe(4);
    // plot is topmost
    expect(eventTypeLaneY("plot")).toBeGreaterThan(eventTypeLaneY("world"));
  });

  it("normalizes unknown types into the plot lane", () => {
    expect(normalizeEventType("mystery")).toBe("plot");
    expect(normalizeEventType("冲突高潮")).toBe("conflict");
  });

  it("places X by narrative chapter with micro-offset, not index%4 jitter", () => {
    const events = [
      event(0, 1, { source_start: 10 }),
      event(1, 1, { source_start: 50 }),
      event(2, 2, { source_start: 0 }),
      event(3, 2, { source_start: 80 }),
    ];
    const xs = buildChapterXPositions(events);
    const x0 = xs.get(1)!;
    const x1 = xs.get(2)!;
    const x2 = xs.get(3)!;
    const x3 = xs.get(4)!;

    // Same chapter → same integer floor; later source_start → larger x
    expect(Math.floor(x0)).toBe(1);
    expect(Math.floor(x1)).toBe(1);
    expect(x1).toBeGreaterThan(x0);
    expect(Math.floor(x2)).toBe(2);
    expect(Math.floor(x3)).toBe(2);
    expect(x3).toBeGreaterThan(x2);

    // Must not be the legacy index-based primary layout: [0,1,2,3]
    expect(x0).not.toBe(0);
    expect(x1).not.toBe(1);
    expect(x2).not.toBe(2);

    // Within-chapter micro-offset stays in (chapter, chapter+1)
    expect(x0).toBeGreaterThan(1);
    expect(x0).toBeLessThan(2);
    expect(x1).toBeLessThan(2);
  });

  it("assigns ≥2 distinct Y lanes when multiple event types are present", () => {
    const events = [
      event(0, 1, { event_type: "plot" }),
      event(1, 1, { event_type: "conflict" }),
      event(2, 2, { event_type: "character" }),
      event(3, 3, { event_type: "world" }),
    ];
    const points = buildSwimlanePoints(events);
    const ys = [...points.values()].map((p) => p[1]);
    expect(new Set(ys).size).toBeGreaterThanOrEqual(2);
    expect(new Set(ys).size).toBe(4);

    // Not the legacy ((index % 4) - 1.5) * 0.16 primary layout
    for (const [id, [, y]] of points) {
      const index = id - 1;
      const legacyY = ((index % 4) - 1.5) * 0.16;
      expect(y).not.toBeCloseTo(legacyY, 5);
    }
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
    expect(screen.getByTestId("timeline-canvas")).toHaveAttribute(
      "data-layout",
      "chapter-swimlane"
    );
    expect(screen.getByRole("button", { name: "返回全书概览" })).toBeInTheDocument();
    expect(screen.getByText(/横轴章节/)).toBeInTheDocument();
  });

  it("keeps overview stages for dense books before swimlane drill-in", () => {
    const events = Array.from({ length: 120 }, (_, i) => event(i));
    render(
      <TimelineChart
        events={events}
        causalEdges={[]}
        ordering="narrative"
        novelId="1"
      />
    );
    expect(screen.getByRole("heading", { name: /全书概览/ })).toBeInTheDocument();
    expect(screen.getByText(/共\s*\d+\s*个剧情阶段/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /展开首阶段/ }));
    expect(screen.getByTestId("timeline-canvas")).toHaveAttribute(
      "data-layout",
      "chapter-swimlane"
    );
  });
});
