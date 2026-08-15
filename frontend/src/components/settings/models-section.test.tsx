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
  getTestResult: vi.fn<(id: number) => { success: boolean; message: string } | null>(
    () => null
  ),
  discover: vi.fn().mockResolvedValue({
    data: {
      models: [
        { id: "provider-model-a", name: "Provider Model A" },
        { id: "provider-model-b", name: "Provider Model B" },
      ],
    },
  }),
  providers: vi.fn().mockResolvedValue({
    data: [
      {
        id: "custom",
        label: "OpenAI 兼容服务",
        credential_kind: "api_key",
        credential_required: false,
      },
      {
        id: "anthropic",
        label: "Anthropic",
        default_base_url: "https://api.anthropic.com/v1",
        credential_kind: "api_key",
        credential_required: true,
      },
      {
        id: "ollama",
        label: "Ollama",
        default_base_url: "http://127.0.0.1:11434",
        credential_kind: "none",
        credential_required: false,
      },
    ],
  }),
  models: [] as AIModelConfig[],
  loading: false,
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    aiModelsApi: {
      ...actual.aiModelsApi,
      discover: mocks.discover,
      providers: mocks.providers,
    },
  };
});

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
  tier: "quality",
  max_tokens: 8000,
  temperature: 0.7,
  is_default: true,
  is_active: true,
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

  it("首个模型自动设为默认供 Pi Agent 使用", async () => {
    mocks.models = [];
    render(<ModelsSection chapter="贰" />);
    fireEvent.click(screen.getAllByRole("button", { name: "添加模型" })[0]);
    fireEvent.change(screen.getByPlaceholderText("例如：GPT-4o、Claude 3.5 Sonnet"), {
      target: { value: "首个模型" },
    });
    fireEvent.change(
      screen.getByPlaceholderText("例如：gpt-4o-mini、claude-3-5-sonnet-20241022"),
      { target: { value: "provider-model-a" } }
    );
    fireEvent.click(screen.getByRole("button", { name: "添加模型" }));

    await waitFor(() =>
      expect(mocks.addModel).toHaveBeenCalledWith(
        expect.objectContaining({
          model_id: "provider-model-a",
          is_default: true,
        })
      )
    );
  });

  it("通过 Base URL 获取模型列表并选择模型", async () => {
    render(<ModelsSection chapter="叁" />);
    fireEvent.click(screen.getByRole("button", { name: "添加模型" }));
    await waitFor(() => expect(mocks.providers).toHaveBeenCalled());
    fireEvent.change(
      screen.getByPlaceholderText("自定义 API 地址，例如：https://api.example.com/v1"),
      { target: { value: "https://models.example.com/v1" } }
    );
    fireEvent.change(screen.getByLabelText(/API Key/), {
      target: { value: "provider-secret" },
    });

    fireEvent.click(screen.getByRole("button", { name: "获取模型列表" }));

    await waitFor(() =>
      expect(mocks.discover).toHaveBeenCalledWith({
        provider: "custom",
        base_url: "https://models.example.com/v1",
        api_key: "provider-secret",
      })
    );
    expect(await screen.findByRole("option", { name: "Provider Model A" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("已发现模型"), {
      target: { value: "provider-model-b" },
    });
    expect(
      screen.getByPlaceholderText("例如：gpt-4o-mini、claude-3-5-sonnet-20241022")
    ).toHaveValue("provider-model-b");
  });

  it("不再显示 Vertex 供应商", async () => {
    render(<ModelsSection chapter="叁" />);
    fireEvent.click(screen.getByRole("button", { name: "添加模型" }));
    await waitFor(() => expect(mocks.providers).toHaveBeenCalled());

    expect(screen.queryByRole("option", { name: /Vertex/i })).not.toBeInTheDocument();
  });

  it("从后端供应商协议自动填写 URL 和凭据提示", async () => {
    render(<ModelsSection chapter="贰" />);
    fireEvent.click(screen.getByRole("button", { name: "添加模型" }));

    await waitFor(() => expect(mocks.providers).toHaveBeenCalled());
    fireEvent.change(screen.getByLabelText("服务提供商"), {
      target: { value: "anthropic" },
    });

    expect(screen.getByLabelText(/Base URL/)).toHaveValue("https://api.anthropic.com/v1");
    expect(screen.getByLabelText(/API Key/)).toHaveAttribute(
      "placeholder",
      "Anthropic API Key"
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
