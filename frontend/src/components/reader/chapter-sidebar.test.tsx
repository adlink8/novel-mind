import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChapterSidebar } from "./chapter-sidebar";

const chapters = [
  {
    id: 1,
    novel_id: 7,
    chapter_number: 1,
    title: "第一章",
    content: "正文",
    word_count: 2,
    created_at: "",
    updated_at: "",
  },
  {
    id: 2,
    novel_id: 7,
    chapter_number: 2,
    title: "第二章",
    content: "正文",
    word_count: 2,
    created_at: "",
    updated_at: "",
  },
];

describe("ChapterSidebar", () => {
  it("selects a chapter and closes the drawer on mobile-style navigation", () => {
    HTMLElement.prototype.scrollIntoView = vi.fn();
    const onSelectChapter = vi.fn();
    const onToggle = vi.fn();

    render(
      <ChapterSidebar
        chapters={chapters}
        currentChapterId={1}
        onSelectChapter={onSelectChapter}
        isOpen
        onToggle={onToggle}
        forceDrawer
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /第二章/ }));

    expect(onSelectChapter).toHaveBeenCalledWith(2);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});
