import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import TimelinePrototypePage from "./page";

describe("TimelinePrototypePage", () => {
  it("渲染全书概览与剧情阶段聚合", () => {
    render(<TimelinePrototypePage />);
    expect(screen.getByRole("heading", { name: "时间线" })).toBeInTheDocument();
    expect(screen.getByText("开端")).toBeInTheDocument();
    expect(screen.getByText("终局")).toBeInTheDocument();
    expect(screen.getByText(/每块代表一个剧情阶段/)).toBeInTheDocument();
  });

  it("点击剧情阶段进入下钻详情", () => {
    render(<TimelinePrototypePage />);
    fireEvent.click(screen.getByRole("button", { name: /王国篇/ }));
    expect(screen.getByRole("heading", { name: /第 7–12 章/ })).toBeInTheDocument();
    expect(screen.getByText("利姆露救下三人")).toBeInTheDocument();
    expect(screen.getByText("命名仪式")).toBeInTheDocument();
  });

  it("展开查看按钮进入下钻并可返回", () => {
    render(<TimelinePrototypePage />);
    fireEvent.click(screen.getByRole("button", { name: "展开查看" }));
    expect(screen.getByRole("heading", { name: /第 7–12 章/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "返回全书概览" }));
    expect(screen.queryByRole("heading", { name: /第 7–12 章/ })).not.toBeInTheDocument();
  });
});
