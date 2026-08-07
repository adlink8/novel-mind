import { describe, it, expect, vi, beforeEach } from "vitest";

const { aiModelsApi, settingsApi } = vi.hoisted(() => ({
  aiModelsApi: {
    list: vi.fn(),
    create: vi.fn(),
    test: vi.fn(),
    setDefault: vi.fn(),
    delete: vi.fn(),
  },
  settingsApi: {
    getRouting: vi.fn(),
    putRouting: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => ({
  aiModelsApi,
  settingsApi,
}));

import { useAIConfigStore } from "./aiConfigStore";

const modelA = {
  id: 1,
  name: "GPT-4o",
  provider: "openai",
  model_id: "gpt-4o",
  tier: "quality",
  max_tokens: 8000,
  temperature: 0.7,
  is_default: true,
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const modelB = {
  id: 2,
  name: "Claude",
  provider: "anthropic",
  model_id: "claude-3",
  tier: "balanced",
  max_tokens: 8000,
  temperature: 0.7,
  is_default: false,
  is_active: true,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

describe("useAIConfigStore", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useAIConfigStore.setState({
      models: [],
      defaultModel: null,
      routingPreference: "balanced",
      loading: false,
      error: null,
      testResults: {},
    });
  });

  it("fetchModels 成功时识别默认模型", async () => {
    aiModelsApi.list.mockResolvedValue({ data: [modelA, modelB] });
    await useAIConfigStore.getState().fetchModels();
    const state = useAIConfigStore.getState();
    expect(state.models).toHaveLength(2);
    expect(state.defaultModel?.id).toBe(1);
    expect(state.loading).toBe(false);
    expect(state.error).toBeNull();
  });

  it("fetchModels 无默认模型时 defaultModel 为 null", async () => {
    aiModelsApi.list.mockResolvedValue({ data: [modelB] });
    await useAIConfigStore.getState().fetchModels();
    expect(useAIConfigStore.getState().defaultModel).toBeNull();
  });

  it("fetchModels 失败时记录错误", async () => {
    aiModelsApi.list.mockRejectedValue(new Error("network down"));
    await useAIConfigStore.getState().fetchModels();
    const state = useAIConfigStore.getState();
    expect(state.error).toMatch(/network down/);
    expect(state.loading).toBe(false);
    expect(state.models).toHaveLength(0);
  });

  it("fetchModels 非 Error 拒绝时使用默认错误文案", async () => {
    aiModelsApi.list.mockRejectedValue("boom");
    await useAIConfigStore.getState().fetchModels();
    expect(useAIConfigStore.getState().error).toBe("Failed to fetch models");
  });

  it("addModel 成功时追加模型", async () => {
    aiModelsApi.create.mockResolvedValue({ data: modelB });
    await useAIConfigStore.getState().addModel({
      name: "Claude",
      provider: "anthropic",
      model_id: "claude-3",
    });
    const state = useAIConfigStore.getState();
    expect(state.models).toEqual([modelB]);
    expect(state.loading).toBe(false);
  });

  it("addModel 失败时记录错误", async () => {
    aiModelsApi.create.mockRejectedValue(new Error("bad request"));
    await useAIConfigStore.getState().addModel({
      name: "x",
      provider: "openai",
      model_id: "x",
    });
    expect(useAIConfigStore.getState().error).toMatch(/bad request/);
  });

  it("removeModel 成功时移除模型", async () => {
    useAIConfigStore.setState({ models: [modelA, modelB], defaultModel: modelA });
    aiModelsApi.delete.mockResolvedValue({ data: {} });
    await useAIConfigStore.getState().removeModel(1);
    const state = useAIConfigStore.getState();
    expect(state.models).toEqual([modelB]);
    // 删除默认模型时清空 defaultModel
    expect(state.defaultModel).toBeNull();
  });

  it("removeModel 删除非默认模型时保留 defaultModel", async () => {
    useAIConfigStore.setState({ models: [modelA, modelB], defaultModel: modelA });
    aiModelsApi.delete.mockResolvedValue({ data: {} });
    await useAIConfigStore.getState().removeModel(2);
    const state = useAIConfigStore.getState();
    expect(state.models).toEqual([modelA]);
    expect(state.defaultModel?.id).toBe(1);
  });

  it("removeModel 失败时记录错误", async () => {
    aiModelsApi.delete.mockRejectedValue(new Error("gone"));
    await useAIConfigStore.getState().removeModel(1);
    expect(useAIConfigStore.getState().error).toMatch(/gone/);
  });

  it("setDefaultModel 成功时更新默认标记", async () => {
    useAIConfigStore.setState({ models: [modelA, modelB] });
    aiModelsApi.setDefault.mockResolvedValue({ data: {} });
    await useAIConfigStore.getState().setDefaultModel(2);
    const state = useAIConfigStore.getState();
    expect(state.models.map((m) => [m.id, m.is_default])).toEqual([
      [1, false],
      [2, true],
    ]);
    expect(state.defaultModel?.id).toBe(2);
  });

  it("setDefaultModel 失败时记录错误", async () => {
    aiModelsApi.setDefault.mockRejectedValue(new Error("nope"));
    await useAIConfigStore.getState().setDefaultModel(2);
    expect(useAIConfigStore.getState().error).toMatch(/nope/);
  });

  it("testConnection 成功时缓存结果并格式化延迟", async () => {
    aiModelsApi.test.mockResolvedValue({
      data: { success: true, model_name: "gpt-4o", latency_ms: 42 },
    });
    await useAIConfigStore.getState().testConnection(1);
    expect(useAIConfigStore.getState().testResults[1]).toEqual({
      success: true,
      message: "Connection successful (42ms)",
    });
  });

  it("testConnection 返回失败时缓存 error 字段", async () => {
    aiModelsApi.test.mockResolvedValue({
      data: { success: false, model_name: "gpt-4o", latency_ms: 0, error: "auth fail" },
    });
    await useAIConfigStore.getState().testConnection(1);
    expect(useAIConfigStore.getState().testResults[1]).toEqual({
      success: false,
      message: "auth fail",
    });
  });

  it("testConnection 抛错时缓存错误信息", async () => {
    aiModelsApi.test.mockRejectedValue(new Error("timeout"));
    await useAIConfigStore.getState().testConnection(1);
    expect(useAIConfigStore.getState().testResults[1]).toEqual({
      success: false,
      message: "timeout",
    });
  });

  it("testConnection 非 Error 拒绝使用默认文案", async () => {
    aiModelsApi.test.mockRejectedValue("x");
    await useAIConfigStore.getState().testConnection(1);
    expect(useAIConfigStore.getState().testResults[1].message).toBe(
      "Connection failed"
    );
  });

  it("fetchRoutingPreference 成功时更新偏好", async () => {
    settingsApi.getRouting.mockResolvedValue({ data: { preference: "quality" } });
    await useAIConfigStore.getState().fetchRoutingPreference();
    expect(useAIConfigStore.getState().routingPreference).toBe("quality");
  });

  it("fetchRoutingPreference 失败时保留当前偏好", async () => {
    settingsApi.getRouting.mockRejectedValue(new Error("down"));
    useAIConfigStore.setState({ routingPreference: "quality" });
    await useAIConfigStore.getState().fetchRoutingPreference();
    expect(useAIConfigStore.getState().routingPreference).toBe("quality");
    expect(useAIConfigStore.getState().error).toBeNull();
  });

  it("setRoutingPreference 成功时更新偏好并清空错误", async () => {
    useAIConfigStore.setState({ error: "old error" });
    settingsApi.putRouting.mockResolvedValue({ data: { preference: "budget" } });
    await useAIConfigStore.getState().setRoutingPreference("budget");
    const state = useAIConfigStore.getState();
    expect(state.routingPreference).toBe("budget");
    expect(state.error).toBeNull();
  });

  it("setRoutingPreference 失败时记录错误但保留偏好", async () => {
    settingsApi.putRouting.mockRejectedValue(new Error("save failed"));
    await useAIConfigStore.getState().setRoutingPreference("budget");
    const state = useAIConfigStore.getState();
    expect(state.error).toMatch(/save failed/);
    expect(state.routingPreference).toBe("balanced");
  });

  it("clearError 清空错误", () => {
    useAIConfigStore.setState({ error: "some error" });
    useAIConfigStore.getState().clearError();
    expect(useAIConfigStore.getState().error).toBeNull();
  });

  it("addModel 非 Error 拒绝使用默认文案", async () => {
    aiModelsApi.create.mockRejectedValue("boom");
    await useAIConfigStore.getState().addModel({
      name: "x",
      provider: "openai",
      model_id: "x",
    });
    expect(useAIConfigStore.getState().error).toBe("Failed to add model");
  });

  it("removeModel 非 Error 拒绝使用默认文案", async () => {
    aiModelsApi.delete.mockRejectedValue("gone");
    await useAIConfigStore.getState().removeModel(1);
    expect(useAIConfigStore.getState().error).toBe("Failed to remove model");
  });

  it("setDefaultModel 目标不在列表时 defaultModel 为 null", async () => {
    useAIConfigStore.setState({ models: [modelA, modelB] });
    aiModelsApi.setDefault.mockResolvedValue({ data: {} });
    await useAIConfigStore.getState().setDefaultModel(999);
    const state = useAIConfigStore.getState();
    expect(state.defaultModel).toBeNull();
    expect(state.models.every((m) => !m.is_default)).toBe(true);
  });

  it("setDefaultModel 非 Error 拒绝使用默认文案", async () => {
    aiModelsApi.setDefault.mockRejectedValue("nope");
    await useAIConfigStore.getState().setDefaultModel(2);
    expect(useAIConfigStore.getState().error).toBe("Failed to set default model");
  });

  it("testConnection 成功响应无 error 字段时使用默认失败文案", async () => {
    aiModelsApi.test.mockResolvedValue({
      data: { success: false, model_name: "gpt-4o", latency_ms: 0 },
    });
    await useAIConfigStore.getState().testConnection(1);
    expect(useAIConfigStore.getState().testResults[1]).toEqual({
      success: false,
      message: "Connection failed",
    });
  });

  it("setRoutingPreference 非 Error 拒绝使用默认文案", async () => {
    settingsApi.putRouting.mockRejectedValue("nope");
    await useAIConfigStore.getState().setRoutingPreference("budget");
    expect(useAIConfigStore.getState().error).toBe(
      "Failed to save routing preference"
    );
  });
});
