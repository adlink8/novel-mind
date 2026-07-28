import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProgressBar } from "./progress-bar";

describe("ProgressBar", () => {
  it("occupies layout space instead of covering the chat input", () => {
    const { container } = render(
      <ProgressBar
        chapterPercent={17}
        chapterTitle="第十三章"
        chapterIndex={14}
        chapterTotal={420}
      />
    );

    expect(screen.getByText("本章 17% · 第 14/420 章")).toBeInTheDocument();
    expect(container.firstElementChild).toHaveClass("shrink-0");
    expect(container.firstElementChild).not.toHaveClass("absolute");
  });
});
