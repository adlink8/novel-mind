import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { AnchorHTMLAttributes } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "./app-shell";

vi.mock("next/navigation", () => ({
  usePathname: () => "/novels",
}));

vi.mock("next/link", () => ({
  default: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a href={String(href)} {...props}>{children}</a>
  ),
}));

describe("AppShell", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("渲染桌面导航项与移动导航", async () => {
    render(
      <AppShell>
        <p>页面内容</p>
      </AppShell>
    );
    expect(screen.getByText("页面内容")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "主导航" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "移动导航" })).toBeInTheDocument();
    // 当前路径 /novels 高亮（桌面 + 移动各一个）
    const shelfLinks = screen.getAllByRole("link", { name: "书架" });
    expect(shelfLinks.length).toBeGreaterThan(0);
    for (const link of shelfLinks) {
      expect(link).toHaveAttribute("aria-current", "page");
    }
  });

  it("工作台导航仅在根路径高亮", () => {
    render(<AppShell><p>x</p></AppShell>);
    for (const link of screen.getAllByRole("link", { name: "工作台" })) {
      expect(link).not.toHaveAttribute("aria-current");
    }
  });

  it("切换折叠导航并持久化到 localStorage", async () => {
    render(<AppShell><p>x</p></AppShell>);
    const nav = screen.getByTestId("app-shell-nav");
    expect(nav).toHaveAttribute("data-collapsed", "false");
    fireEvent.click(screen.getByTestId("app-shell-nav-toggle"));
    await waitFor(() =>
      expect(nav).toHaveAttribute("data-collapsed", "true")
    );
    expect(localStorage.getItem("novelmind:app-shell:nav-collapsed")).toBe("1");
  });

  it("恢复折叠状态并写入新状态", async () => {
    localStorage.setItem("novelmind:app-shell:nav-collapsed", "1");
    render(<AppShell><p>x</p></AppShell>);
    await waitFor(() =>
      expect(screen.getByTestId("app-shell-nav")).toHaveAttribute(
        "data-collapsed",
        "true"
      )
    );
    fireEvent.click(screen.getByTestId("app-shell-nav-toggle"));
    await waitFor(() =>
      expect(localStorage.getItem("novelmind:app-shell:nav-collapsed")).toBe("0")
    );
  });

  it("analysis 路径使用工作台布局", () => {
    render(<AppShell><p>x</p></AppShell>);
    expect(screen.getAllByRole("link", { name: "分析" }).length).toBeGreaterThan(0);
  });

  it("跳到主内容链接存在", () => {
    render(<AppShell><p>x</p></AppShell>);
    expect(screen.getByRole("link", { name: "跳到主内容" })).toHaveAttribute(
      "href",
      "#main-content"
    );
  });
});
