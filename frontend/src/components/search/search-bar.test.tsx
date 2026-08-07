import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SearchBar } from "./search-bar";
import type { Novel, SearchResult } from "@/lib/api";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

const mocks = vi.hoisted(() => ({
  novelList: vi.fn(),
  globalSearch: vi.fn(),
  inNovelSearch: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    novelsApi: {
      list: mocks.novelList,
    },
    searchApi: {
      global: mocks.globalSearch,
      inNovel: mocks.inNovelSearch,
    },
  };
});

const novel: Novel = {
  id: 1,
  title: "雾城",
  author: null,
  description: null,
  genre: null,
  word_count: 1000,
  chapter_count: 3,
  status: "ready",
  chunk_count: 5,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const result: SearchResult = {
  novel_id: 1,
  novel_title: "雾城",
  chapter_id: 3,
  chapter_title: "第三章",
  chunk_id: "c3",
  chunk_index: 2,
  content_snippet: "雾中传来<mark>铃铛</mark>声",
  score: 0.85,
};

describe("SearchBar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.novelList.mockResolvedValue({ data: { items: [novel], total: 1 } });
    mocks.globalSearch.mockResolvedValue({
      data: { results: [result], total: 1 },
    });
    mocks.inNovelSearch.mockResolvedValue({
      data: { results: [result], total: 1 },
    });
  });

  it("加载书架并提供书本选择器", async () => {
    render(<SearchBar />);
    await waitFor(() => expect(mocks.novelList).toHaveBeenCalled());
    const select = screen.getByLabelText("选择要检索的书本");
    expect(select).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /雾城/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "全部作品" })).toBeInTheDocument();
  });

  it("showNovelSelect=false 时不加载书架", () => {
    render(<SearchBar showNovelSelect={false} />);
    expect(mocks.novelList).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("选择要检索的书本")).not.toBeInTheDocument();
  });

  it("输入关键词触发防抖全局搜索并展示预览结果", async () => {
    render(<SearchBar />);
    fireEvent.change(screen.getByPlaceholderText("搜索全部作品中的原文…"), {
      target: { value: "铃铛" },
    });
    await waitFor(() => expect(mocks.globalSearch).toHaveBeenCalledWith("铃铛", 5));
    await screen.findByText(/雾中传来/);
    expect(screen.getByText("85%")).toBeInTheDocument();
  });

  it("选中某本书后按小说内搜索，且显示限定范围提示", async () => {
    render(<SearchBar />);
    await waitFor(() =>
      expect(screen.getByRole("option", { name: /雾城/ })).toBeInTheDocument()
    );
    fireEvent.change(screen.getByLabelText("选择要检索的书本"), {
      target: { value: "1" },
    });
    fireEvent.change(screen.getByPlaceholderText(/在《雾城》中搜索/), {
      target: { value: "铃声" },
    });
    await waitFor(() =>
      expect(mocks.inNovelSearch).toHaveBeenCalledWith(1, "铃声", 5)
    );
    await screen.findByText(/当前范围：仅《雾城》/);
  });

  it("回车触发 router.push 跳转 /search", async () => {
    render(<SearchBar />);
    const input = screen.getByPlaceholderText("搜索全部作品中的原文…");
    fireEvent.change(input, { target: { value: "迷雾" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith("/search?q=%E8%BF%B7%E9%9B%BE")
    );
  });

  it("搜索按钮禁用态：空关键词不可点", () => {
    render(<SearchBar />);
    expect(screen.getByRole("button", { name: "搜索" })).toBeDisabled();
  });

  it("搜索失败时显示错误提示", async () => {
    mocks.globalSearch.mockRejectedValue(new Error("boom"));
    render(<SearchBar />);
    fireEvent.change(screen.getByPlaceholderText("搜索全部作品中的原文…"), {
      target: { value: "异常" },
    });
    await screen.findByText("搜索失败，请稍后重试");
  });

  it("无结果时预览下拉保持隐藏（无 loading/error/results）", async () => {
    mocks.globalSearch.mockResolvedValue({ data: { results: [], total: 0 } });
    render(<SearchBar />);
    fireEvent.change(screen.getByPlaceholderText("搜索全部作品中的原文…"), {
      target: { value: "无结果词" },
    });
    await waitFor(() => expect(mocks.globalSearch).toHaveBeenCalled());
    await waitFor(() =>
      expect(screen.queryByText("未找到相关结果")).not.toBeInTheDocument()
    );
  });

  it("点击预览结果跳转到阅读页", async () => {
    render(<SearchBar />);
    fireEvent.change(screen.getByPlaceholderText("搜索全部作品中的原文…"), {
      target: { value: "铃铛" },
    });
    const item = await screen.findByText(/雾中传来/);
    fireEvent.click(item);
    await waitFor(() =>
      expect(push).toHaveBeenCalledWith("/novels/1?chapter=3&chunk=2")
    );
  });

  it("初始 query 与 novelId 回填", async () => {
    render(<SearchBar initialQuery="雾" initialNovelId="1" />);
    expect(screen.getByDisplayValue("雾")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByLabelText("选择要检索的书本")).toHaveValue("1")
    );
    // 选中小说后 placeholder 变为「在《雾城》中搜索…」
    await screen.findByPlaceholderText(/在《雾城》中搜索/);
  });

  it("⌘K 聚焦输入框", () => {
    render(<SearchBar />);
    const input = screen.getByPlaceholderText("搜索全部作品中的原文…");
    fireEvent.keyDown(document, { key: "k", metaKey: true });
    expect(input).toHaveFocus();
  });

  it("Escape 关闭预览并失焦", async () => {
    render(<SearchBar />);
    fireEvent.change(screen.getByPlaceholderText("搜索全部作品中的原文…"), {
      target: { value: "铃铛" },
    });
    await screen.findByText(/雾中传来/);
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() =>
      expect(screen.queryByText(/雾中传来/)).not.toBeInTheDocument()
    );
  });
});
