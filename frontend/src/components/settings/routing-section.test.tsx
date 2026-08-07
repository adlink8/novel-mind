import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { RoutingSection } from "./routing-section";

const mocks = vi.hoisted(() => ({
  routingPreference: "balanced",
  fetchRoutingPreference: vi.fn().mockResolvedValue(undefined),
  setRoutingPreference: vi.fn().mockResolvedValue(undefined),
}));

vi.mock("@/stores/aiConfigStore", () => ({
  useAIConfigStore: (selector: (s: unknown) => unknown) =>
    selector({
      routingPreference: mocks.routingPreference,
      fetchRoutingPreference: mocks.fetchRoutingPreference,
      setRoutingPreference: mocks.setRoutingPreference,
    }),
}));

describe("RoutingSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.routingPreference = "balanced";
  });

  it("挂载时拉取路由偏好并渲染三种策略", async () => {
    render(<RoutingSection chapter="贰" />);
    expect(mocks.fetchRoutingPreference).toHaveBeenCalled();
    expect(await screen.findByRole("button", { name: /极致质量/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /智能均衡/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /省钱模式/ })).toBeInTheDocument();
    // 当前策略选中标记
    expect(screen.getByRole("button", { name: /智能均衡/ })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
  });

  it("点击其他策略调用 setRoutingPreference", async () => {
    render(<RoutingSection chapter="贰" />);
    fireEvent.click(await screen.findByRole("button", { name: /极致质量/ }));
    await waitFor(() =>
      expect(mocks.setRoutingPreference).toHaveBeenCalledWith("quality")
    );
  });

  it("点击当前策略不重复保存", async () => {
    render(<RoutingSection chapter="贰" />);
    fireEvent.click(await screen.findByRole("button", { name: /智能均衡/ }));
    expect(mocks.setRoutingPreference).not.toHaveBeenCalled();
  });
});
