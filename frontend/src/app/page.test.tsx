import { render, screen } from "@testing-library/react";
import type { AnchorHTMLAttributes } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import HomePage from "./page";
import type { Novel } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  novels: [] as Novel[],
  loading: false,
}));

vi.mock("@/hooks/use-novels", () => ({
  useNovels: () => ({
    novels: mocks.novels,
    loading: mocks.loading,
    fetchNovels: vi.fn(),
  }),
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={String(href)} {...props}>{children}</a>
  ),
}));

vi.mock("@/components/flip-book", () => ({
  FlipBook: ({ pages, ariaLabel }: { pages: unknown[]; ariaLabel: string }) => (
    <div data-testid="flip-book" aria-label={ariaLabel}>
      {pages.map((page) => (page as { front: React.ReactNode }).front)}
    </div>
  ),
}));

const novel: Novel = {
  id: 7,
  title: "雾城",
  author: "甲",
  description: null,
  genre: null,
  word_count: 12345,
  chapter_count: 4,
  status: "ready",
  chunk_count: 1,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-03-01T00:00:00Z",
};

describe("HomePage", () => {
  beforeEach(() => {
    mocks.novels = [];
    mocks.loading = false;
  });

  it("渲染快速操作与统计", () => {
    render(<HomePage />);
    expect(screen.getAllByText("导入原文").length).toBeGreaterThan(0);
    expect(screen.getAllByText("证据检索").length).toBeGreaterThan(0);
    expect(screen.getAllByText("检索评测").length).toBeGreaterThan(0);
    expect(screen.getAllByText("创作草稿").length).toBeGreaterThan(0);
    expect(screen.getAllByText("已入库作品").length).toBeGreaterThan(0);
    expect(screen.getAllByText("可阅读章节").length).toBeGreaterThan(0);
    expect(screen.getAllByText("原文字数").length).toBeGreaterThan(0);
  });

  it("空书架展示引导文案", () => {
    render(<HomePage />);
    expect(screen.getAllByText("书架还空着，等第一本书。").length).toBeGreaterThan(0);
  });

  it("有小说时展示最近作品与统计值", () => {
    mocks.novels = [novel];
    render(<HomePage />);
    expect(screen.getAllByText("雾城").length).toBeGreaterThan(0);
    expect(screen.getAllByText("甲 · 4 章").length).toBeGreaterThan(0);
    // 统计：1 本 / 4 章 / 1.2万字
    expect(screen.getAllByText("1").length).toBeGreaterThan(0);
    expect(screen.getAllByText("4").length).toBeGreaterThan(0);
    expect(screen.getAllByText("1.2万").length).toBeGreaterThan(0);
  });

  it("加载中统计显示破折号", () => {
    mocks.loading = true;
    render(<HomePage />);
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });
});
