"use client";

import { useEffect } from "react";
import { useAIConfigStore } from "@/stores/aiConfigStore";
import type { AIModelConfig } from "@/lib/api";

export function useAIModels() {
  const {
    models,
    defaultModel,
    routingPreference,
    loading,
    error,
    testResults,
    fetchModels,
    addModel,
    removeModel,
    setDefaultModel,
    testConnection,
    setRoutingPreference,
    clearError,
  } = useAIConfigStore();

  // Auto-fetch on mount
  useEffect(() => {
    fetchModels();
  }, [fetchModels]);

  const getModelById = (id: string): AIModelConfig | undefined => {
    return models.find((m) => m.id === id);
  };

  const getModelsByProvider = (
    provider: AIModelConfig["provider"]
  ): AIModelConfig[] => {
    return models.filter((m) => m.provider === provider);
  };

  const getTestResult = (id: string) => {
    return testResults[id] || null;
  };

  const routingDescriptions: Record<string, string> = {
    quality: "\u4F18\u5148\u4F7F\u7528\u6700\u5F3A\u6A21\u578B\uFF0C\u9002\u5408\u6DF1\u5EA6\u5206\u6790\u548C\u590D\u6742\u521B\u4F5C",
    balanced: "\u667A\u80FD\u5206\u914D\u4EFB\u52A1\u5230\u5408\u9002\u7684\u6A21\u578B\uFF0C\u517C\u987E\u8D28\u91CF\u548C\u6210\u672C",
    budget: "\u4F18\u5148\u4F7F\u7528\u8F7B\u91CF\u6A21\u578B\uFF0C\u9002\u5408\u65E5\u5E38\u7B80\u5355\u4EFB\u52A1",
  };

  return {
    models,
    defaultModel,
    routingPreference,
    loading,
    error,
    testResults,
    fetchModels,
    addModel,
    removeModel,
    setDefaultModel,
    testConnection,
    setRoutingPreference,
    clearError,
    getModelById,
    getModelsByProvider,
    getTestResult,
    routingDescriptions,
  };
}
