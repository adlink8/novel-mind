/**
 * AI 模型管理 Hook
 *
 * 封装 useAIConfigStore + 便捷查询方法。
 * AI 设置页面通过 useAIModels() 获取模型列表、测试结果等。
 *
 * 自动行为:
 * - 组件挂载时自动 fetchModels()
 *
 * 额外工具方法:
 * - getModelById(id)        - 按 ID 查找模型
 * - getModelsByProvider(p)  - 按提供商筛选
 * - getTestResult(id)       - 获取测试结果
 */

"use client";

import { useEffect } from "react";
import { useAIConfigStore } from "@/stores/aiConfigStore";
import type { AIModelConfig } from "@/lib/api";

export function useAIModels() {
  const {
    models, defaultModel, loading, error, testResults,
    fetchModels, addModel, removeModel, setDefaultModel, testConnection,
    clearError,
  } = useAIConfigStore();

  // 组件挂载时自动加载模型列表
  useEffect(() => {
    fetchModels();
  }, [fetchModels]);

  /** 按 ID 查找模型 */
  const getModelById = (id: number): AIModelConfig | undefined => {
    return models.find((m) => m.id === id);
  };

  /** 按提供商筛选模型 */
  const getModelsByProvider = (provider: AIModelConfig["provider"]): AIModelConfig[] => {
    return models.filter((m) => m.provider === provider);
  };

  /** 获取指定模型的测试结果 */
  const getTestResult = (id: number) => {
    return testResults[id] || null;
  };

  return {
    models, defaultModel, loading, error, testResults,
    fetchModels, addModel, removeModel, setDefaultModel, testConnection,
    clearError, getModelById, getModelsByProvider, getTestResult,
  };
}
