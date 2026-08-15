import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { UsageSection } from "./usage-section";

const mocks = vi.hoisted(() => ({
  summary: vi.fn(),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    usageApi: {
      summary: mocks.summary,
    },
  };
});

describe("UsageSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.summary.mockResolvedValue({
      data: {
        today_cost_usd: 1.25,
        week_cost_usd: 8.5,
        month_cost_usd: 30,
        total_tokens: 1234567,
      },
    });
  });

  it("加载成功展示格式化金额与 Token 数", async () => {
    render(<UsageSection chapter="肆" />);
    expect(await screen.findByText("$1.25")).toBeInTheDocument();
    expect(screen.getByText("$8.50")).toBeInTheDocument();
    expect(screen.getByText("$30.00")).toBeInTheDocument();
    expect(screen.getByText("1,234,567")).toBeInTheDocument();
    expect(screen.getByText("今日花费")).toBeInTheDocument();
    expect(screen.getByText("总 Token 数")).toBeInTheDocument();
  });

  it("加载失败展示暂无数据", async () => {
    mocks.summary.mockRejectedValue(new Error("boom"));
    render(<UsageSection chapter="肆" />);
    expect((await screen.findAllByText("暂无数据")).length).toBeGreaterThan(0);
  });

  it("加载中展示省略号", () => {
    mocks.summary.mockReturnValue(new Promise(() => {}));
    render(<UsageSection chapter="肆" />);
    expect(screen.getAllByText("…").length).toBeGreaterThan(0);
  });
});
