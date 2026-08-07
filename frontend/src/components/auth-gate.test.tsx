import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthGate } from "./auth-gate";
import type { AuthUser } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  me: vi.fn(),
  login: vi.fn(),
  register: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    authApi: {
      me: mocks.me,
      login: mocks.login,
      register: mocks.register,
      logout: vi.fn(),
    },
  };
});

const user: AuthUser = {
  id: 1,
  username: "writer",
  email: "writer@example.com",
  is_active: true,
};

describe("AuthGate", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.me.mockResolvedValue({ data: user });
    mocks.login.mockResolvedValue({
      data: { access_token: "tok", token_type: "bearer", user_id: 1, username: "writer" },
    });
    mocks.register.mockResolvedValue({ data: user });
  });

  it("验证会话期间展示 loading", () => {
    mocks.me.mockReturnValue(new Promise(() => {}));
    render(<AuthGate>content</AuthGate>);
    expect(screen.getByText("正在验证会话...")).toBeInTheDocument();
  });

  it("已登录用户直接渲染 children", async () => {
    render(<AuthGate><div>书架内容</div></AuthGate>);
    expect(await screen.findByText("书架内容")).toBeInTheDocument();
  });

  it("未登录时展示登录表单，提交成功后可进入", async () => {
    mocks.me.mockResolvedValue({ data: null });
    render(<AuthGate><div>书架内容</div></AuthGate>);
    fireEvent.change(await screen.findByLabelText("用户名"), {
      target: { value: "writer" },
    });
    fireEvent.change(screen.getByLabelText("密码"), {
      target: { value: "password1" },
    });
    // login 成功后 me() 返回当前用户
    mocks.me.mockResolvedValue({ data: user });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    await waitFor(() =>
      expect(mocks.login).toHaveBeenCalledWith("writer", "password1")
    );
    expect(await screen.findByText("书架内容")).toBeInTheDocument();
  });

  it("切换注册模式展示邮箱字段", async () => {
    mocks.me.mockResolvedValue({ data: null });
    render(<AuthGate><div>内容</div></AuthGate>);
    fireEvent.click(await screen.findByRole("button", { name: "创建账户" }));
    expect(screen.getByLabelText("邮箱")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "建立你的故事库" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "注册并登录" })).toBeInTheDocument();
  });

  it("注册流程调用 register 再 login", async () => {
    mocks.me.mockResolvedValue({ data: null });
    render(<AuthGate><div>内容</div></AuthGate>);
    fireEvent.click(await screen.findByRole("button", { name: "创建账户" }));
    fireEvent.change(screen.getByLabelText("用户名"), {
      target: { value: "newbie" },
    });
    fireEvent.change(screen.getByLabelText("邮箱"), {
      target: { value: "newbie@example.com" },
    });
    fireEvent.change(screen.getByLabelText("密码"), {
      target: { value: "password1" },
    });
    mocks.me.mockResolvedValue({ data: user });
    fireEvent.click(screen.getByRole("button", { name: "注册并登录" }));
    await waitFor(() =>
      expect(mocks.register).toHaveBeenCalledWith(
        "newbie",
        "newbie@example.com",
        "password1"
      )
    );
    expect(mocks.login).toHaveBeenCalledWith("newbie", "password1");
  });

  it("登录失败展示错误提示", async () => {
    mocks.me.mockResolvedValue({ data: null });
    mocks.login.mockRejectedValue(
      Object.assign(new Error("用户名或密码错误"), { response: { status: 401 } })
    );
    render(<AuthGate><div>内容</div></AuthGate>);
    fireEvent.change(await screen.findByLabelText("用户名"), {
      target: { value: "bad" },
    });
    fireEvent.change(screen.getByLabelText("密码"), {
      target: { value: "wrongpass" },
    });
    fireEvent.click(screen.getByRole("button", { name: "登录" }));
    expect(await screen.findByText("用户名或密码错误")).toBeInTheDocument();
  });

  it("me() 失败视为未登录", async () => {
    mocks.me.mockRejectedValue(new Error("network"));
    render(<AuthGate><div>内容</div></AuthGate>);
    expect(await screen.findByLabelText("用户名")).toBeInTheDocument();
  });
});
