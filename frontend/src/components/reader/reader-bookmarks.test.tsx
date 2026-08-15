import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { listBookmarks, createBookmark, deleteBookmark } = vi.hoisted(() => ({
  listBookmarks: vi.fn(),
  createBookmark: vi.fn(),
  deleteBookmark: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  novelsApi: { listBookmarks, createBookmark, deleteBookmark },
}));

vi.mock("@/lib/use-dismissable-layer", () => ({
  useDismissableLayer: () => ({
    present: true,
    closing: false,
  }),
}));

import { ReaderBookmarks } from "./reader-bookmarks";
import type { ReaderBookmark } from "@/lib/api";

const chapters = [
  { id: 1, title: "第一章" },
  { id: 2, title: "第二章" },
];

function bookmark(overrides: Partial<ReaderBookmark> = {}): ReaderBookmark {
  return {
    id: 1,
    owner_id: 1,
    novel_id: 10,
    chapter_id: 1,
    position_percent: 42,
    label: null,
    note: null,
    created_at: "2026-08-06T00:00:00Z",
    updated_at: "2026-08-06T00:00:00Z",
    ...overrides,
  };
}

function renderPanel(props: Partial<Parameters<typeof ReaderBookmarks>[0]> = {}) {
  return render(
    <ReaderBookmarks
      novelId="10"
      chapters={chapters as any}
      open
      onOpenChange={vi.fn()}
      onNavigate={vi.fn()}
      currentChapterId={1}
      currentPercent={30}
      {...props}
    />
  );
}

describe("ReaderBookmarks", () => {
  beforeEach(() => {
    listBookmarks.mockReset();
    createBookmark.mockReset();
    deleteBookmark.mockReset();
  });

  it("loads and renders bookmark list with chapter title + percent", async () => {
    listBookmarks.mockResolvedValue({
      data: [
        bookmark({ id: 1, chapter_id: 2, position_percent: 50, label: "伏笔" }),
      ],
    });
    renderPanel();

    expect(await screen.findByText("伏笔")).toBeInTheDocument();
    expect(screen.getByText("第二章")).toBeInTheDocument();
    expect(screen.getByText("50%")).toBeInTheDocument();
  });

  it("renders empty state and add button when no bookmarks", async () => {
    listBookmarks.mockResolvedValue({ data: [] });
    renderPanel();

    expect(
      await screen.findByText("点击「在当前位置添加书签」保存阅读位置。")
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("reader-bookmarks-add-open")
    ).toBeInTheDocument();
  });

  it("creates a bookmark at current position with label/note", async () => {
    listBookmarks.mockResolvedValue({ data: [] });
    createBookmark.mockResolvedValue({
      data: bookmark({ id: 5, position_percent: 30, label: "悬念", note: "留意" }),
    });
    renderPanel({ currentPercent: 30 });

    fireEvent.click(await screen.findByTestId("reader-bookmarks-add-open"));
    fireEvent.change(screen.getByLabelText("书签标签"), {
      target: { value: "悬念" },
    });
    fireEvent.change(screen.getByLabelText("书签备注"), {
      target: { value: "留意" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(createBookmark).toHaveBeenCalledWith("10", {
        chapter_id: 1,
        position_percent: 30,
        label: "悬念",
        note: "留意",
      })
    );
    expect(await screen.findByText("悬念")).toBeInTheDocument();
  });

  it("deletes a bookmark and removes it from the list", async () => {
    listBookmarks.mockResolvedValue({
      data: [bookmark({ id: 7, label: "待删除" })],
    });
    deleteBookmark.mockResolvedValue({});
    renderPanel();

    fireEvent.click(await screen.findByTitle("删除书签"));
    await waitFor(() => expect(deleteBookmark).toHaveBeenCalledWith("10", 7));
    await waitFor(() =>
      expect(screen.queryByText("待删除")).not.toBeInTheDocument()
    );
  });

  it("navigates when a bookmark row is clicked", async () => {
    listBookmarks.mockResolvedValue({
      data: [bookmark({ id: 1, chapter_id: 2 })],
    });
    const onNavigate = vi.fn();
    const onOpenChange = vi.fn();
    renderPanel({ onNavigate, onOpenChange });

    fireEvent.click(
      await screen.findByLabelText("跳转到第二章 42%")
    );
    expect(onNavigate).toHaveBeenCalledWith(
      expect.objectContaining({ id: 1, chapter_id: 2 })
    );
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
