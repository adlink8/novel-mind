import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { NarrativeMemoryProgress } from "./narrative-memory-progress";

describe("NarrativeMemoryProgress", () => {
  const dimensions = [
    {
      dimension: "timeline",
      status: "available" as const,
      progress: 1,
    },
    {
      dimension: "relationship",
      status: "partial" as const,
      progress: 0.5,
    },
    {
      dimension: "world",
      status: "blocked" as const,
      progress: 0,
      blocked_reason: "no_candidate_content",
    },
  ];

  it("renders every dimension with its status label and progress", () => {
    render(
      <NarrativeMemoryProgress
        dimensions={dimensions}
        progress={0.6}
        manifestChecksum={"a".repeat(64)}
      />
    );
    expect(screen.getByTestId("nm-progress-panel")).toBeInTheDocument();
    expect(screen.getByTestId("nm-dimension-timeline")).toHaveAttribute(
      "data-status",
      "available"
    );
    expect(screen.getByTestId("nm-dimension-status-timeline")).toHaveTextContent(
      "可用"
    );
    expect(screen.getByTestId("nm-dimension-status-relationship")).toHaveTextContent(
      "部分"
    );
    expect(screen.getByTestId("nm-dimension-status-world")).toHaveTextContent(
      "阻塞"
    );
    // Blocked dimensions expose the stable reason.
    expect(screen.getByTestId("nm-dimension-reason-world")).toHaveTextContent(
      "no_candidate_content"
    );
    // Overall progress bar reflects 60%.
    expect(screen.getByTestId("nm-progress-fill")).toHaveStyle({
      width: "60%",
    });
    expect(screen.getByTestId("nm-progress-value")).toHaveTextContent("60%");
  });

  it("shows candidate-only badge and manifest checksum", () => {
    render(
      <NarrativeMemoryProgress
        dimensions={dimensions}
        manifestChecksum={"b".repeat(64)}
      />
    );
    expect(screen.getByTestId("nm-candidate-badge")).toHaveTextContent(
      "candidate_preview"
    );
    expect(screen.getByTestId("nm-manifest-checksum")).toHaveTextContent(
      "manifest"
    );
    expect(screen.getByTestId("nm-manifest-checksum")).toHaveTextContent(
      "bbbbbbbbbbbb"
    );
  });

  it("renders resumable state from DB checkpoint authority", () => {
    render(
      <NarrativeMemoryProgress
        dimensions={dimensions}
        resumable
        resumeCount={2}
        runStatus="partial"
        cutoff={12}
      />
    );
    expect(screen.getByTestId("nm-resume-state")).toHaveTextContent(
      "可恢复 · DB checkpoint 权威"
    );
    expect(screen.getByTestId("nm-resume-count")).toHaveTextContent("×2");
    expect(screen.getByTestId("nm-run-status")).toHaveTextContent("partial");
    expect(screen.getByTestId("nm-cutoff")).toHaveTextContent("≤ 第 12 章");
  });

  it("shows honest empty when no dimension results", () => {
    render(<NarrativeMemoryProgress dimensions={[]} />);
    expect(screen.getByTestId("nm-dimensions-empty")).toBeInTheDocument();
    expect(screen.getByTestId("nm-resume-state")).toHaveTextContent("不可恢复");
  });
});
