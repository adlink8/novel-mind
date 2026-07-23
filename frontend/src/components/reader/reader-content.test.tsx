import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { Chapter } from "@/lib/api";
import { ReaderContent } from "./reader-content";

function makeChapter(content: string): Chapter {
  return {
    id: 1,
    novel_id: 11,
    chapter_number: 1,
    title: "第一章 测试",
    content,
    word_count: content.length,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

afterEach(cleanup);

describe("ReaderContent keyboard paging", () => {
  it("flips pages with ArrowRight/ArrowLeft in paged mode", () => {
    // 3500 字一页 → 4000 字分两页
    render(
      <ReaderContent chapter={makeChapter("字".repeat(4000))} readingMode="paged" />
    );

    expect(screen.getByText(/第 1\/2 页/)).toBeInTheDocument();
    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    expect(screen.getByText(/第 2\/2 页/)).toBeInTheDocument();
    fireEvent.keyDown(document.body, { key: "ArrowLeft" });
    expect(screen.getByText(/第 1\/2 页/)).toBeInTheDocument();
  });

  it("ignores arrows when the event comes from form controls", () => {
    render(
      <div>
        <input data-testid="external-input" />
        <ReaderContent
          chapter={makeChapter("字".repeat(4000))}
          readingMode="paged"
        />
      </div>
    );

    fireEvent.keyDown(screen.getByTestId("external-input"), {
      key: "ArrowRight",
    });
    expect(screen.getByText(/第 1\/2 页/)).toBeInTheDocument();
  });

  it("ignores arrows in scroll mode", () => {
    render(
      <ReaderContent chapter={makeChapter("字".repeat(4000))} readingMode="scroll" />
    );

    fireEvent.keyDown(document.body, { key: "ArrowRight" });
    // 长页模式没有页码指示，只验证不渲染翻页控件
    expect(screen.getByText(/长页模式/)).toBeInTheDocument();
    expect(screen.queryByText(/第 \d+\/\d+ 页/)).not.toBeInTheDocument();
  });
});
