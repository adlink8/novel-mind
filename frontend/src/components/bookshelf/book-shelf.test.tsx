import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { AnchorHTMLAttributes } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BookShelf } from "./book-shelf";
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
  title: "雾城",
  author: "作者甲",
  description: null,
  genre: null,
  word_count: 5000,
  chapter_count: 10,
  status: "ready",
  chunk_count: 3,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

describe("BookShelf", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("渲染书本链接与标题、作者、操作条", () => {
    render(<BookShelf novels={[novel]} />);
    const link = screen.getByRole("link", { name: /雾城/ });
    expect(link).toHaveAttribute("href", "/novels/7");
    expect(screen.getByText("作者甲")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "《雾城》时间线分析" })
    ).toBeInTheDocument();
  });

  it("点击操作条时间线按钮跳转分析页", () => {
    render(<BookShelf novels={[novel]} />);
    fireEvent.click(screen.getByRole("button", { name: "《雾城》时间线分析" }));
    expect(push).toHaveBeenCalledWith("/analysis?novel=7");
  });

  it("reduced-motion 时点击书本直接走 Link（不触发动画跳转）", () => {
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: vi.fn().mockImplementation((query: string) => ({
        matches: query.includes("reduce"),
        media: query,
        onchange: null,
        addListener: vi.fn(),
        removeListener: vi.fn(),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
    render(<BookShelf novels={[novel]} />);
    const link = screen.getByRole("link", { name: /雾城/ });
    fireEvent.click(link);
    // reduced-motion：直接走默认锚点跳转，不调用 router.push
    expect(push).not.toHaveBeenCalled();
  });

  it("批量模式下点击书本切换勾选，无操作条", () => {
    const onSelectedChange = vi.fn();
    render(
      <BookShelf
        novels={[novel]}
        selectionMode
        selectedIds={new Set<number>([7])}
        onSelectedChange={onSelectedChange}
      />
    );
    const checkbox = screen.getByRole("checkbox", { name: "选择《雾城》" });
    expect(checkbox).toHaveAttribute("aria-checked", "true");
    fireEvent.click(checkbox);
    expect(onSelectedChange).toHaveBeenCalledWith(7, false);
    expect(screen.queryByRole("button", { name: "《雾城》时间线分析" })).not.toBeInTheDocument();
  });

  it("重命名对话框：提交新名称并调用 onRename", async () => {
    const onRename = vi.fn().mockResolvedValue(undefined);
    render(<BookShelf novels={[novel]} onRename={onRename} />);
    fireEvent.click(screen.getByRole("button", { name: "重命名《雾城》" }));
    expect(screen.getByRole("dialog", { name: "更改书籍名称" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("书籍名称"), {
      target: { value: "新书名" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存名称" }));
    await waitFor(() => expect(onRename).toHaveBeenCalledWith(7, "新书名"));
  });

  it("重命名空名称展示错误", async () => {
    render(<BookShelf novels={[novel]} onRename={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "重命名《雾城》" }));
    fireEvent.change(screen.getByLabelText("书籍名称"), { target: { value: "  " } });
    fireEvent.click(screen.getByRole("button", { name: "保存名称" }));
    expect(screen.getByRole("alert")).toHaveTextContent("书籍名称不能为空");
  });

  it("删除：二次确认后调用 onDelete", async () => {
    const onDelete = vi.fn().mockResolvedValue(undefined);
    render(<BookShelf novels={[novel]} onDelete={onDelete} />);
    fireEvent.click(screen.getByRole("button", { name: "删除《雾城》" }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(onDelete).toHaveBeenCalledWith(7));
  });

  it("删除：对话框点取消不调用 onDelete", async () => {
    const onDelete = vi.fn().mockResolvedValue(undefined);
    render(<BookShelf novels={[novel]} onDelete={onDelete} />);
    fireEvent.click(screen.getByRole("button", { name: "删除《雾城》" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(onDelete).not.toHaveBeenCalled();
  });

  it("非批量模式展示「导入新书」幽灵书", () => {
    render(<BookShelf novels={[novel]} />);
    expect(screen.getByRole("button", { name: "导入新书" })).toBeInTheDocument();
  });
});
