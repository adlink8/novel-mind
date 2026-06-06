import { create } from "zustand";
import { aiModelsApi, type AIModelConfig } from "@/lib/api";

type RoutingPreference = "quality" | "balanced" | "budget";

interface AIConfigState {
  models: AIModelConfig[];
  defaultModel: AIModelConfig | null;
  routingPreference: RoutingPreference;
  loading: boolean;
  error: string | null;
  testResults: Record<string, { success: boolean; message: string }>;

  fetchModels: () => Promise<void>;
  addModel: (data: Partial<AIModelConfig>) => Promise<void>;
  removeModel: (id: string) => Promise<void>;
  setDefaultModel: (id: string) => Promise<void>;
  testConnection: (id: string) => Promise<void>;
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

  fetchModels: async () => {
    set({ loading: true, error: null });
    try {
      const res = await aiModelsApi.list();
      const models = res.data;
      const defaultModel = models.find((m) => m.is_default) || null;
      set({ models, defaultModel, loading: false });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to fetch models";
      set({ error: message, loading: false });
    }
  },

  addModel: async (data: Partial<AIModelConfig>) => {
    set({ loading: true, error: null });
    try {
      const res = await aiModelsApi.create(data);
      set((state) => ({
        models: [...state.models, res.data],
        loading: false,
      }));
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to add model";
      set({ error: message, loading: false });
    }
  },

  removeModel: async (id: string) => {
    set({ loading: true, error: null });
    try {
      await aiModelsApi.delete(id);
      set((state) => ({
        models: state.models.filter((m) => m.id !== id),
        defaultModel: state.defaultModel?.id === id ? null : state.defaultModel,
        loading: false,
      }));
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to remove model";
      set({ error: message, loading: false });
    }
  },

  setDefaultModel: async (id: string) => {
    set({ loading: true, error: null });
    try {
      await aiModelsApi.setDefault(id);
      set((state) => ({
        models: state.models.map((m) => ({ ...m, is_default: m.id === id })),
        defaultModel: state.models.find((m) => m.id === id) || null,
        loading: false,
      }));
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to set default model";
      set({ error: message, loading: false });
    }
  },

  testConnection: async (id: string) => {
    set((state) => ({
      testResults: {
        ...state.testResults,
        [id]: { success: false, message: "Testing..." },
      },
    }));
    try {
      await aiModelsApi.test(id);
      set((state) => ({
        testResults: {
          ...state.testResults,
          [id]: { success: true, message: "Connection successful" },
        },
      }));
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Connection failed";
      set((state) => ({
        testResults: {
          ...state.testResults,
          [id]: { success: false, message },
        },
      }));
    }
  },

  setRoutingPreference: (pref: RoutingPreference) =>
    set({ routingPreference: pref }),

  clearError: () => set({ error: null }),
}));
