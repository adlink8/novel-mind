import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

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

  it("saves a selected paragraph through the bookmark callback", async () => {
    const onBookmarkSelection = vi.fn().mockResolvedValue(undefined);
    render(
      <ReaderContent
        chapter={makeChapter("这是需要保存的选中段落。")}
        onBookmarkSelection={onBookmarkSelection}
      />
    );

    const textNode = screen.getByTestId("reader-page-text").firstChild;
    expect(textNode).not.toBeNull();
    const range = document.createRange();
    range.setStart(textNode!, 0);
    range.setEnd(textNode!, 7);
    const selection = window.getSelection()!;
    selection.removeAllRanges();
    selection.addRange(range);
    fireEvent(document, new Event("selectionchange"));

    const saveButton = await screen.findByRole("button", { name: "保存书签" });
    expect(screen.queryByText("书签")).not.toBeInTheDocument();
    fireEvent.click(saveButton);
    await waitFor(() => expect(onBookmarkSelection).toHaveBeenCalledTimes(1));
    expect(onBookmarkSelection.mock.calls[0][0].selection_text).toBe(
      "这是需要保存的"
    );
  });
});
