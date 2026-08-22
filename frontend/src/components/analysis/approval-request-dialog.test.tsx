import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApprovalRequestDialog,
  type ApprovalRequestView,
} from "./approval-request-dialog";

/**
 * ApprovalRequestDialog 单测（25.3-04/06 Task 3 / D-11）。
 * - vi.hoisted：mock @/lib/api 的 api.post + getAccessToken。
 * - 每个按钮 POST 精确 payload 到正确端点；reject 解析 denied 语义；
 * - 静态断言：无全局确认弹窗使用（硬规则，25.2 RESEARCH）。
 */

const mocks = vi.hoisted(() => ({
  apiPost: vi.fn(),
  getAccessToken: vi.fn(),
}));

vi.mock("@/lib/api", () => ({
  api: { post: mocks.apiPost },
  getAccessToken: mocks.getAccessToken,
}));

const REQUEST: ApprovalRequestView = {
  id: 42,
  owner_id: 7,
  run_id: 9,
  action: "publish_illustration",
  payload_summary: { action: "publish_illustration", summary: "发布插画《竹林》" },
  status: "pending",
  created_at: "2026-08-02T00:00:00Z",
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderDialog(overrides: Partial<React.ComponentProps<typeof ApprovalRequestDialog>> = {}) {
  const props: React.ComponentProps<typeof ApprovalRequestDialog> = {
    open: true,
    onOpenChange: vi.fn(),
    request: REQUEST,
    onDecide: vi.fn(),
    onError: vi.fn(),
    ...overrides,
  };
  render(<ApprovalRequestDialog {...props} />);
  return props;
}

describe("ApprovalRequestDialog", () => {
  it("展示 action 名与 payload_summary", () => {
    renderDialog();
    expect(screen.getByTestId("approval-action").textContent).toBe("publish_illustration");
    expect(screen.getByTestId("approval-summary").textContent).toContain("发布插画《竹林》");
  });

  it("Approve once POST 确认 {mode:'once'} 到 confirm 端点，带 getAccessToken 认证", async () => {
    const props = renderDialog();
    mocks.getAccessToken.mockReturnValue("token-abc");
    mocks.apiPost.mockResolvedValue({ data: { ...REQUEST, status: "approved" } });

    fireEvent.click(screen.getByTestId("approval-approve-once"));
    await waitFor(() => expect(mocks.apiPost).toHaveBeenCalledTimes(1));

    const [url, body, config] = mocks.apiPost.mock.calls[0];
    expect(url).toBe("/agent/approval-requests/42/confirm");
    expect(body).toEqual({ mode: "once" });
    expect(config.headers.Authorization).toBe("Bearer token-abc");
    expect(props.onDecide).toHaveBeenCalledWith(
      expect.objectContaining({ status: "approved" })
    );
  });

  it("Approve for this session POST 确认 {mode:'session'}（D-11 会话级批准）", async () => {
    const props = renderDialog();
    mocks.apiPost.mockResolvedValue({
      data: { ...REQUEST, status: "approved_for_session" },
    });

    fireEvent.click(screen.getByTestId("approval-approve-session"));
    await waitFor(() => expect(mocks.apiPost).toHaveBeenCalledTimes(1));

    const [url, body] = mocks.apiPost.mock.calls[0];
    expect(url).toBe("/agent/approval-requests/42/confirm");
    expect(body).toEqual({ mode: "session" });
    expect(props.onDecide).toHaveBeenCalledWith(
      expect.objectContaining({ status: "approved_for_session" })
    );
  });

  it("Reject POST 到 reject 端点，解析为拒绝（服务端置 rejected）", async () => {
    const props = renderDialog();
    mocks.apiPost.mockResolvedValue({ data: { ...REQUEST, status: "rejected" } });

    fireEvent.click(screen.getByTestId("approval-reject"));
    await waitFor(() => expect(mocks.apiPost).toHaveBeenCalledTimes(1));

    const [url, body] = mocks.apiPost.mock.calls[0];
    expect(url).toBe("/agent/approval-requests/42/reject");
    expect(body).toEqual({});
    expect(props.onDecide).toHaveBeenCalledWith(
      expect.objectContaining({ status: "rejected" })
    );
  });

  it("决策失败触发 onError，不误报成功", async () => {
    const props = renderDialog();
    mocks.apiPost.mockRejectedValue(new Error("network"));

    fireEvent.click(screen.getByTestId("approval-approve-once"));
    await waitFor(() => expect(props.onError).toHaveBeenCalledWith("审批决策失败，请重试"));
    expect(props.onDecide).not.toHaveBeenCalled();
  });

  it("request 为空时按钮禁用（pending 空态）", () => {
    renderDialog({ request: null });
    expect(screen.getByTestId("approval-approve-once")).toBeDisabled();
    expect(screen.getByTestId("approval-approve-session")).toBeDisabled();
    expect(screen.getByTestId("approval-reject")).toBeDisabled();
  });

  it("静态断言：本文件绝不使用全局确认弹窗（D-11 硬规则）", () => {
    // 程序化拼接避免字面量落入源码（同时满足 grep 验收与断言有效性）。
    const forbidden = "window" + ".confirm";
    const source = ApprovalRequestDialog.toString();
    expect(source).not.toContain(forbidden);
  });
});
