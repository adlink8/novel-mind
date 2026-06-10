/**
 * AI 配置状态管理 Store (Zustand)
 *
 * 管理 AI 模型列表、默认模型、路由偏好和测试结果。
 * AI 设置页面通过 useAIConfigStore() 共享状态。
 *
 * 状态:
 * - models: AIModelConfig[]     - 已配置的 AI 模型列表
 * - defaultModel: AIModelConfig | null - 默认模型
 * - routingPreference - 路由偏好（quality/balanced/budget）
 * - testResults - 连接测试结果缓存
 */

import { create } from "zustand";
import { aiModelsApi, type AIModelConfig, type AIModelConfigCreate } from "@/lib/api";

type RoutingPreference = "quality" | "balanced" | "budget";

interface AIConfigState {
  models: AIModelConfig[];
  defaultModel: AIModelConfig | null;
  routingPreference: RoutingPreference;
  loading: boolean;
  error: string | null;
  testResults: Record<string, { success: boolean; message: string }>;

  fetchModels: () => Promise<void>;
  addModel: (data: AIModelConfigCreate) => Promise<void>;
  removeModel: (id: number) => Promise<void>;
  setDefaultModel: (id: number) => Promise<void>;
  testConnection: (id: number) => Promise<void>;
  setRoutingPreference: (pref: RoutingPreference) => void;
  clearError: () => void;
}

export const useAIConfigStore = create<AIConfigState>((set, get) => ({
  models: [],
  defaultModel: null,
  routingPreference: "balanced",
  loading: false,
  error: null,
  testResults: {},

  /** 获取模型列表并自动识别默认模型 */
  fetchModels: async () => {
    set({ loading: true, error: null });
    try {
      const res = await aiModelsApi.list();
      const models = res.data;
      const defaultModel = models.find((m) => m.is_default) || null;
      set({ models, defaultModel, loading: false });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to fetch models";
      set({ error: message, loading: false });
    }
  },

  /** 添加新模型配置 */
  addModel: async (data: AIModelConfigCreate) => {
    set({ loading: true, error: null });
    try {
      const res = await aiModelsApi.create(data);
      set((state) => ({ models: [...state.models, res.data], loading: false }));
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to add model";
      set({ error: message, loading: false });
    }
  },

  /** 删除模型（软删除） */
  removeModel: async (id: number) => {
    set({ loading: true, error: null });
    try {
      await aiModelsApi.delete(id);
      set((state) => ({
        models: state.models.filter((m) => m.id !== id),
        defaultModel: state.defaultModel?.id === id ? null : state.defaultModel,
        loading: false,
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to remove model";
      set({ error: message, loading: false });
    }
  },

  /** 设为默认模型 */
  setDefaultModel: async (id: number) => {
    set({ loading: true, error: null });
    try {
      await aiModelsApi.setDefault(id);
      set((state) => ({
        models: state.models.map((m) => ({ ...m, is_default: m.id === id })),
        defaultModel: state.models.find((m) => m.id === id) || null,
        loading: false,
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to set default model";
      set({ error: message, loading: false });
    }
  },

  /** 测试模型连通性（结果缓存在 testResults 中） */
  testConnection: async (id: number) => {
    set((state) => ({ testResults: { ...state.testResults, [id]: { success: false, message: "Testing..." } } }));
    try {
      const res = await aiModelsApi.test(id);
      const result = res.data;
      set((state) => ({
        testResults: {
          ...state.testResults,
          [id]: {
            success: result.success,
            message: result.success ? `Connection successful (${result.latency_ms}ms)` : result.error || "Connection failed",
          },
        },
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : "Connection failed";
      set((state) => ({ testResults: { ...state.testResults, [id]: { success: false, message } } }));
    }
  },

  setRoutingPreference: (pref: RoutingPreference) => set({ routingPreference: pref }),
  clearError: () => set({ error: null }),
}));
