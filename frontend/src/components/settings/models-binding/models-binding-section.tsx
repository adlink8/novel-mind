"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { aiModelsApi, settingsApi, type AgentTaskModelBindings, type AgentSettingsPayload } from "@/lib/api";
import { SettingsSection } from "../settings-section";

export interface AgentModelOption {
  id: number;
  name: string;
  model_id: string;
}

const EMPTY_BINDINGS: AgentTaskModelBindings = {
  qa: null,
  deep_analysis: null,
  continuation: null,
  illustration: null,
  rag_eval: null,
  embedding: null,
};

const taskFields: { key: keyof AgentTaskModelBindings; label: string }[] = [
  { key: "qa", label: "问答" },
  { key: "deep_analysis", label: "深度分析" },
  { key: "continuation", label: "续写" },
  { key: "illustration", label: "插图" },
  { key: "rag_eval", label: "RAG 评估" },
  { key: "embedding", label: "嵌入" },
];

interface ModelsBindingSectionProps {
  chapter: string;
  models?: AgentModelOption[];
  bindings?: AgentTaskModelBindings;
  onChange?: (bindings: AgentTaskModelBindings) => void;
}

export function ModelsBindingSection({
  chapter,
  models: providedModels,
  bindings: providedBindings,
  onChange,
}: ModelsBindingSectionProps) {
  const [models, setModels] = useState<AgentModelOption[]>(providedModels ?? []);
  const [bindings, setBindings] = useState<AgentTaskModelBindings>(
    providedBindings ?? EMPTY_BINDINGS
  );
  const [settings, setSettings] = useState<AgentSettingsPayload | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (providedModels && providedBindings) return;
    let active = true;
    void Promise.all([settingsApi.getAgent(), aiModelsApi.list()])
      .then(([settingsResponse, modelsResponse]) => {
        if (!active) return;
        setSettings(settingsResponse.data);
        setBindings(settingsResponse.data.task_model_bindings);
        setModels(
          modelsResponse.data.map((model) => ({
            id: model.id,
            name: model.name,
            model_id: model.model_id,
          }))
        );
      })
      .catch(() => {
        if (active) setMessage("模型绑定加载失败，请稍后再试");
      });

    return () => {
      active = false;
    };
  }, [providedBindings, providedModels]);

  const updateBinding = (key: keyof AgentTaskModelBindings, value: string) => {
    const next = { ...bindings, [key]: value === "" ? null : Number(value) };
    setBindings(next);
    onChange?.(next);
    setMessage(null);
  };

  const saveBindings = async () => {
    if (!settings) return;
    setSaving(true);
    setMessage(null);
    try {
      const latestResponse = await settingsApi.getAgent();
      const response = await settingsApi.putAgent({
        ...latestResponse.data,
        task_model_bindings: bindings,
      });
      setSettings(response.data);
      setBindings(response.data.task_model_bindings);
      setMessage("任务模型绑定已保存");
    } catch {
      setMessage("保存失败，请稍后再试");
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsSection chapter={chapter} title="任务模型绑定">
      <div className="paper-surface rounded-3xl p-5 sm:p-6">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {taskFields.map(({ key, label }) => (
            <label key={key} className="space-y-2 text-sm" htmlFor={`model-binding-${key}`}>
              <span>{label}</span>
              <select
                id={`model-binding-${key}`}
                aria-label={label}
                value={bindings[key] ?? ""}
                onChange={(event) => updateBinding(key, event.target.value)}
                className="h-9 w-full rounded-lg border border-input bg-background px-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring/50"
              >
                <option value="">不绑定</option>
                {models.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.name} · {model.model_id}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </div>
        {!providedModels && !settings ? (
          <p className="mt-4 text-sm text-muted-foreground">加载中…</p>
        ) : null}
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <Button
            type="button"
            onClick={() => void saveBindings()}
            disabled={saving || !settings}
          >
            {saving ? "保存中…" : "保存模型绑定"}
          </Button>
          {message ? (
            <p className="text-sm text-muted-foreground" role="status">
              {message}
            </p>
          ) : null}
        </div>
      </div>
    </SettingsSection>
  );
}
