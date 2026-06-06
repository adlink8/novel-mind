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

type RoutingPreference = "quality" | "balanced" | "budget";

const routingOptions: {
  value: RoutingPreference;
  label: string;
  icon: string;
  description: string;
}[] = [
  {
    value: "quality",
    label: "\u6781\u81F4\u8D28\u91CF",
    icon: "\u2B50",
    description: "\u4F18\u5148\u4F7F\u7528\u6700\u5F3A\u6A21\u578B\uFF0C\u9002\u5408\u6DF1\u5EA6\u5206\u6790\u548C\u590D\u6742\u521B\u4F5C",
  },
  {
    value: "balanced",
    label: "\u667A\u80FD\u5747\u8861",
    icon: "\u2696\uFE0F",
    description: "\u667A\u80FD\u5206\u914D\u4EFB\u52A1\u5230\u5408\u9002\u7684\u6A21\u578B\uFF0C\u517C\u987E\u8D28\u91CF\u548C\u6210\u672C",
  },
  {
    value: "budget",
    label: "\u8282\u7701\u6A21\u5F0F",
    icon: "\uD83D\uDCB0",
    description: "\u4F18\u5148\u4F7F\u7528\u8F7B\u91CF\u6A21\u578B\uFF0C\u9002\u5408\u65E5\u5E38\u7B80\u5355\u4EFB\u52A1",
  },
];

const providerOptions: { value: AIModelConfig["provider"]; label: string }[] = [
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "ollama", label: "Ollama" },
  { value: "custom", label: "\u81EA\u5B9A\u4E49" },
];

const providerLabels: Record<AIModelConfig["provider"], string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  ollama: "Ollama",
  custom: "\u81EA\u5B9A\u4E49",
};

const providerIcons: Record<AIModelConfig["provider"], string> = {
  openai: "\uD83E\uDD16",
  anthropic: "\uD83E\uDDE0",
  ollama: "\uD83E\uDD99",
  custom: "\uD83D\uDD27",
};

// Placeholder cost data
const costSummary = {
  today: "\xA50.00",
  thisWeek: "\xA50.00",
  thisMonth: "\xA50.00",
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

  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [formData, setFormData] = useState({
    model_name: "",
    provider: "openai" as AIModelConfig["provider"],
    base_url: "",
  });
  const [addLoading, setAddLoading] = useState(false);

  const handleAddModel = useCallback(async () => {
    if (!formData.model_name.trim()) return;
    setAddLoading(true);
    try {
      await addModel({
        model_name: formData.model_name,
        provider: formData.provider,
        base_url: formData.base_url || undefined,
      });
      setFormData({ model_name: "", provider: "openai", base_url: "" });
      setAddDialogOpen(false);
      fetchModels();
    } catch {
      // Error handled by store
    } finally {
      setAddLoading(false);
    }
  }, [formData, addModel, fetchModels]);

  const handleTestConnection = useCallback(
    async (id: string) => {
      await testConnection(id);
    },
    [testConnection]
  );

  const handleSetDefault = useCallback(
    async (id: string) => {
      await setDefaultModel(id);
    },
    [setDefaultModel]
  );

  const handleRemoveModel = useCallback(
    async (id: string) => {
      await removeModel(id);
    },
    [removeModel]
  );

  return (
    <div className="p-6 md:p-8 max-w-4xl mx-auto">
      {/* Header */}
      <div className="mb-8">
        <h2 className="text-2xl font-bold">{"AI \u8BBE\u7F6E"}</h2>
        <p className="text-muted-foreground mt-1">
          {"\u914D\u7F6E AI \u6A21\u578B\u548C\u667A\u80FD\u8DEF\u7531\u7B56\u7565"}
        </p>
      </div>

      {/* Routing Preference */}
      <section className="mb-10">
        <h3 className="text-lg font-semibold mb-4">{"\u667A\u80FD\u8DEF\u7531\u7B56\u7565"}</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {routingOptions.map((option) => (
            <Card
              key={option.value}
              className={`cursor-pointer transition-all ${
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
                  {routingPreference === option.value && (
                    <Badge className="ml-auto bg-novel-500 text-white text-xs">
                      {"\u5F53\u524D"}
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

      {/* AI Models */}
      <section className="mb-10">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold">{"AI \u6A21\u578B"}</h3>
          <Button onClick={() => setAddDialogOpen(true)}>
            + {"\u6DFB\u52A0\u6A21\u578B"}
          </Button>
        </div>

        {loading && models.length === 0 && (
          <div className="flex items-center justify-center py-12">
            <div className="text-center">
              <div className="text-4xl mb-4 animate-pulse">{"\u23F3"}</div>
              <p className="text-muted-foreground">{"\u52A0\u8F7D\u4E2D..."}</p>
            </div>
          </div>
        )}

        {!loading && models.length === 0 && (
          <EmptyState
            icon={"\uD83E\uDD16"}
            title={"\u8FD8\u6CA1\u6709\u914D\u7F6E AI \u6A21\u578B"}
            description={
              "\u6DFB\u52A0\u4F60\u7684\u7B2C\u4E00\u4E2A AI \u6A21\u578B\uFF0C\u5F00\u59CB\u667A\u80FD\u5206\u6790\u4E0E\u521B\u4F5C"
            }
            actionLabel={"\u6DFB\u52A0\u6A21\u578B"}
            onAction={() => setAddDialogOpen(true)}
          />
        )}

        {models.length > 0 && (
          <div className="space-y-3">
            {models.map((model) => {
              const testResult = getTestResult(model.id);
              return (
                <Card key={model.id}>
                  <CardContent>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-4">
                        <div className="w-12 h-12 rounded-xl bg-muted flex items-center justify-center text-2xl">
                          {providerIcons[model.provider]}
                        </div>
                        <div>
                          <div className="flex items-center gap-2">
                            <h4 className="font-semibold">
                              {model.model_name}
                            </h4>
                            {model.is_default && (
                              <Badge className="bg-novel-100 text-novel-800 text-xs">
                                {"\u9ED8\u8BA4"}
                              </Badge>
                            )}
                          </div>
                          <p className="text-xs text-muted-foreground mt-0.5">
                            {providerLabels[model.provider]}
                            {model.base_url && (
                              <span className="ml-2">
                                {model.base_url}
                              </span>
                            )}
                          </p>
                        </div>
                      </div>

                      <div className="flex items-center gap-2">
                        {/* Test result */}
                        {testResult && (
                          <span
                            className={`text-xs ${
                              testResult.success
                                ? "text-green-600"
                                : "text-red-600"
                            }`}
                          >
                            {testResult.success ? "\u2705" : "\u274C"}{" "}
                            {testResult.message}
                          </span>
                        )}

                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => handleTestConnection(model.id)}
                        >
                          {"\u6D4B\u8BD5"}
                        </Button>

                        {!model.is_default && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleSetDefault(model.id)}
                          >
                            {"\u8BBE\u4E3A\u9ED8\u8BA4"}
                          </Button>
                        )}

                        <Button
                          variant="destructive"
                          size="sm"
                          onClick={() => handleRemoveModel(model.id)}
                        >
                          {"\u5220\u9664"}
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

      {/* Cost Summary */}
      <section className="mb-10">
        <h3 className="text-lg font-semibold mb-4">{"\u7528\u91CF\u6982\u89C8"}</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <Card>
            <CardContent>
              <p className="text-xs text-muted-foreground mb-1">
                {"\u4ECA\u65E5\u82B1\u8D39"}
              </p>
              <p className="text-xl font-bold">{costSummary.today}</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent>
              <p className="text-xs text-muted-foreground mb-1">
                {"\u672C\u5468\u82B1\u8D39"}
              </p>
              <p className="text-xl font-bold">{costSummary.thisWeek}</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent>
              <p className="text-xs text-muted-foreground mb-1">
                {"\u672C\u6708\u82B1\u8D39"}
              </p>
              <p className="text-xl font-bold">{costSummary.thisMonth}</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent>
              <p className="text-xs text-muted-foreground mb-1">
                {"\u603B Token \u6570"}
              </p>
              <p className="text-xl font-bold">{costSummary.totalTokens}</p>
            </CardContent>
          </Card>
        </div>
      </section>

      {/* Add Model Dialog */}
      <Dialog open={addDialogOpen} onOpenChange={setAddDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{"\u6DFB\u52A0 AI \u6A21\u578B"}</DialogTitle>
            <DialogDescription>
              {"\u914D\u7F6E\u65B0\u7684 AI \u6A21\u578B\u7528\u4E8E\u5C0F\u8BF4\u5206\u6790\u548C\u521B\u4F5C"}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            {/* Model Name */}
            <div className="space-y-2">
              <label className="text-sm font-medium">{"\u6A21\u578B\u540D\u79F0"}</label>
              <Input
                placeholder={"\u4F8B\u5982\uFF1AGPT-4o\u3001Claude 3.5 Sonnet"}
                value={formData.model_name}
                onChange={(e) =>
                  setFormData((prev) => ({
                    ...prev,
                    model_name: e.target.value,
                  }))
                }
              />
            </div>

            {/* Provider */}
            <div className="space-y-2">
              <label className="text-sm font-medium">{"\u670D\u52A1\u63D0\u4F9B\u5546"}</label>
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

            {/* Base URL */}
            <div className="space-y-2">
              <label className="text-sm font-medium">
                {"Base URL"}{" "}
                <span className="text-muted-foreground font-normal">
                  ({"\u53EF\u9009"})
                </span>
              </label>
              <Input
                placeholder={"\u81EA\u5B9A\u4E49 API \u5730\u5740\uFF0C\u4F8B\u5982\uFF1Ahttps://api.example.com/v1"}
                value={formData.base_url}
                onChange={(e) =>
                  setFormData((prev) => ({
                    ...prev,
                    base_url: e.target.value,
                  }))
                }
              />
            </div>

            {/* Actions */}
            <div className="flex justify-end gap-2 pt-2">
              <DialogClose>
                <Button variant="outline">{"\u53D6\u6D88"}</Button>
              </DialogClose>
              <Button
                onClick={handleAddModel}
                disabled={!formData.model_name.trim() || addLoading}
              >
                {addLoading ? "\u6DFB\u52A0\u4E2D..." : "\u6DFB\u52A0\u6A21\u578B"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
