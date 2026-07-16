/**
 * 设置中心 - app/settings/page.tsx
 * 账户（退出登录）+ AI 模型路由 / 模型管理 / 用量概览。
 */

"use client";

import React, { useState, useCallback, useEffect } from "react";
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
import { authApi, type AIModelConfig, type AuthUser } from "@/lib/api";
import {
  Bot,
  CheckCircle2,
  CircleDollarSign,
  Gauge,
  LoaderCircle,
  LogOut,
  Plus,
  Scale,
  Sparkles,
  UserRound,
  Wrench,
  XCircle,
} from "lucide-react";
import { PageContainer, PageHeader } from "@/components/page-header";
import { cn } from "@/lib/utils";

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
  icon: React.ComponentType<{ className?: string }>;
  description: string;
}[] = [
  {
    value: "quality",
    label: "极致质量",
    icon: Sparkles,
    description: "优先使用最强模型，适合深度分析和复杂创作",
  },
  {
    value: "balanced",
    label: "智能均衡",
    icon: Scale,
    description: "智能分配任务到合适的模型，兼顾质量和成本",
  },
  {
    value: "budget",
    label: "省钱模式",
    icon: CircleDollarSign,
    description: "优先使用轻量模型，适合日常简单任务",
  },
];

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

// ============================================================
// 占位费用数据 - 后续应接入真实的用量统计 API
// ============================================================
const costSummary = {
  today: "¥0.00",
  thisWeek: "¥0.00",
  thisMonth: "¥0.00",
  totalTokens: "0",
};

export default function SettingsPage() {
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

  const [user, setUser] = useState<AuthUser | null>(null);
  const [userLoading, setUserLoading] = useState(true);
  const [logoutLoading, setLogoutLoading] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);

  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [formData, setFormData] = useState({
    name: "",
    model_id: "",
    provider: "vertex_google" as AIModelConfig["provider"],
    base_url: "",
    api_key: "",
  });
  const [addLoading, setAddLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await authApi.me();
        if (!cancelled) setUser(res.data);
      } catch {
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setUserLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleLogout = useCallback(async () => {
    setLogoutLoading(true);
    setLogoutError(null);
    try {
      await authApi.logout();
      // Full navigation so AuthGate re-validates session.
      window.location.assign("/");
    } catch {
      setLogoutError("退出失败，请重试");
      setLogoutLoading(false);
    }
  }, []);

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
    <PageContainer className="space-y-9">
      <PageHeader
        eyebrow="Settings"
        title="设置中心"
        description="管理账户、模型路由与 AI 提供商。退出登录请在下方账户区操作。"
      />

      {/* ========== 账户 ========== */}
      <section aria-labelledby="settings-account-heading" className="motion-transition-content">
        <h3 id="settings-account-heading" className="mb-4 font-serif text-xl font-semibold">
          账户
        </h3>
        <Card className="paper-surface motion-transition-feedback">
          <CardContent className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-3">
              <span className="grid size-11 place-items-center rounded-2xl bg-secondary text-primary">
                <UserRound className="size-5" />
              </span>
              <div>
                <p className="text-sm font-medium">
                  {userLoading
                    ? "加载账户…"
                    : user
                      ? user.username
                      : "未登录"}
                </p>
                {user?.email ? (
                  <p className="text-xs text-muted-foreground">{user.email}</p>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    退出后需重新登录才能访问书架与分析
                  </p>
                )}
              </div>
            </div>
            <div className="flex flex-col items-stretch gap-2 sm:items-end">
              {logoutError ? (
                <p className="text-xs text-destructive" role="alert">
                  {logoutError}
                </p>
              ) : null}
              <Button
                type="button"
                variant="outline"
                className="rounded-full motion-transition-feedback"
                disabled={logoutLoading || userLoading || !user}
                onClick={() => void handleLogout()}
                data-testid="settings-logout"
              >
                {logoutLoading ? (
                  <LoaderCircle className="mr-2 size-4 animate-spin" />
                ) : (
                  <LogOut className="mr-2 size-4" />
                )}
                退出登录
              </Button>
            </div>
          </CardContent>
        </Card>
      </section>

      {/* ========== 路由策略 ========== */}
      <section aria-labelledby="settings-routing-heading">
        <h3 id="settings-routing-heading" className="mb-4 font-serif text-xl font-semibold">
          智能路由策略
        </h3>
        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          {routingOptions.map((option) => {
            const OptionIcon = option.icon;
            return (
            <Card
              key={option.value}
              className={cn(
                "cursor-pointer paper-surface motion-transition-feedback",
                routingPreference === option.value
                  ? "border-primary/40 bg-primary/[0.06] ring-1 ring-primary/30"
                  : "hover:-translate-y-0.5 hover:border-primary/20 hover:shadow-lg"
              )}
              onClick={() => setRoutingPreference(option.value)}
            >
              <CardContent>
                <div className="flex items-center gap-3 mb-2">
                  <span className="grid size-10 place-items-center rounded-xl bg-secondary text-primary"><OptionIcon className="size-4.5" /></span>
                  <h4 className="font-semibold">{option.label}</h4>
                  {/* 当前选中标记 */}
                  {routingPreference === option.value && (
                    <Badge className="ml-auto bg-primary text-primary-foreground text-xs">
                      {"当前"}
                    </Badge>
                  )}
                </div>
                <p className="text-xs text-muted-foreground">
                  {option.description}
                </p>
              </CardContent>
            </Card>
          )})}
        </div>
      </section>

      {/* ========== AI 模型管理区 ========== */}
      <section>
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-serif text-xl font-semibold">AI 模型</h3>
          <Button className="rounded-full px-4" onClick={() => setAddDialogOpen(true)}>
            <Plus className="mr-1 size-4" />添加模型
          </Button>
        </div>

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
                <Card key={model.id} className="paper-surface rounded-2xl">
                  <CardContent>
                    <div className="flex items-center justify-between">
                      {/* 左侧：模型信息（图标 + 名称 + 提供商 + 模型ID） */}
                      <div className="flex items-center gap-4">
                        <div className="grid size-12 place-items-center rounded-2xl bg-secondary text-primary">{React.createElement(resolveProviderIcon(model.provider), { className: "size-5" })}</div>
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
                            {resolveProviderLabel(model.provider)}
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
                            {testResult.success ? <CheckCircle2 className="mr-1 inline size-3.5" /> : <XCircle className="mr-1 inline size-3.5" />}{" "}
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
      <section>
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
    </PageContainer>
  );
}
