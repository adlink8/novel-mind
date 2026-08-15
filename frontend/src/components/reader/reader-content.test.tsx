import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
});

describe("ReaderContent additional branches", () => {
  it("无章节时展示选择提示", () => {
    render(<ReaderContent chapter={null} />);
    expect(screen.getByText("选择章节开始阅读")).toBeInTheDocument();
  });

  it("单页短章展示字数但不显示页码", () => {
    render(<ReaderContent chapter={makeChapter("短文")} />);
    expect(screen.getByText("2 字")).toBeInTheDocument();
    expect(screen.queryByText(/第 \d+\/\d+ 页/)).not.toBeInTheDocument();
  });

  it("分页模式第一页上一页禁用、末页下一页禁用", () => {
    render(
      <ReaderContent
        chapter={makeChapter("字".repeat(4000))}
        readingMode="paged"
      />
    );
    const prevBtn = screen.getByRole("button", { name: "上一页" });
    expect(prevBtn).toBeDisabled();
    expect(screen.getByRole("button", { name: "下一页" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    // 第二页 → 上一页可用；末页无下一章 → 下一页禁用
    expect(screen.getByRole("button", { name: "上一页" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
  });

  it("翻页按钮触发 onNextChapter/onPrevChapter", () => {
    const onNextChapter = vi.fn();
    const onPrevChapter = vi.fn();
    render(
      <ReaderContent
        chapter={makeChapter("字".repeat(4000))}
        readingMode="paged"
        hasNextChapter
        hasPrevChapter
        onNextChapter={onNextChapter}
        onPrevChapter={onPrevChapter}
      />
    );
    // 首页上一章按钮显示「上一章」
    expect(screen.getByRole("button", { name: "上一章" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "上一章" }));
    expect(onPrevChapter).toHaveBeenCalled();
    // 翻到末页后「下一章」
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    const nextChapterBtn = screen.getByRole("button", { name: "下一章" });
    fireEvent.click(nextChapterBtn);
    expect(onNextChapter).toHaveBeenCalled();
  });

  it("超大章节展示分页警告", () => {
    render(
      <ReaderContent chapter={makeChapter("字".repeat(21000))} readingMode="paged" />
    );
    expect(screen.getByText("本章体量较大，已自动分页显示")).toBeInTheDocument();
  });

  it("长页模式不渲染翻页按钮", () => {
    render(
      <ReaderContent chapter={makeChapter("字".repeat(4000))} readingMode="scroll" />
    );
    expect(screen.queryByRole("button", { name: /上一页|下一页/ })).not.toBeInTheDocument();
  });

  it("onChapterProgress 在换章时被调用", () => {
    const onChapterProgress = vi.fn();
    render(
      <ReaderContent
        chapter={makeChapter("字".repeat(1000))}
        readingMode="paged"
        onChapterProgress={onChapterProgress}
      />
    );
    expect(onChapterProgress).toHaveBeenCalled();
  });

  it("citation highlight 渲染 <mark> 高亮", () => {
    render(
      <ReaderContent
        chapter={makeChapter("雾城第一章内容".repeat(1000))}
        readingMode="paged"
        highlightRange={{ sourceStart: 3, sourceEnd: 5 }}
      />
    );
    expect(screen.getAllByTestId("reader-citation-highlight").length).toBeGreaterThan(0);
  });
});

