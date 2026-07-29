import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Bookmark, Chapter } from "@/lib/api";
import { novelsApi } from "@/lib/api";
import { ReaderBookmarks } from "./reader-bookmarks";

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    novelsApi: {
      ...actual.novelsApi,
      listBookmarks: vi.fn(),
      deleteBookmark: vi.fn(),
    },
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const chapter: Chapter = {
  id: 7,
  novel_id: 1,
  chapter_number: 3,
  title: "第三章 旧书店",
  content: "阿宁走进旧书店，翻开了泛黄的书页。",
  word_count: 20,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const bookmark: Bookmark = {
  id: 11,
  owner_id: 2,
  novel_id: 1,
  chapter_id: chapter.id,
  source_start: 0,
  source_end: 7,
  selected_text: "阿宁走进旧书店",
  selection_text_hash: "a".repeat(64),
  chapter_content_hash: "b".repeat(64),
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

describe("ReaderBookmarks", () => {
  it("loads saved bookmarks and navigates from the icon-only trigger", async () => {
    vi.mocked(novelsApi.listBookmarks).mockResolvedValue({
      data: [bookmark],
    } as never);
    const onNavigate = vi.fn();

    render(
      <ReaderBookmarks
        novelId="1"
        chapters={[chapter]}
        open
        onOpenChange={vi.fn()}
        onNavigate={onNavigate}
      />
    );

    expect(screen.getByRole("button", { name: "打开书签" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(bookmark.selected_text)).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: /跳转到第三章/ }));

    expect(onNavigate).toHaveBeenCalledWith(bookmark);
  });
});
