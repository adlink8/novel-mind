import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AccountSection } from "./account-section";
import type { AuthUser } from "@/lib/api";

const push = vi.fn();
const refresh = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, refresh }),
}));

const mocks = vi.hoisted(() => ({
  me: vi.fn(),
  logout: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    authApi: {
      me: mocks.me,
      logout: mocks.logout,
    },
  };
});

const user: AuthUser = {
  id: 1,
  username: "writer",
  email: "writer@example.com",
  is_active: true,
};

describe("AccountSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.me.mockResolvedValue({ data: user });
    mocks.logout.mockResolvedValue(undefined);
  });

  it("加载账户信息", async () => {
    render(<AccountSection chapter="壹" />);
    expect(await screen.findByText("writer")).toBeInTheDocument();
    expect(screen.getByText("writer@example.com")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "退出登录" })).toBeEnabled();
  });

  it("加载失败时显示未登录", async () => {
    mocks.me.mockRejectedValue(new Error("boom"));
    render(<AccountSection chapter="壹" />);
    expect(await screen.findByText("未登录")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "退出登录" })).toBeDisabled();
  });

  it("退出登录跳转首页并刷新", async () => {
    render(<AccountSection chapter="壹" />);
    await screen.findByText("writer");
    fireEvent.click(screen.getByRole("button", { name: "退出登录" }));
    await waitFor(() => expect(mocks.logout).toHaveBeenCalled());
    await waitFor(() => expect(push).toHaveBeenCalledWith("/"));
    expect(refresh).toHaveBeenCalled();
  });

  it("退出失败展示错误", async () => {
    mocks.logout.mockRejectedValue(new Error("boom"));
    render(<AccountSection chapter="壹" />);
    await screen.findByText("writer");
    fireEvent.click(screen.getByRole("button", { name: "退出登录" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("退出失败，请重试");
  });
});
