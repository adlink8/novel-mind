"use client";

/**
 * 设置中心 · AI 模型管理 — 列表 / 添加 / 测试连接 / 设默认 / 删除。
 * 状态经 useAIModels()（Zustand aiConfigStore）共享。
 */

import React, { useCallback, useState } from "react";
import {
  Bot,
  CheckCircle2,
  Gauge,
  LoaderCircle,
  Plus,
  Sparkles,
  Wrench,
  XCircle,
} from "lucide-react";

import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useAIModels } from "@/hooks/use-ai-models";
import type { AIModelConfig } from "@/lib/api";
import { SettingsSection } from "./settings-section";

/** AI 服务提供商选项列表 */
const providerOptions: { value: string; label: string }[] = [
  { value: "vertex_google", label: "Google Cloud Vertex" },
  { value: "gemini", label: "Google AI Studio" },
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "ollama", label: "Ollama" },
  { value: "custom", label: "自定义" },
];

/** 提供商显示名称映射 */
const providerLabels: Record<string, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  ollama: "Ollama",
  custom: "自定义",
  vertex_google: "Google Cloud Vertex",
  vertex: "Google Cloud Vertex",
  vertex_ai: "Google Cloud Vertex",
  gemini: "Google AI Studio",
  google: "Google",
};

/** 提供商图标映射 */
const providerIcons: Record<string, React.ComponentType<{ className?: string }>> = {
  openai: Bot,
  anthropic: Sparkles,
  ollama: Gauge,
  custom: Wrench,
  vertex_google: Sparkles,
  vertex: Sparkles,
  vertex_ai: Sparkles,
  gemini: Sparkles,
  google: Sparkles,
};

function resolveProviderIcon(
  provider: string
): React.ComponentType<{ className?: string }> {
  return providerIcons[provider] ?? Bot;
}

function resolveProviderLabel(provider: string): string {
  return providerLabels[provider] ?? provider;
}

export function ModelsSection({ chapter }: { chapter: string }) {
  const {
    models,
    loading,
    fetchModels,
    addModel,
    removeModel,
    setDefaultModel,
    testConnection,
    getTestResult,
  } = useAIModels();

  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [formData, setFormData] = useState({
    name: "",
    model_id: "",
    provider: "vertex_google" as AIModelConfig["provider"],
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
      setFormData({ name: "", model_id: "", provider: "vertex_google", base_url: "", api_key: "" });
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
    <SettingsSection
      chapter={chapter}
      title="AI 模型"
      action={
        <Button className="rounded-full px-4" onClick={() => setAddDialogOpen(true)}>
          <Plus className="mr-1 size-4" />添加模型
        </Button>
      }
    >
      {/* 加载中状态 */}
      {loading && models.length === 0 && (
        <div className="flex items-center justify-center py-12">
          <div className="text-center">
            <LoaderCircle className="mx-auto mb-4 size-7 animate-spin text-primary" />
            <p className="text-muted-foreground">{"加载中..."}</p>
          </div>
        </div>
      )}

      {/* 空状态：未配置任何模型 */}
      {!loading && models.length === 0 && (
        <EmptyState
          icon={<Bot className="size-6" />}
          title={"还没有配置 AI 模型"}
          description={"添加你的第一个 AI 模型，开始智能分析与创作"}
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
              <Card key={model.id} className="paper-surface rounded-2xl">
                <CardContent>
                  <div className="flex items-center justify-between">
                    {/* 左侧：模型信息（图标 + 名称 + 提供商 + 模型ID） */}
                    <div className="flex items-center gap-4">
                      <div className="grid size-12 place-items-center rounded-2xl bg-secondary text-primary">
                        {React.createElement(resolveProviderIcon(model.provider), {
                          className: "size-5",
                        })}
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <h4 className="font-semibold">{model.name}</h4>
                          {/* 默认模型印章（朱砂白文） */}
                          {model.is_default && (
                            <span className="grid rotate-2 place-items-center rounded-[4px] border border-[#b03a2e]/50 bg-[#b03a2e]/90 px-1.5 py-0.5 font-serif text-[11px] font-semibold leading-none text-white">
                              默认
                            </span>
                          )}
                        </div>
                        <p className="mt-0.5 text-xs text-muted-foreground">
                          {resolveProviderLabel(model.provider)}
                          <span className="ml-2">{model.model_id}</span>
                          {model.base_url && (
                            <span className="ml-2">{model.base_url}</span>
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
                            testResult.success ? "text-green-600" : "text-red-600"
                          }`}
                        >
                          {testResult.success ? (
                            <CheckCircle2 className="mr-1 inline size-3.5" />
                          ) : (
                            <XCircle className="mr-1 inline size-3.5" />
                          )}{" "}
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
                  setFormData((prev) => ({ ...prev, name: e.target.value }))
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
                  setFormData((prev) => ({ ...prev, model_id: e.target.value }))
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
                className="h-8 w-full rounded-lg border border-input bg-background px-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring/50"
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
                <span className="text-muted-foreground font-normal">({"可选"})</span>
              </label>
              <Input
                placeholder={"自定义 API 地址，例如：https://api.example.com/v1"}
                value={formData.base_url}
                onChange={(e) =>
                  setFormData((prev) => ({ ...prev, base_url: e.target.value }))
                }
              />
            </div>

            {/* API Key 输入（可选，密码类型） */}
            <div className="space-y-2">
              <label className="text-sm font-medium">
                {"API Key"}{" "}
                <span className="text-muted-foreground font-normal">({"可选"})</span>
              </label>
              <Input
                type="password"
                placeholder={"模型服务 API Key"}
                value={formData.api_key}
                onChange={(e) =>
                  setFormData((prev) => ({ ...prev, api_key: e.target.value }))
                }
              />
            </div>

            {/* 操作按钮：取消 + 添加 */}
            <div className="flex justify-end gap-2 pt-2">
              <DialogClose render={<Button variant="outline" />}>
                {"取消"}
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
    </SettingsSection>
  );
}
