import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import SettingsPage from "./page";

vi.mock("@/components/settings/models-section", () => ({
  ModelsSection: () => <section>AI 模型</section>,
}));
vi.mock("@/components/settings/usage-section", () => ({
  UsageSection: () => <section>用量概览</section>,
}));
vi.mock("@/components/settings/agent-settings/agent-settings-section", () => ({
  AgentSettingsSection: () => <section>Agent 设置</section>,
}));
vi.mock("@/components/settings/models-binding/models-binding-section", () => ({
  ModelsBindingSection: () => <section>任务模型绑定</section>,
}));
vi.mock("@/components/settings/skills-tools/skills-tools-entry", () => ({
  SkillsToolsEntry: () => <section>Skills/Tools</section>,
}));

describe("SettingsPage", () => {
  it("只展示实际模型连接设置，不再展示智能路由策略", () => {
    render(<SettingsPage />);

    expect(screen.getByText("AI 模型")).toBeInTheDocument();
    expect(screen.getByText("Agent 设置")).toBeInTheDocument();
    expect(screen.getByText("任务模型绑定")).toBeInTheDocument();
    expect(screen.getByText("Skills/Tools")).toBeInTheDocument();
    expect(screen.queryByText("账户")).not.toBeInTheDocument();
    expect(screen.queryByText("退出登录")).not.toBeInTheDocument();
    expect(screen.queryByText("智能路由策略")).not.toBeInTheDocument();
  });
});
