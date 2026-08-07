import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import NovelReaderPage from "./page";
import type { Chapter, Novel } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  getParams: vi.fn(() => ({ id: "7" })),
  getSearchParams: vi.fn(() => new URLSearchParams()),
  get: vi.fn(),
  getChapters: vi.fn(),
  getChapter: vi.fn(),
  updateProgress: vi.fn().mockResolvedValue({ data: {} }),
  listAnchors: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useParams: () => mocks.getParams(),
  useRouter: () => ({ push: mocks.push }),
  useSearchParams: () => mocks.getSearchParams(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    novelsApi: {
      get: mocks.get,
      getChapters: mocks.getChapters,
      getChapter: mocks.getChapter,
      updateProgress: mocks.updateProgress,
    },
  };
});

vi.mock("@/lib/illustration-anchor", async () => {
  const actual = await vi.importActual<typeof import("@/lib/illustration-anchor")>(
    "@/lib/illustration-anchor"
  );
  return {
    ...actual,
    illustrationAnchorApi: {
      list: mocks.listAnchors,
    },
  };
});

// 简化 reader 子组件，聚焦页面自身逻辑
vi.mock("@/components/reader/reader-content", () => ({
  ReaderContent: ({
    onChapterProgress,
    hasNextChapter,
    hasPrevChapter,
  }: {
    onChapterProgress: (p: number) => void;
    hasNextChapter: boolean;
    hasPrevChapter: boolean;
  }) => (
    <div data-testid="reader-content">
      <button type="button" data-testid="progress-50" onClick={() => onChapterProgress(50)}>
        50%
      </button>
      <span>{hasNextChapter ? "有下一章" : "无下一章"}</span>
      <span>{hasPrevChapter ? "有上一章" : "无上一章"}</span>
    </div>
  ),
}));

vi.mock("@/components/reader/reader-chat-panel", () => ({
  ReaderChatPanel: ({ layout }: { layout: string }) => (
    <div data-testid={`chat-panel-${layout}`} />
  ),
}));

vi.mock("@/components/reader/reader-bookmarks", () => ({
  ReaderBookmarks: () => <div data-testid="bookmarks" />,
}));

vi.mock("@/components/reader/reader-preferences", async () => {
  const actual = await vi.importActual<
    typeof import("@/components/reader/reader-preferences")
  >("@/components/reader/reader-preferences");
  return {
    ...actual,
    loadReaderPreferences: () =>
      actual.DEFAULT_READER_PREFERENCES &&
      window.__TEST_IMMERSIVE__
        ? { ...actual.DEFAULT_READER_PREFERENCES, immersive: true }
        : actual.DEFAULT_READER_PREFERENCES,
    saveReaderPreferences: vi.fn(),
    ReaderPreferencesPanel: ({ preferences }: { preferences: unknown }) => (
      <div data-testid="preferences-panel" data-mode={JSON.stringify(preferences)} />
    ),
  };
});

vi.mock("@/components/reader/chapter-sidebar", () => ({
  ChapterSidebar: () => <div data-testid="chapter-sidebar" />,
}));

vi.mock("@/components/reader/progress-bar", () => ({
  ProgressBar: ({ chapterTitle }: { chapterTitle: string }) => (
    <div data-testid="progress-bar">{chapterTitle}</div>
  ),
}));

vi.mock("@/components/reader/search-panel", () => ({
  SearchPanel: () => <div data-testid="search-panel" />,
}));

vi.mock("@/components/book-loader", () => ({
  BookLoader: ({ label }: { label?: string }) => (
    <div data-testid="book-loader">{label}</div>
  ),
}));

const novel: Novel = {
  id: 7,
  title: "雾城",
  author: "甲",
  description: null,
  genre: null,
  word_count: 5000,
  chapter_count: 2,
  status: "ready",
  chunk_count: 1,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const chapter1: Chapter = {
  id: 101,
  novel_id: 7,
  chapter_number: 1,
  title: "第一章",
  content: "雾城第一章内容",
  chunk_count: 0,
  created_at: "2026-01-01T00:00:00Z",
};

const chapter2: Chapter = {
  id: 102,
  novel_id: 7,
  chapter_number: 2,
  title: "第二章",
  content: "雾城第二章内容",
  chunk_count: 0,
  created_at: "2026-01-01T00:00:00Z",
};

declare global {
  interface Window {
    __TEST_IMMERSIVE__?: boolean;
  }
}

describe("NovelReaderPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    window.scrollTo = vi.fn();
    window.__TEST_IMMERSIVE__ = false;
    Object.defineProperty(window, "innerWidth", {
      writable: true,
      configurable: true,
      value: 1024,
    });
    mocks.getParams.mockReturnValue({ id: "7" });
    mocks.getSearchParams.mockReturnValue(new URLSearchParams());
    mocks.get.mockResolvedValue({ data: novel });
    mocks.getChapters.mockResolvedValue({ data: [chapter1, chapter2] });
    mocks.getChapter.mockResolvedValue({ data: chapter1 });
    mocks.listAnchors.mockResolvedValue({ data: { items: [] } });
  });

  it("加载中展示 BookLoader", () => {
    mocks.get.mockReturnValue(new Promise(() => {}));
    render(<NovelReaderPage />);
    expect(screen.getByTestId("book-loader")).toBeInTheDocument();
  });

  it("渲染阅读器：标题、正文、章节导航", async () => {
    render(<NovelReaderPage />);
    expect(await screen.findByText("雾城")).toBeInTheDocument();
    expect(await screen.findByTestId("reader-content")).toBeInTheDocument();
    await waitFor(() => expect(mocks.get).toHaveBeenCalledWith("7"));
    await waitFor(() => expect(mocks.getChapter).toHaveBeenCalledWith("7", "101"));
  });

  it("加载失败展示错误与返回书架按钮", async () => {
    mocks.get.mockRejectedValue(new Error("boom"));
    render(<NovelReaderPage />);
    expect(await screen.findByText("加载小说失败，请重试")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "返回书架" }));
    expect(mocks.push).toHaveBeenCalledWith("/novels");
  });

  it("下一章按钮切换到第二章", async () => {
    render(<NovelReaderPage />);
    await screen.findByTestId("reader-content");
    fireEvent.click(screen.getByRole("button", { name: "下一章" }));
    await waitFor(() => expect(mocks.getChapter).toHaveBeenCalledWith("7", "102"));
  });

  it("第一章时上一章按钮禁用", async () => {
    render(<NovelReaderPage />);
    await screen.findByTestId("reader-content");
    expect(screen.getByRole("button", { name: "上一章" })).toBeDisabled();
    // 第一章 → 有下一章
    expect(screen.getByText("有下一章")).toBeInTheDocument();
    expect(screen.getByText("无上一章")).toBeInTheDocument();
  });

  it("书架按钮持久化进度并返回", async () => {
    render(<NovelReaderPage />);
    await screen.findByTestId("reader-content");
    fireEvent.click(screen.getByRole("button", { name: "书架" }));
    expect(mocks.push).toHaveBeenCalledWith("/novels");
  });

  it("内容进度回调更新百分比", async () => {
    render(<NovelReaderPage />);
    await screen.findByTestId("reader-content");
    fireEvent.click(screen.getByTestId("progress-50"));
    await waitFor(() => expect(mocks.updateProgress).toHaveBeenCalled());
  });

  it("?chapter= 参数直接定位指定章", async () => {
    mocks.getSearchParams.mockReturnValue(new URLSearchParams("chapter=102"));
    render(<NovelReaderPage />);
    await waitFor(() => expect(mocks.getChapter).toHaveBeenCalledWith("7", "102"));
  });

  it("from=timeline 时展示时间线定位模式提示", async () => {
    mocks.getSearchParams.mockReturnValue(
      new URLSearchParams("chapter=102&from=timeline")
    );
    render(<NovelReaderPage />);
    expect(
      await screen.findByText("时间线定位模式 · 未改动你的阅读进度")
    ).toBeInTheDocument();
  });

  it("打开搜索面板（Ctrl+F 快捷键）", async () => {
    render(<NovelReaderPage />);
    await screen.findByTestId("reader-content");
    fireEvent.keyDown(window, { key: "f", ctrlKey: true });
    expect(await screen.findByTestId("search-panel")).toBeInTheDocument();
  });

  it("打开 AI 对话面板", async () => {
    render(<NovelReaderPage />);
    await screen.findByTestId("reader-content");
    fireEvent.click(screen.getByTestId("reader-chat-open"));
    // jsdom 默认 window.innerWidth=1024 → mobile 布局
    expect(await screen.findByTestId("chat-panel-mobile")).toBeInTheDocument();
  });

  it("无章节时渲染空内容", async () => {
    mocks.getChapters.mockResolvedValue({ data: [] });
    render(<NovelReaderPage />);
    expect(await screen.findByTestId("reader-content")).toBeInTheDocument();
  });

  it("桌面宽屏打开对话面板用桌面布局", async () => {
    Object.defineProperty(window, "innerWidth", {
      writable: true,
      configurable: true,
      value: 1440,
    });
    render(<NovelReaderPage />);
    await screen.findByTestId("reader-content");
    fireEvent.click(screen.getByTestId("reader-chat-open"));
    expect(await screen.findByTestId("chat-panel-desktop")).toBeInTheDocument();
  });

  it("沉浸模式渲染目录抽屉与底部控制层", async () => {
    window.__TEST_IMMERSIVE__ = true;
    render(<NovelReaderPage />);
    await screen.findByTestId("reader-content");
    expect(screen.getByRole("button", { name: "目录" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上一章" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下一章" })).toBeInTheDocument();
  });

  it("沉浸模式点按正文切换控制层", async () => {
    window.__TEST_IMMERSIVE__ = true;
    render(<NovelReaderPage />);
    const content = await screen.findByTestId("reader-content");
    fireEvent.click(content);
    // 点按后控制层隐藏（目录按钮消失）
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "目录" })).not.toBeInTheDocument()
    );
    fireEvent.click(content);
    expect(screen.getByRole("button", { name: "目录" })).toBeInTheDocument();
  });

  it("时间线定位模式切换章节后恢复进度可写", async () => {
    mocks.getSearchParams.mockReturnValue(
      new URLSearchParams("chapter=101&from=timeline")
    );
    render(<NovelReaderPage />);
    await screen.findByTestId("reader-content");
    fireEvent.click(screen.getByRole("button", { name: "下一章" }));
    await waitFor(() => expect(mocks.getChapter).toHaveBeenCalledWith("7", "102"));
    // 时间线定位提示消失（进度已恢复可写）
    await waitFor(() =>
      expect(screen.queryByText("时间线定位模式 · 未改动你的阅读进度")).not.toBeInTheDocument()
    );
  });
});
