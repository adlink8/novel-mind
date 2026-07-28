import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { AnchorHTMLAttributes } from "react";
import { describe, expect, it, vi } from "vitest";

import { NovelCard } from "./novel-card";
import type { Novel } from "@/lib/api";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={String(href)} {...props}>{children}</a>
  ),
}));

const novel: Novel = {
  id: 7,
  title: "旧书名",
  author: null,
  description: null,
  genre: null,
  word_count: 1000,
  chapter_count: 10,
  status: "ready",
  chunk_count: 0,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

describe("NovelCard", () => {
  it("在卡片底部提供稳定的管理操作", () => {
    render(<NovelCard novel={novel} onDelete={vi.fn()} onRename={vi.fn()} />);

    const timeline = screen.getByRole("button", { name: "时间线" });
    const footer = timeline.parentElement;
    expect(footer).toHaveClass("border-t");
    expect(timeline.closest(".paper-surface")?.className).not.toContain(
      "min-h-[480px]"
    );
    expect(screen.getByRole("button", { name: "重命名《旧书名》" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删除《旧书名》" })).toBeInTheDocument();
  });

  it("提交新的书籍名称", async () => {
    const onRename = vi.fn().mockResolvedValue(undefined);
    render(<NovelCard novel={novel} onRename={onRename} />);

    fireEvent.click(screen.getByRole("button", { name: "重命名《旧书名》" }));
    fireEvent.change(screen.getByLabelText("书籍名称"), { target: { value: "新书名" } });
    fireEvent.click(screen.getByRole("button", { name: "保存名称" }));

    await waitFor(() => expect(onRename).toHaveBeenCalledWith(7, "新书名"));
  });

  it("批量模式显示选择框并隐藏单本删除", () => {
    const onSelectedChange = vi.fn();
    render(
      <NovelCard
        novel={novel}
        onDelete={vi.fn()}
        selectionMode
        selected
        onSelectedChange={onSelectedChange}
      />
    );

    const checkbox = screen.getByRole("checkbox", { name: "选择《旧书名》" });
    expect(checkbox).toBeChecked();
    fireEvent.click(checkbox);
    expect(onSelectedChange).toHaveBeenCalledWith(7, false);
    expect(screen.queryByRole("button", { name: "删除《旧书名》" })).not.toBeInTheDocument();
  });
});
