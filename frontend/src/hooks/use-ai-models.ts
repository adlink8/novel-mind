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
 * - routingDescriptions     - 路由偏好的中文描述映射
 */

"use client";

import { useEffect } from "react";
import { useAIConfigStore } from "@/stores/aiConfigStore";
import type { AIModelConfig } from "@/lib/api";

export function useAIModels() {
  const {
    models, defaultModel, routingPreference, loading, error, testResults,
    fetchModels, addModel, removeModel, setDefaultModel, testConnection,
    setRoutingPreference, clearError,
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

  /** 路由偏好的中文描述（用于 UI 展示） */
  const routingDescriptions: Record<string, string> = {
    quality: "优先使用最强模型，适合深度分析和复杂创作",
    balanced: "智能分配任务到合适的模型，兼顾质量和成本",
    budget: "优先使用轻量模型，适合日常简单任务",
  };

  return {
    models, defaultModel, routingPreference, loading, error, testResults,
    fetchModels, addModel, removeModel, setDefaultModel, testConnection,
    setRoutingPreference, clearError,
    getModelById, getModelsByProvider, getTestResult, routingDescriptions,
  };
}
