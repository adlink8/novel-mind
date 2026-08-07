import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ModelsSection } from "./models-section";
import type { AIModelConfig } from "@/lib/api";

const mocks = vi.hoisted(() => ({
  fetchModels: vi.fn().mockResolvedValue(undefined),
  addModel: vi.fn().mockResolvedValue(undefined),
  removeModel: vi.fn().mockResolvedValue(undefined),
  setDefaultModel: vi.fn().mockResolvedValue(undefined),
  testConnection: vi.fn().mockResolvedValue(undefined),
  getTestResult: vi.fn(() => null),
  models: [] as AIModelConfig[],
  loading: false,
}));

vi.mock("@/hooks/use-ai-models", () => ({
  useAIModels: () => ({
    models: mocks.models,
    loading: mocks.loading,
    fetchModels: mocks.fetchModels,
    addModel: mocks.addModel,
    removeModel: mocks.removeModel,
    setDefaultModel: mocks.setDefaultModel,
    testConnection: mocks.testConnection,
    getTestResult: mocks.getTestResult,
  }),
}));

const model: AIModelConfig = {
  id: 1,
  name: "主模型",
  model_id: "gpt-4o",
  provider: "openai",
  base_url: null,
  api_key: null,
  is_default: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const model2: AIModelConfig = {
  ...model,
  id: 2,
  name: "备用模型",
  model_id: "claude-3",
  provider: "anthropic",
  is_default: false,
};

describe("ModelsSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.models = [model, model2];
    mocks.loading = false;
    mocks.getTestResult.mockReturnValue(null);
  });

  it("加载中展示 loading", () => {
    mocks.models = [];
    mocks.loading = true;
    render(<ModelsSection chapter="叁" />);
    expect(screen.getByText("加载中...")).toBeInTheDocument();
  });

  it("空列表展示 EmptyState", () => {
    mocks.models = [];
    render(<ModelsSection chapter="叁" />);
    expect(screen.getByText("还没有配置 AI 模型")).toBeInTheDocument();
  });

  it("渲染模型列表与默认标记", () => {
    render(<ModelsSection chapter="叁" />);
    expect(screen.getByText("主模型")).toBeInTheDocument();
    expect(screen.getByText("备用模型")).toBeInTheDocument();
    expect(screen.getByText("默认")).toBeInTheDocument();
    expect(screen.getByText("gpt-4o")).toBeInTheDocument();
    expect(screen.getByText("claude-3")).toBeInTheDocument();
  });

  it("打开添加对话框并提交新模型", async () => {
    render(<ModelsSection chapter="叁" />);
    fireEvent.click(screen.getByRole("button", { name: "添加模型" }));
    expect(await screen.findByText("配置新的 AI 模型用于小说分析和创作")).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("例如：GPT-4o、Claude 3.5 Sonnet"), {
      target: { value: "新模型" },
    });
    fireEvent.change(screen.getByPlaceholderText("例如：gpt-4o-mini、claude-3-5-sonnet-20241022"), {
      target: { value: "gpt-4o-mini" },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加模型" }));
    await waitFor(() =>
      expect(mocks.addModel).toHaveBeenCalledWith(
        expect.objectContaining({ name: "新模型", model_id: "gpt-4o-mini" })
      )
    );
  });

  it("测试连接并展示结果", async () => {
    mocks.models = [model];
    mocks.getTestResult.mockReturnValue({ success: true, message: "连接成功" });
    render(<ModelsSection chapter="叁" />);
    fireEvent.click(screen.getByRole("button", { name: "测试" }));
    await waitFor(() => expect(mocks.testConnection).toHaveBeenCalledWith(1));
    expect(screen.getByText("连接成功")).toBeInTheDocument();
  });

  it("测试结果失败态", () => {
    mocks.models = [model];
    mocks.getTestResult.mockReturnValue({ success: false, message: "连接失败" });
    render(<ModelsSection chapter="叁" />);
    expect(screen.getByText("连接失败")).toBeInTheDocument();
  });

  it("非默认模型可设为默认", async () => {
    render(<ModelsSection chapter="叁" />);
    fireEvent.click(screen.getByRole("button", { name: "设为默认" }));
    await waitFor(() => expect(mocks.setDefaultModel).toHaveBeenCalledWith(2));
  });

  it("删除模型", async () => {
    render(<ModelsSection chapter="叁" />);
    fireEvent.click(screen.getAllByRole("button", { name: "删除" })[1]);
    await waitFor(() => expect(mocks.removeModel).toHaveBeenCalledWith(2));
  });
});
