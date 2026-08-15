import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook } from "@testing-library/react";

const fetchModels = vi.fn().mockResolvedValue(undefined);
const addModel = vi.fn().mockResolvedValue(undefined);
const removeModel = vi.fn().mockResolvedValue(undefined);
const setDefaultModel = vi.fn().mockResolvedValue(undefined);
const testConnection = vi.fn().mockResolvedValue(undefined);
const fetchRoutingPreference = vi.fn().mockResolvedValue(undefined);
const setRoutingPreference = vi.fn().mockResolvedValue(undefined);
const clearError = vi.fn();

const models = [
  {
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
  },
  {
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
  },
];

vi.mock("@/stores/aiConfigStore", () => ({
  useAIConfigStore: () => ({
    models,
    defaultModel: models[0],
    routingPreference: "balanced",
    loading: false,
    error: null,
    testResults: {
      1: { success: true, message: "Connection successful (12ms)" },
    },
    fetchModels,
    addModel,
    removeModel,
    setDefaultModel,
    testConnection,
    fetchRoutingPreference,
    setRoutingPreference,
    clearError,
  }),
}));

import { useAIModels } from "./use-ai-models";

describe("useAIModels", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("自动获取模型列表", async () => {
    renderHook(() => useAIModels());
    expect(fetchModels).toHaveBeenCalled();
  });

  it("暴露 store 状态与动作", () => {
    const { result } = renderHook(() => useAIModels());
    expect(result.current.models).toHaveLength(2);
    expect(result.current.defaultModel?.id).toBe(1);
    expect(result.current.routingPreference).toBe("balanced");
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeNull();
    expect(result.current.addModel).toBe(addModel);
    expect(result.current.removeModel).toBe(removeModel);
    expect(result.current.setDefaultModel).toBe(setDefaultModel);
    expect(result.current.testConnection).toBe(testConnection);
    expect(result.current.fetchRoutingPreference).toBe(fetchRoutingPreference);
    expect(result.current.setRoutingPreference).toBe(setRoutingPreference);
    expect(result.current.clearError).toBe(clearError);
  });

  it("getModelById 按 ID 查找模型", () => {
    const { result } = renderHook(() => useAIModels());
    expect(result.current.getModelById(1)?.name).toBe("GPT-4o");
    expect(result.current.getModelById(999)).toBeUndefined();
  });

  it("getModelsByProvider 按提供商筛选", () => {
    const { result } = renderHook(() => useAIModels());
    const openai = result.current.getModelsByProvider("openai");
    expect(openai).toHaveLength(1);
    expect(openai[0].id).toBe(1);
    expect(result.current.getModelsByProvider("ollama")).toHaveLength(0);
  });

  it("getTestResult 返回缓存的测试结果，无则 null", () => {
    const { result } = renderHook(() => useAIModels());
    expect(result.current.getTestResult(1)).toEqual({
      success: true,
      message: "Connection successful (12ms)",
    });
    expect(result.current.getTestResult(2)).toBeNull();
  });

  it("routingDescriptions 提供三种偏好的中文描述", () => {
    const { result } = renderHook(() => useAIModels());
    expect(result.current.routingDescriptions.quality).toContain("最强模型");
    expect(result.current.routingDescriptions.balanced).toContain("兼顾质量");
    expect(result.current.routingDescriptions.budget).toContain("轻量模型");
  });
});
