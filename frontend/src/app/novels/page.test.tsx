import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import NovelsPage from "./page";
import type { Novel } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  fetchNovels: vi.fn().mockResolvedValue(undefined),
  deleteNovel: vi.fn().mockResolvedValue(undefined),
  deleteNovels: vi.fn().mockResolvedValue(undefined),
  renameNovel: vi.fn().mockResolvedValue(undefined),
  novels: [] as Novel[],
  loading: false,
}));

vi.mock("@/hooks/use-novels", () => ({
  useNovels: () => ({
    novels: mocks.novels,
    loading: mocks.loading,
    fetchNovels: mocks.fetchNovels,
    deleteNovel: mocks.deleteNovel,
    deleteNovels: mocks.deleteNovels,
    renameNovel: mocks.renameNovel,
  }),
}));

vi.mock("@/components/bookshelf/book-shelf", () => ({
  BookShelf: (props: {
    novels: Novel[];
    selectionMode?: boolean;
    onSelectedChange?: (id: number, selected: boolean) => void;
    selectedIds?: Set<number>;
  }) => (
    <div data-testid="mock-book-shelf">
      {props.novels.map((n) => (
        <button
          key={n.id}
          type="button"
          aria-label={`选择《${n.title}》`}
          onClick={() => props.onSelectedChange?.(n.id, !props.selectedIds?.has(n.id))}
        >
          {n.title}
        </button>
      ))}
      {props.selectionMode ? <span>批量模式</span> : null}
    </div>
  ),
}));

vi.mock("@/components/novel-upload-dialog", () => ({
  NovelUploadDialog: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="mock-upload-dialog">{children}</div>
  ),
}));

vi.mock("next/link", () => ({
  default: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

const novelA: Novel = {
  id: 1,
  title: "雾城",
  author: "甲",
  description: null,
  genre: null,
  word_count: 1000,
  chapter_count: 3,
  status: "ready",
  chunk_count: 1,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const novelB: Novel = {
  id: 2,
  title: "云海",
  author: "乙",
  description: null,
  genre: null,
  word_count: 500,
  chapter_count: 1,
  status: "importing",
  chunk_count: 0,
  created_at: "2026-02-01T00:00:00Z",
  updated_at: "2026-02-01T00:00:00Z",
};

describe("NovelsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.novels = [novelA, novelB];
    mocks.loading = false;
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  it("渲染书架标题与书本", () => {
    render(<NovelsPage />);
    expect(screen.getByText("我的书架")).toBeInTheDocument();
    expect(screen.getByText("雾城")).toBeInTheDocument();
    expect(screen.getByText("云海")).toBeInTheDocument();
  });

  it("加载中且列表为空时展示骨架", () => {
    mocks.novels = [];
    mocks.loading = true;
    render(<NovelsPage />);
    expect(screen.queryByTestId("mock-book-shelf")).not.toBeInTheDocument();
  });

  it("空书架展示引导 EmptyState", () => {
    mocks.novels = [];
    render(<NovelsPage />);
    expect(screen.getByText("书架是空的")).toBeInTheDocument();
  });

  it("搜索过滤小说", () => {
    render(<NovelsPage />);
    fireEvent.change(screen.getByPlaceholderText("搜索小说标题、作者..."), {
      target: { value: "云海" },
    });
    expect(screen.queryByText("雾城")).not.toBeInTheDocument();
    expect(screen.getByText("云海")).toBeInTheDocument();
  });

  it("按状态筛选", () => {
    render(<NovelsPage />);
    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "importing" } });
    expect(screen.queryByText("雾城")).not.toBeInTheDocument();
    expect(screen.getByText("云海")).toBeInTheDocument();
  });

  it("按标题排序", () => {
    render(<NovelsPage />);
    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[1], { target: { value: "title" } });
    const shelf = screen.getByTestId("mock-book-shelf");
    const titles = Array.from(shelf.querySelectorAll("button")).map(
      (b) => b.textContent
    );
    expect(titles[0]).toContain("雾城");
    expect(titles[1]).toContain("云海");
  });

  it("进入批量模式、全选、删除所选", async () => {
    render(<NovelsPage />);
    fireEvent.click(screen.getByRole("button", { name: "批量管理" }));
    expect(screen.getByText("批量模式")).toBeInTheDocument();
    expect(screen.getByText("已选择 0 本")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "全选当前结果" }));
    expect(screen.getByText("已选择 2 本")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "删除所选" }));
    await waitFor(() => {
      const call = mocks.deleteNovels.mock.calls[0];
      expect(call && new Set(call[0])).toEqual(new Set([1, 2]));
    });
  });

  it("退出批量管理清除选择", () => {
    render(<NovelsPage />);
    fireEvent.click(screen.getByRole("button", { name: "批量管理" }));
    fireEvent.click(screen.getByRole("button", { name: "退出批量管理" }));
    expect(screen.queryByText("批量模式")).not.toBeInTheDocument();
  });

  it("筛选无结果展示提示", () => {
    render(<NovelsPage />);
    fireEvent.change(screen.getByPlaceholderText("搜索小说标题、作者..."), {
      target: { value: "不存在的小说" },
    });
    expect(screen.getByText("没有找到匹配的小说")).toBeInTheDocument();
  });
});
