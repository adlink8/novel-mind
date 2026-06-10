/**
 * AI 设置页面 - app/settings/page.tsx
 * ======================================
 * 管理 AI 模型配置和智能路由策略的设置页面。
 *
 * 主要职责：
 * 1. 路由策略选择 - 用户可选择"极致质量"/"智能均衡"/"省钱模式"
 * 2. AI 模型管理 - 添加、删除、设置默认模型、测试连接
 * 3. 用量概览 - 展示 Token 消耗和费用统计（目前为占位数据）
 * 4. 添加模型对话框 - 表单收集模型名称、ID、提供商、Base URL、API Key
 *
 * 数据流：
 * - 通过 useAIModels Hook 从 Zustand aiConfigStore 获取模型列表和操作方法
 * - 路由策略通过 store 的 setRoutingPreference 持久化
 * - 模型 CRUD 操作通过 store 调用后端 API
 *
 * 支持的 AI 提供商：
 * - OpenAI (GPT 系列)
 * - Anthropic (Claude 系列)
 * - Ollama (本地部署)
 * - 自定义（第三方兼容 API）
 */

/**
 * AI 设置页 (Settings Page)
 *
 * 功能:
 * 1. 路由策略选择（质量优先/智能均衡/节省模式）
 * 2. AI 模型管理（添加/删除/测试连接/设为默认）
 * 3. 用量概览（今日/本周/本月费用，总 Token 数 — 当前为占位数据）
 *
 * 数据流:
 * useAIModels() → Zustand Store → aiModelsApi → GET/POST/DELETE /api/models
 */

"use client";

import React, { useState, useCallback } from "react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogClose,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/empty-state";
import { useAIModels } from "@/hooks/use-ai-models";
import type { AIModelConfig } from "@/lib/api";

/** 路由策略偏好类型 */
type RoutingPreference = "quality" | "balanced" | "budget";

/**
 * 路由策略选项配置
 * 
 * 三种策略：
 * - quality（极致质量）：优先使用最强模型，适合深度分析和复杂创作
 * - balanced（智能均衡）：智能分配任务到合适的模型，兼顾质量和成本
 * - budget（省钱模式）：优先使用轻量模型，适合日常简单任务
 */
const routingOptions: {
  value: RoutingPreference;
  label: string;
  icon: string;
  description: string;
}[] = [
  {
    value: "quality",
    label: "极致质量",
    icon: "⭐",
    description: "优先使用最强模型，适合深度分析和复杂创作",
  },
  {
    value: "balanced",
    label: "智能均衡",
    icon: "⚖️",
    description: "智能分配任务到合适的模型，兼顾质量和成本",
  },
  {
    value: "budget",
    label: "省钱模式",
    icon: "💰",
    description: "优先使用轻量模型，适合日常简单任务",
  },
];

/** AI 服务提供商选项列表 */
const providerOptions: { value: AIModelConfig["provider"]; label: string }[] = [
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "ollama", label: "Ollama" },
  { value: "custom", label: "自定义" },
];

/** 提供商显示名称映射 */
const providerLabels: Record<AIModelConfig["provider"], string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  ollama: "Ollama",
  custom: "自定义",
};

/** 提供商 Emoji 图标映射 */
const providerIcons: Record<AIModelConfig["provider"], string> = {
  openai: "🤖",
  anthropic: "🧠",
  ollama: "🦙",
  custom: "🔧",
};

// ============================================================
// 占位费用数据 - 后续应接入真实的用量统计 API
// ============================================================
const costSummary = {
  today: "¥0.00",
  thisWeek: "¥0.00",
  thisMonth: "¥0.00",
  totalTokens: "0",
};

/**
 * AI 设置页面组件
 * 
 * 状态管理：
 * - addDialogOpen: 添加模型对话框的开关状态
 * - formData: 添加模型表单数据（name, model_id, provider, base_url, api_key）
 * - addLoading: 添加操作的加载状态
 */
export default function SettingsPage() {
  // 从 Hook 获取 AI 模型相关状态和操作方法
  const {
    models,
    defaultModel,
    routingPreference,
    loading,
    fetchModels,
    addModel,
    removeModel,
    setDefaultModel,
    testConnection,
    setRoutingPreference,
    getTestResult,
  } = useAIModels();

  // 添加模型对话框状态
  const [addDialogOpen, setAddDialogOpen] = useState(false);

  // 添加模型表单数据
  const [formData, setFormData] = useState({
    name: "",
    model_id: "",
    provider: "openai" as AIModelConfig["provider"],
    base_url: "",
    api_key: "",
  });
  const [addLoading, setAddLoading] = useState(false);

  /**
   * 处理添加模型
   * 流程：验证表单 -> 调用 store 添加 -> 重置表单 -> 关闭对话框 -> 刷新列表
   */
  const handleAddModel = useCallback(async () => {
    if (!formData.name.trim() || !formData.model_id.trim()) return;
    setAddLoading(true);
    try {
      await addModel({
        name: formData.name,
        model_id: formData.model_id,
        provider: formData.provider,
        base_url: formData.base_url || undefined,
        api_key: formData.api_key || undefined,
      });
      // 重置表单数据
      setFormData({ name: "", model_id: "", provider: "openai", base_url: "", api_key: "" });
      setAddDialogOpen(false);
      fetchModels(); // 刷新模型列表
    } catch {
      // 错误已在 store 中处理
    } finally {
      setAddLoading(false);
    }
  }, [formData, addModel, fetchModels]);

  /** 测试模型连接 */
  const handleTestConnection = useCallback(
    async (id: number) => {
      await testConnection(id);
    },
    [testConnection]
  );

  /** 设置默认模型 */
  const handleSetDefault = useCallback(
    async (id: number) => {
      await setDefaultModel(id);
    },
    [setDefaultModel]
  );

  /** 删除模型 */
  const handleRemoveModel = useCallback(
    async (id: number) => {
      await removeModel(id);
    },
    [removeModel]
  );

  return (
    <div className="p-6 md:p-8 max-w-4xl mx-auto">
      {/* ========== 页面头部 ========== */}
      <div className="mb-8">
        <h2 className="text-2xl font-bold">{"AI 设置"}</h2>
        <p className="text-muted-foreground mt-1">
          {"配置 AI 模型和智能路由策略"}
        </p>
      </div>

      {/* ========== 路由策略选择区 ========== */}
      {/* 三张卡片，用户点击选择当前使用的路由策略 */}
      <section className="mb-10">
        <h3 className="text-lg font-semibold mb-4">{"智能路由策略"}</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {routingOptions.map((option) => (
            <Card
              key={option.value}
              className={`cursor-pointer transition-all ${
                // 选中状态高亮：显示 ring 边框和阴影
                routingPreference === option.value
                  ? "ring-2 ring-novel-500 shadow-md"
                  : "hover:ring-1 hover:ring-novel-300"
              }`}
              onClick={() => setRoutingPreference(option.value)}
            >
              <CardContent>
                <div className="flex items-center gap-3 mb-2">
                  <span className="text-2xl">{option.icon}</span>
                  <h4 className="font-semibold">{option.label}</h4>
                  {/* 当前选中标记 */}
                  {routingPreference === option.value && (
                    <Badge className="ml-auto bg-novel-500 text-white text-xs">
                      {"当前"}
                    </Badge>
                  )}
                </div>
                <p className="text-xs text-muted-foreground">
                  {option.description}
                </p>
              </CardContent>
            </Card>
          ))}
        </div>
      </section>

      {/* ========== AI 模型管理区 ========== */}
      <section className="mb-10">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold">{"AI 模型"}</h3>
          <Button onClick={() => setAddDialogOpen(true)}>
            + {"添加模型"}
          </Button>
        </div>

        {/* 加载中状态 */}
        {loading && models.length === 0 && (
          <div className="flex items-center justify-center py-12">
            <div className="text-center">
              <div className="text-4xl mb-4 animate-pulse">{"⏳"}</div>
              <p className="text-muted-foreground">{"加载中..."}</p>
            </div>
          </div>
        )}

        {/* 空状态：未配置任何模型 */}
        {!loading && models.length === 0 && (
          <EmptyState
            icon={"🤖"}
            title={"还没有配置 AI 模型"}
            description={
              "添加你的第一个 AI 模型，开始智能分析与创作"
            }
            actionLabel={"添加模型"}
            onAction={() => setAddDialogOpen(true)}
          />
        )}

        {/* 模型列表 */}
        {models.length > 0 && (
          <div className="space-y-3">
            {models.map((model) => {
              // 获取当前模型的连接测试结果
              const testResult = getTestResult(model.id);
              return (
                <Card key={model.id}>
                  <CardContent>
                    <div className="flex items-center justify-between">
                      {/* 左侧：模型信息（图标 + 名称 + 提供商 + 模型ID） */}
                      <div className="flex items-center gap-4">
                        <div className="w-12 h-12 rounded-xl bg-muted flex items-center justify-center text-2xl">
                          {providerIcons[model.provider]}
                        </div>
                        <div>
                          <div className="flex items-center gap-2">
                            <h4 className="font-semibold">
                              {model.name}
                            </h4>
                            {/* 默认模型标记 */}
                            {model.is_default && (
                              <Badge className="bg-novel-100 text-novel-800 text-xs">
                                {"默认"}
                              </Badge>
                            )}
                          </div>
                          <p className="text-xs text-muted-foreground mt-0.5">
                            {providerLabels[model.provider]}
                            <span className="ml-2">{model.model_id}</span>
                            {model.base_url && (
                              <span className="ml-2">
                                {model.base_url}
                              </span>
                            )}
                          </p>
                        </div>
                      </div>

                      {/* 右侧：操作按钮区 */}
                      <div className="flex items-center gap-2">
                        {/* 连接测试结果指示器 */}
                        {testResult && (
                          <span
                            className={`text-xs ${
                              testResult.success
                                ? "text-green-600"
                                : "text-red-600"
                            }`}
                          >
                            {testResult.success ? "✅" : "❌"}{" "}
                            {testResult.message}
                          </span>
                        )}

                        {/* 测试连接按钮 */}
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => handleTestConnection(model.id)}
                        >
                          {"测试"}
                        </Button>

                        {/* 设为默认按钮（非默认模型才显示） */}
                        {!model.is_default && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleSetDefault(model.id)}
                          >
                            {"设为默认"}
                          </Button>
                        )}

                        {/* 删除按钮 */}
                        <Button
                          variant="destructive"
                          size="sm"
                          onClick={() => handleRemoveModel(model.id)}
                        >
                          {"删除"}
                        </Button>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}
      </section>

      {/* ========== 用量概览区 ========== */}
      {/* 展示费用统计和 Token 消耗，目前使用占位数据 */}
      <section className="mb-10">
        <h3 className="text-lg font-semibold mb-4">{"用量概览"}</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Card>
            <CardContent>
              <p className="text-xs text-muted-foreground mb-1">
                {"今日花费"}
              </p>
              <p className="text-xl font-bold">{costSummary.today}</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent>
              <p className="text-xs text-muted-foreground mb-1">
                {"本周花费"}
              </p>
              <p className="text-xl font-bold">{costSummary.thisWeek}</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent>
              <p className="text-xs text-muted-foreground mb-1">
                {"本月花费"}
              </p>
              <p className="text-xl font-bold">{costSummary.thisMonth}</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent>
              <p className="text-xs text-muted-foreground mb-1">
                {"总 Token 数"}
              </p>
              <p className="text-xl font-bold">{costSummary.totalTokens}</p>
            </CardContent>
          </Card>
        </div>
      </section>

      {/* ========== 添加模型对话框 ========== */}
      {/* 收集新模型的配置信息：名称、模型ID、提供商、Base URL、API Key */}
      <Dialog open={addDialogOpen} onOpenChange={setAddDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{"添加 AI 模型"}</DialogTitle>
            <DialogDescription>
              {"配置新的 AI 模型用于小说分析和创作"}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {/* 模型名称输入 */}
            <div className="space-y-2">
              <label className="text-sm font-medium">{"模型名称"}</label>
              <Input
                placeholder={"例如：GPT-4o、Claude 3.5 Sonnet"}
                value={formData.name}
                onChange={(e) =>
                  setFormData((prev) => ({
                    ...prev,
                    name: e.target.value,
                  }))
                }
              />
            </div>

            {/* 模型 ID 输入 */}
            <div className="space-y-2">
              <label className="text-sm font-medium">{"模型 ID"}</label>
              <Input
                placeholder={"例如：gpt-4o-mini、claude-3-5-sonnet-20241022"}
                value={formData.model_id}
                onChange={(e) =>
                  setFormData((prev) => ({
                    ...prev,
                    model_id: e.target.value,
                  }))
                }
              />
            </div>

            {/* 服务提供商选择 */}
            <div className="space-y-2">
              <label className="text-sm font-medium">{"服务提供商"}</label>
              <select
                value={formData.provider}
                onChange={(e) =>
                  setFormData((prev) => ({
                    ...prev,
                    provider: e.target.value as AIModelConfig["provider"],
                  }))
                }
                className="w-full h-8 rounded-lg border border-input bg-background px-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring/50"
              >
                {providerOptions.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>

            {/* Base URL 输入（可选，用于自定义 API 地址） */}
            <div className="space-y-2">
              <label className="text-sm font-medium">
                {"Base URL"}{" "}
                <span className="text-muted-foreground font-normal">
                  ({"可选"})
                </span>
              </label>
              <Input
                placeholder={"自定义 API 地址，例如：https://api.example.com/v1"}
                value={formData.base_url}
                onChange={(e) =>
                  setFormData((prev) => ({
                    ...prev,
                    base_url: e.target.value,
                  }))
                }
              />
            </div>

            {/* API Key 输入（可选，密码类型） */}
            <div className="space-y-2">
              <label className="text-sm font-medium">
                {"API Key"}{" "}
                <span className="text-muted-foreground font-normal">
                  ({"可选"})
                </span>
              </label>
              <Input
                type="password"
                placeholder={"模型服务 API Key"}
                value={formData.api_key}
                onChange={(e) =>
                  setFormData((prev) => ({
                    ...prev,
                    api_key: e.target.value,
                  }))
                }
              />
            </div>

            {/* 操作按钮：取消 + 添加 */}
            <div className="flex justify-end gap-2 pt-2">
              <DialogClose>
                <Button variant="outline">{"取消"}</Button>
              </DialogClose>
              <Button
                onClick={handleAddModel}
                disabled={!formData.name.trim() || !formData.model_id.trim() || addLoading}
              >
                {addLoading ? "添加中..." : "添加模型"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
