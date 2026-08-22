import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SearchPage from "./page";
import type { Novel, SearchResult } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  globalSearch: vi.fn(),
  inNovelSearch: vi.fn(),
  novelGet: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useSearchParams: () =>
    new URLSearchParams(window.__TEST_PARAMS__ ? window.__TEST_PARAMS__ : ""),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    searchApi: {
      global: mocks.globalSearch,
      inNovel: mocks.inNovelSearch,
    },
    novelsApi: {
      get: mocks.novelGet,
      list: vi.fn().mockResolvedValue({ data: { items: [], total: 0 } }),
    },
  };
});

vi.mock("@/components/search/search-bar", () => ({
  SearchBar: (props: { initialQuery?: string; initialNovelId?: string }) => (
    <div data-testid="mock-search-bar">
      {props.initialQuery ?? ""}:{props.initialNovelId ?? ""}
    </div>
  ),
}));

const result: SearchResult = {
  novel_id: 1,
  novel_title: "雾城",
  chapter_id: null,
  chapter_title: null,
  chunk_id: 1,
  chunk_index: 0,
  content_snippet: "雾中铃铛",
  score: 0.9,
};

const novel: Novel = {
  id: 1,
  title: "雾城",
  author: null,
  description: null,
  genre: null,
  word_count: 10,
  chapter_count: 3,
  status: "ready",
  chunk_count: 5,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

declare global {
  interface Window {
    __TEST_PARAMS__?: string;
  }
}

describe("SearchPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.__TEST_PARAMS__ = "";
    mocks.globalSearch.mockResolvedValue({
      data: { results: [result], total: 1 },
    });
    mocks.novelGet.mockResolvedValue({ data: novel });
  });

  it("无关键词时展示引导空状态", async () => {
    render(<SearchPage />);
    expect(
      await screen.findByText("选择范围并输入关键词")
    ).toBeInTheDocument();
    expect(screen.getAllByText(/全部作品/).length).toBeGreaterThan(0);
    expect(screen.getByTestId("mock-search-bar")).toBeInTheDocument();
  });

  it("展示搜索结果卡片与计数", async () => {
    window.__TEST_PARAMS__ = "q=%E9%9B%BE%E9%93%83";
    render(<SearchPage />);
    expect(await screen.findByText(/在 全部作品 中找到 1 条相关原文/)).toBeInTheDocument();
    expect(await screen.findByText("雾中铃铛")).toBeInTheDocument();
    expect(screen.getByText(/雾城/)).toBeInTheDocument();
  });

  it("带 novel 参数时调用小说内搜索并展示范围", async () => {
    window.__TEST_PARAMS__ = "q=%E9%93%83&novel=1";
    render(<SearchPage />);
    await waitFor(() => expect(mocks.novelGet).toHaveBeenCalledWith("1"));
    await waitFor(() =>
      expect(mocks.inNovelSearch).toHaveBeenCalledWith(1, "铃", 20)
    );
    expect(await screen.findByText(/《雾城》/)).toBeInTheDocument();
  });

  it("无 novel 参数时调用全局搜索", async () => {
    window.__TEST_PARAMS__ = "q=%E9%9B%BE";
    render(<SearchPage />);
    await waitFor(() => expect(mocks.globalSearch).toHaveBeenCalledWith("雾", 20));
  });

  it("搜索失败时展示错误空状态", async () => {
    mocks.globalSearch.mockRejectedValue(new Error("boom"));
    window.__TEST_PARAMS__ = "q=%E9%9B%BE";
    render(<SearchPage />);
    expect(await screen.findByText("搜索出错")).toBeInTheDocument();
    expect(screen.getByText("搜索失败，请稍后重试")).toBeInTheDocument();
  });

  it("无结果时展示未找到空状态（novel 范围内）", async () => {
    mocks.inNovelSearch.mockResolvedValue({ data: { results: [], total: 0 } });
    window.__TEST_PARAMS__ = "q=%E9%9B%BE&novel=1";
    render(<SearchPage />);
    expect(await screen.findByText("未找到相关结果")).toBeInTheDocument();
    expect(
      screen.getByText(/在 《雾城》 中没有找到「雾」/)
    ).toBeInTheDocument();
  });

  it("novel 加载失败时回退为小说 #id", async () => {
    mocks.novelGet.mockRejectedValue(new Error("boom"));
    mocks.inNovelSearch.mockResolvedValue({ data: { results: [], total: 0 } });
    window.__TEST_PARAMS__ = "q=%E9%93%83&novel=1";
    render(<SearchPage />);
    expect(await screen.findByText(/在 小说 #1 中没有找到「铃」/)).toBeInTheDocument();
  });
});
