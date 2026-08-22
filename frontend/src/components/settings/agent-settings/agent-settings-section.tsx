"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  settingsApi,
  type AgentSettingsPayload,
  type UserPreferenceMemory,
} from "@/lib/api/settings";
import { SettingsSection } from "../settings-section";

type BooleanSettingKey =
  | "auto_deep_analysis"
  | "memory_enabled"
  | "show_analysis_progress"
  | "notify_analysis_complete"
  | "auto_create_candidate_artifacts";

const booleanSettings: { key: BooleanSettingKey; label: string }[] = [
  { key: "auto_deep_analysis", label: "自动执行深度分析" },
  { key: "memory_enabled", label: "启用记忆" },
  { key: "show_analysis_progress", label: "展示分析进度" },
  { key: "notify_analysis_complete", label: "分析完成时通知" },
  { key: "auto_create_candidate_artifacts", label: "自动创建候选产物" },
];

function formatMemoryExpiry(expiresAt: string | null) {
  if (!expiresAt) return "永久";
  const date = new Date(expiresAt);
  return Number.isNaN(date.getTime())
    ? expiresAt
    : date.toISOString().slice(0, 10).replaceAll("-", "/");
}

export function AgentSettingsSection({ chapter }: { chapter: string }) {
  const [settings, setSettings] = useState<AgentSettingsPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [memoryItems, setMemoryItems] = useState<UserPreferenceMemory[]>([]);
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [memoryLoading, setMemoryLoading] = useState(false);
  const [memoryAction, setMemoryAction] = useState<number | "clear" | null>(null);

  useEffect(() => {
    let active = true;
    void settingsApi
      .getAgent()
      .then((response) => {
        if (active) setSettings(response.data);
      })
      .catch(() => {
        if (active) setMessage("设置加载失败，请稍后再试");
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, []);

  const updateBoolean = (key: BooleanSettingKey, value: boolean) => {
    setSettings((current) => (current ? { ...current, [key]: value } : current));
    if (key === "memory_enabled" && !value) {
      setMemoryOpen(false);
      setMemoryItems([]);
    }
    setMessage(null);
  };

  const updateRetention = (value: string) => {
    const days = value === "" ? null : Number(value);
    setSettings((current) =>
      current ? { ...current, memory_retention_days: days } : current
    );
    setMessage(null);
  };

  const saveSettings = async () => {
    if (!settings) return;
    setSaving(true);
    setMessage(null);
    try {
      const latestResponse = await settingsApi.getAgent();
      const nextSettings: AgentSettingsPayload = {
        ...latestResponse.data,
        auto_deep_analysis: settings.auto_deep_analysis,
        memory_enabled: settings.memory_enabled,
        memory_retention_days: settings.memory_retention_days,
        show_analysis_progress: settings.show_analysis_progress,
        notify_analysis_complete: settings.notify_analysis_complete,
        auto_create_candidate_artifacts: settings.auto_create_candidate_artifacts,
      };
      const response = await settingsApi.putAgent(nextSettings);
      setSettings(response.data);
      setMessage("Agent 设置已保存");
    } catch {
      setMessage("保存失败，请稍后再试");
    } finally {
      setSaving(false);
    }
  };

  const loadMemories = async () => {
    setMemoryOpen(true);
    setMemoryLoading(true);
    setMessage(null);
    try {
      const response = await settingsApi.listMemoryPreferences();
      setMemoryItems(response.data.items);
    } catch {
      setMessage("记忆加载失败，请稍后再试");
    } finally {
      setMemoryLoading(false);
    }
  };

  const deleteMemory = async (memoryId: number) => {
    setMemoryAction(memoryId);
    setMessage(null);
    try {
      await settingsApi.deleteMemoryPreference(memoryId);
      setMemoryItems((current) => current.filter((item) => item.id !== memoryId));
      setMessage("记忆已删除");
    } catch {
      setMessage("删除记忆失败，请稍后再试");
    } finally {
      setMemoryAction(null);
    }
  };

  const clearMemories = async () => {
    setMemoryAction("clear");
    setMessage(null);
    try {
      await settingsApi.clearMemoryPreferences();
      setMemoryItems([]);
      setMessage("记忆已清空");
    } catch {
      setMessage("清空记忆失败，请稍后再试");
    } finally {
      setMemoryAction(null);
    }
  };

  return (
    <SettingsSection chapter={chapter} title="Agent 设置">
      <div className="paper-surface rounded-3xl p-5 sm:p-6">
        {loading ? <p className="text-sm text-muted-foreground">加载中…</p> : null}
        {!loading && settings ? (
          <div className="space-y-5">
            <div className="grid gap-3 sm:grid-cols-2">
              {booleanSettings.map(({ key, label }) => (
                <label
                  key={key}
                  className="flex items-center gap-3 rounded-2xl border border-border/70 p-3 text-sm"
                >
                  <input
                    type="checkbox"
                    aria-label={label}
                    checked={settings[key]}
                    onChange={(event) => updateBoolean(key, event.target.checked)}
                    className="size-4 accent-primary"
                  />
                  <span>{label}</span>
                </label>
              ))}
            </div>

            {settings.memory_enabled ? (
              <div className="space-y-3 rounded-2xl border border-border/70 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-medium">个性化记忆</p>
                    <p className="text-xs text-muted-foreground">
                      仅展示当前账户仍在有效期内的记忆。
                    </p>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => void loadMemories()}
                    disabled={memoryLoading}
                  >
                    {memoryLoading ? "加载记忆…" : "查看记忆"}
                  </Button>
                </div>

                {memoryOpen ? (
                  <div className="space-y-3" aria-label="个性化记忆列表">
                    {memoryLoading ? (
                      <p className="text-sm text-muted-foreground">加载中…</p>
                    ) : (
                      <>
                        {memoryItems.length === 0 ? (
                          <p className="text-sm text-muted-foreground">暂无已保存的记忆。</p>
                        ) : (
                        <div className="space-y-2">
                          {memoryItems.map((item) => (
                            <div
                              key={item.id}
                              className="grid gap-2 rounded-xl border border-border/60 p-3 text-sm sm:grid-cols-[1fr_1fr_1fr_1fr_auto] sm:items-center"
                            >
                              <span>
                                <span className="mr-1 text-xs text-muted-foreground">类型</span>
                                {item.kind}
                              </span>
                              <span>
                                <span className="mr-1 text-xs text-muted-foreground">内容</span>
                                {item.value}
                              </span>
                              <span>
                                <span className="mr-1 text-xs text-muted-foreground">来源</span>
                                消息 #{item.source_message_id}
                              </span>
                              <span>
                                <span className="mr-1 text-xs text-muted-foreground">到期</span>
                                {formatMemoryExpiry(item.expires_at)}
                              </span>
                              <Button
                                type="button"
                                variant="ghost"
                                aria-label="删除记忆"
                                onClick={() => void deleteMemory(item.id)}
                                disabled={memoryAction !== null}
                              >
                                删除
                              </Button>
                            </div>
                          ))}
                        </div>
                        )}
                        <Button
                          type="button"
                          variant="outline"
                          onClick={() => void clearMemories()}
                          disabled={memoryAction !== null}
                        >
                          {memoryAction === "clear" ? "清空中…" : "清空记忆"}
                        </Button>
                      </>
                    )}
                  </div>
                ) : null}
              </div>
            ) : (
              <p className="rounded-2xl border border-border/70 p-4 text-sm text-muted-foreground">
                关闭记忆后将停止写入和召回，但会保留已有数据。
              </p>
            )}

            <label className="block max-w-xs space-y-2 text-sm" htmlFor="memory-retention-days">
              <span>记忆保留天数</span>
              <Input
                id="memory-retention-days"
                type="number"
                min={1}
                placeholder="留空表示不设期限"
                value={settings.memory_retention_days ?? ""}
                onChange={(event) => updateRetention(event.target.value)}
              />
            </label>

            <div className="flex flex-wrap items-center gap-3">
              <Button type="button" onClick={() => void saveSettings()} disabled={saving}>
                {saving ? "保存中…" : "保存 Agent 设置"}
              </Button>
              {message ? (
                <p className="text-sm text-muted-foreground" role="status">
                  {message}
                </p>
              ) : null}
            </div>
          </div>
        ) : null}
        {!loading && !settings && message ? (
          <p className="text-sm text-destructive" role="alert">
            {message}
          </p>
        ) : null}
      </div>
    </SettingsSection>
  );
}
