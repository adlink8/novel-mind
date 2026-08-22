import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AgentSettingsSection } from "./agent-settings-section";
import {
  settingsApi,
  type AgentSettingsPayload,
  type UserPreferenceMemory,
} from "@/lib/api/settings";

vi.mock("@/lib/api/settings", () => ({
  settingsApi: {
    getAgent: vi.fn(),
    putAgent: vi.fn(),
    listMemoryPreferences: vi.fn(),
    deleteMemoryPreference: vi.fn(),
    clearMemoryPreferences: vi.fn(),
  },
}));

const defaultSettings: AgentSettingsPayload = {
  auto_deep_analysis: false,
  memory_enabled: true,
  memory_retention_days: null,
  show_analysis_progress: true,
  notify_analysis_complete: true,
  auto_create_candidate_artifacts: false,
  task_model_bindings: {
    qa: null,
    deep_analysis: null,
    continuation: null,
    illustration: null,
    rag_eval: null,
    embedding: null,
  },
};

const memories: UserPreferenceMemory[] = [
  {
    id: 7,
    source_message_id: 31,
    kind: "response_style",
    value: "concise",
    confidence: 1,
    explicit: true,
    created_at: "2026-08-12T08:00:00Z",
    expires_at: "2026-09-11T08:00:00Z",
  },
];

describe("AgentSettingsSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(settingsApi.getAgent).mockResolvedValue({ data: defaultSettings } as never);
    vi.mocked(settingsApi.putAgent).mockResolvedValue({ data: defaultSettings } as never);
    vi.mocked(settingsApi.listMemoryPreferences).mockResolvedValue({
      data: { items: memories, total: memories.length },
    } as never);
    vi.mocked(settingsApi.deleteMemoryPreference).mockResolvedValue(undefined as never);
    vi.mocked(settingsApi.clearMemoryPreferences).mockResolvedValue(undefined as never);
  });

  it("展示六项 Agent 偏好并从服务端加载初始值", async () => {
    render(<AgentSettingsSection chapter="肆" />);

    expect(await screen.findByLabelText("自动执行深度分析")).not.toBeChecked();
    expect(screen.getByLabelText("启用记忆")).toBeChecked();
    expect(screen.getByLabelText("展示分析进度")).toBeChecked();
    expect(screen.getByLabelText("分析完成时通知")).toBeChecked();
    expect(screen.getByLabelText("自动创建候选产物")).not.toBeChecked();
    expect(screen.getByLabelText("记忆保留天数")).toHaveValue(null);
  });

  it("修改偏好后保存完整设置", async () => {
    render(<AgentSettingsSection chapter="肆" />);
    await screen.findByLabelText("启用记忆");

    fireEvent.click(screen.getByLabelText("自动执行深度分析"));
    fireEvent.change(screen.getByLabelText("记忆保留天数"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "保存 Agent 设置" }));

    await waitFor(() =>
      expect(settingsApi.putAgent).toHaveBeenCalledWith({
        ...defaultSettings,
        auto_deep_analysis: true,
        memory_retention_days: 30,
      })
    );
  });

  it("保存前读取最新设置并保留其他区块刚写入的模型绑定", async () => {
    const latestSettings: AgentSettingsPayload = {
      ...defaultSettings,
      task_model_bindings: {
        ...defaultSettings.task_model_bindings,
        qa: 42,
      },
    };
    vi.mocked(settingsApi.getAgent)
      .mockResolvedValueOnce({ data: defaultSettings } as never)
      .mockResolvedValueOnce({ data: latestSettings } as never);

    render(<AgentSettingsSection chapter="肆" />);
    await screen.findByLabelText("启用记忆");

    fireEvent.click(screen.getByLabelText("自动执行深度分析"));
    fireEvent.click(screen.getByRole("button", { name: "保存 Agent 设置" }));

    await waitFor(() =>
      expect(settingsApi.putAgent).toHaveBeenCalledWith({
        ...latestSettings,
        auto_deep_analysis: true,
      })
    );
  });

  it("启用记忆时可查看并管理服务端记忆", async () => {
    render(<AgentSettingsSection chapter="肆" />);
    await screen.findByLabelText("启用记忆");

    fireEvent.click(screen.getByRole("button", { name: "查看记忆" }));

    expect(await screen.findByText("response_style")).toBeInTheDocument();
    expect(screen.getByText("concise")).toBeInTheDocument();
    expect(screen.getByText("消息 #31")).toBeInTheDocument();
    expect(screen.getByText("2026/09/11")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "删除记忆" }));
    await waitFor(() => expect(settingsApi.deleteMemoryPreference).toHaveBeenCalledWith(7));

    fireEvent.click(screen.getByRole("button", { name: "清空记忆" }));
    await waitFor(() => expect(settingsApi.clearMemoryPreferences).toHaveBeenCalledTimes(1));
  });

  it("关闭记忆时说明停止写入和召回但保留已有数据，并隐藏查看入口", async () => {
    vi.mocked(settingsApi.getAgent).mockResolvedValue({
      data: { ...defaultSettings, memory_enabled: false },
    } as never);

    render(<AgentSettingsSection chapter="肆" />);

    expect(await screen.findByText("关闭记忆后将停止写入和召回，但会保留已有数据。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "查看记忆" })).not.toBeInTheDocument();
  });
});
