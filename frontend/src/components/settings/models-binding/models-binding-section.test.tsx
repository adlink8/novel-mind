import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ModelsBindingSection } from "./models-binding-section";
import { aiModelsApi, settingsApi, type AgentSettingsPayload } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  aiModelsApi: {
    list: vi.fn(),
  },
  settingsApi: {
    getAgent: vi.fn(),
    putAgent: vi.fn(),
  },
}));

const initialSettings: AgentSettingsPayload = {
  auto_deep_analysis: false,
  memory_enabled: true,
  memory_retention_days: null,
  show_analysis_progress: true,
  notify_analysis_complete: true,
  auto_create_candidate_artifacts: false,
  task_model_bindings: {
    qa: 1,
    deep_analysis: null,
    continuation: null,
    illustration: null,
    rag_eval: null,
    embedding: null,
  },
};

describe("ModelsBindingSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(settingsApi.getAgent).mockResolvedValue({ data: initialSettings } as never);
    vi.mocked(aiModelsApi.list).mockResolvedValue({
      data: [
        { id: 1, name: "主模型", model_id: "gpt-4o" },
        { id: 2, name: "备用模型", model_id: "claude-3" },
      ],
    } as never);
    vi.mocked(settingsApi.putAgent).mockResolvedValue({ data: initialSettings } as never);
  });

  it("展示六类任务的模型绑定入口", () => {
    render(
      <ModelsBindingSection
        chapter="伍"
        models={[
          { id: 1, name: "主模型", model_id: "gpt-4o" },
          { id: 2, name: "嵌入模型", model_id: "text-embedding-3-small" },
        ]}
        bindings={{
          qa: 1,
          deep_analysis: null,
          continuation: null,
          illustration: null,
          rag_eval: null,
          embedding: 2,
        }}
        onChange={() => undefined}
      />
    );

    for (const label of [
      "问答",
      "深度分析",
      "续写",
      "插图",
      "RAG 评估",
      "嵌入",
    ]) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
    expect(screen.getByLabelText("问答")).toHaveValue("1");
    expect(screen.getByLabelText("嵌入")).toHaveValue("2");
  });

  it("保存前读取最新设置并保留其他区块刚写入的 Agent 偏好", async () => {
    const latestSettings: AgentSettingsPayload = {
      ...initialSettings,
      memory_enabled: false,
      show_analysis_progress: false,
      task_model_bindings: {
        ...initialSettings.task_model_bindings,
        qa: 2,
      },
    };
    vi.mocked(settingsApi.getAgent)
      .mockResolvedValueOnce({ data: initialSettings } as never)
      .mockResolvedValueOnce({ data: latestSettings } as never);

    render(<ModelsBindingSection chapter="伍" />);
    await waitFor(() => expect(settingsApi.getAgent).toHaveBeenCalledTimes(1));

    fireEvent.change(screen.getByLabelText("问答"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "保存模型绑定" }));

    await waitFor(() =>
      expect(settingsApi.putAgent).toHaveBeenCalledWith({
        ...latestSettings,
        task_model_bindings: {
          ...latestSettings.task_model_bindings,
          qa: 2,
        },
      })
    );
  });
});
