import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthGate } from "./auth-gate";

const mocks = vi.hoisted(() => ({
  me: vi.fn(),
  localAutoLogin: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    authApi: {
      me: mocks.me,
      localAutoLogin: mocks.localAutoLogin,
    },
  };
});

describe("AuthGate", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.me.mockResolvedValue({ data: { id: 1 } });
    mocks.localAutoLogin.mockResolvedValue({ data: { access_token: "auto-token" } });
  });

  it("本地工作区初始化期间展示启动状态", () => {
    mocks.me.mockReturnValue(new Promise(() => {}));
    render(<AuthGate>content</AuthGate>);
    expect(screen.getByText("正在启动工作区...")).toBeInTheDocument();
  });

  it("已有本地会话时直接渲染产品页面", async () => {
    render(<AuthGate><div>书架内容</div></AuthGate>);
    expect(await screen.findByText("书架内容")).toBeInTheDocument();
    expect(mocks.localAutoLogin).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("用户名")).not.toBeInTheDocument();
  });

  it("会话缺失时自动建立本地会话且不展示登录表单", async () => {
    mocks.me.mockRejectedValue(new Error("anonymous"));
    render(<AuthGate><div>主页内容</div></AuthGate>);
    expect(await screen.findByText("主页内容")).toBeInTheDocument();
    expect(mocks.localAutoLogin).toHaveBeenCalledTimes(1);
    expect(screen.queryByLabelText("用户名")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "登录" })).not.toBeInTheDocument();
  });

  it("会话失效且本地自动登录不可用时展示登录卡片", async () => {
    mocks.me.mockRejectedValue(new Error("anonymous"));
    mocks.localAutoLogin.mockRejectedValue(new Error("bootstrap unavailable"));
    render(<AuthGate><div>工作台内容</div></AuthGate>);
    expect(await screen.findByText("NovelMind")).toBeInTheDocument();
    expect(screen.getByLabelText("密码")).toBeInTheDocument();
  });
});
