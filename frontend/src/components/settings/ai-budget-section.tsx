"use client";

import { useEffect, useMemo, useState } from "react";

import {
  novelsApi,
  readerChatApi,
  settingsApi,
  type AIBudgetLimits,
  type AIBudgetResponse,
  type ConversationListItem,
  type Novel,
} from "@/lib/api";
import { SettingsSection } from "./settings-section";

type BudgetScope = "defaults" | "novel" | "conversation";

const EMPTY_LIMITS: AIBudgetLimits = {
  max_calls: 40,
  max_input_tokens: 400_000,
  max_output_tokens: 80_000,
  max_cost_usd: 5,
};

function cloneLimits(value: AIBudgetLimits): AIBudgetLimits {
  return { ...value };
}

function formatNumber(value: number): string {
  return Number.isFinite(value) ? String(value) : "";
}

function BudgetFields({
  title,
  value,
  disabled,
  onChange,
}: {
  title: string;
  value: AIBudgetLimits;
  disabled?: boolean;
  onChange: (next: AIBudgetLimits) => void;
}) {
  const update = (field: keyof AIBudgetLimits, raw: string) => {
    const nextValue = Number(raw);
    onChange({ ...value, [field]: Number.isFinite(nextValue) ? nextValue : 0 });
  };

  const fields: { key: keyof AIBudgetLimits; label: string; step?: string }[] = [
    { key: "max_calls", label: "调用次数上限" },
    { key: "max_input_tokens", label: "输入 Token 上限" },
    { key: "max_output_tokens", label: "输出 Token 上限" },
    { key: "max_cost_usd", label: "费用上限（美元）", step: "0.01" },
  ];

  return (
    <fieldset
      disabled={disabled}
      className="rounded-2xl border border-border/70 bg-card/60 p-4 sm:p-5"
    >
      <legend className="px-1 font-serif text-base font-semibold">{title}</legend>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {fields.map((field) => (
          <label key={field.key} className="grid gap-1.5 text-sm">
            <span className="text-muted-foreground">{field.label}</span>
            <input
              type="number"
              min="1"
              step={field.step ?? "1"}
              value={formatNumber(value[field.key])}
              onChange={(event) => update(field.key, event.target.value)}
              className="h-9 rounded-lg border border-border bg-background px-3 outline-none transition-[border-color,box-shadow] motion-duration-fast focus:border-primary focus:ring-2 focus:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-60"
            />
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export function AIBudgetSection({ chapter }: { chapter: string }) {
  const [scope, setScope] = useState<BudgetScope>("defaults");
  const [novels, setNovels] = useState<Novel[]>([]);
  const [novelId, setNovelId] = useState<number | null>(null);
  const [conversations, setConversations] = useState<ConversationListItem[]>([]);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [config, setConfig] = useState<AIBudgetResponse | null>(null);
  const [conversationLimits, setConversationLimits] =
    useState<AIBudgetLimits>(EMPTY_LIMITS);
  const [novelLimits, setNovelLimits] = useState<AIBudgetLimits>(EMPTY_LIMITS);
  const [arcWindowSize, setArcWindowSize] = useState(3);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void novelsApi
      .list()
      .then((response) => {
        if (cancelled) return;
        setNovels(response.data.items);
        setNovelId((current) => current ?? response.data.items[0]?.id ?? null);
      })
      .catch(() => {
        if (!cancelled) setMessage("小说列表加载失败");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (scope !== "conversation" || novelId == null) {
      return;
    }
    let cancelled = false;
    void readerChatApi
      .listConversations(novelId, { limit: 100 })
      .then((response) => {
        if (cancelled) return;
        setConversations(response.data.items);
        setConversationId((current) =>
          current && response.data.items.some((item) => item.id === current)
            ? current
            : response.data.items[0]?.id ?? null
        );
      })
      .catch(() => {
        if (!cancelled) {
          setConversations([]);
          setConversationId(null);
          setMessage("会话列表加载失败");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [novelId, scope]);

  const targetParams = useMemo(() => {
    if (scope === "novel" && novelId != null) return { novel_id: novelId };
    if (scope === "conversation" && conversationId != null) {
      return { conversation_id: conversationId };
    }
    return undefined;
  }, [conversationId, novelId, scope]);

  useEffect(() => {
    if (scope !== "defaults" && targetParams == null) return;
    let cancelled = false;
    void settingsApi
      .getAIBudget(targetParams)
      .then((response) => {
        if (cancelled) return;
        const next = response.data;
        setConfig(next);
        setConversationLimits(cloneLimits(next.conversation));
        setNovelLimits(cloneLimits(next.novel));
        setArcWindowSize(next.arc_window_size);
        setMessage(null);
      })
      .catch(() => {
        if (!cancelled) setMessage("AI 预算配置加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [scope, targetParams]);

  const save = async () => {
    if (scope === "novel" && novelId == null) return;
    if (scope === "conversation" && conversationId == null) return;
    setSaving(true);
    setMessage(null);
    try {
      const payload =
        scope === "defaults"
          ? {
              conversation: conversationLimits,
              novel: novelLimits,
              arc_window_size: arcWindowSize,
            }
          : scope === "novel"
            ? {
                novel_id: novelId ?? undefined,
                novel: novelLimits,
                arc_window_size: arcWindowSize,
              }
            : {
                conversation_id: conversationId ?? undefined,
                conversation: conversationLimits,
              };
      const response = await settingsApi.putAIBudget(payload);
      const next = response.data;
      setConfig(next);
      setConversationLimits(cloneLimits(next.conversation));
      setNovelLimits(cloneLimits(next.novel));
      setArcWindowSize(next.arc_window_size);
      setMessage("已保存，新的 AI 调用会立即使用此上限");
    } catch {
      setMessage("保存 AI 预算失败，请检查输入值");
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsSection chapter={chapter} title="AI 预算">
      <div className="grid gap-4">
        <p className="text-sm leading-6 text-muted-foreground">
          控制单次对话和单本小说的调用次数、Token 与费用上限。默认值沿用原有的
          40 次/$5 与 400 次/$50；修改后立即生效。
        </p>

        <div className="grid gap-3 rounded-2xl border border-border/70 bg-muted/20 p-4 sm:grid-cols-[12rem_1fr] sm:items-center">
          <label className="grid gap-1 text-sm">
            <span className="text-muted-foreground">编辑作用域</span>
            <select
              value={scope}
              onChange={(event) => {
                const nextScope = event.target.value as BudgetScope;
                setScope(nextScope);
                setConfig(null);
                setLoading(true);
              }}
              className="h-9 rounded-lg border border-border bg-background px-3 outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
            >
              <option value="defaults">默认值</option>
              <option value="novel">指定小说</option>
              <option value="conversation">指定会话</option>
            </select>
          </label>

          {scope !== "defaults" ? (
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="grid gap-1 text-sm">
                <span className="text-muted-foreground">小说</span>
                <select
                  value={novelId ?? ""}
                  onChange={(event) => {
                    setNovelId(Number(event.target.value) || null);
                    setConversationId(null);
                    setConfig(null);
                    setLoading(true);
                  }}
                  className="h-9 rounded-lg border border-border bg-background px-3 outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                >
                  <option value="">请选择小说</option>
                  {novels.map((novel) => (
                    <option key={novel.id} value={novel.id}>
                      {novel.title}
                    </option>
                  ))}
                </select>
              </label>
              {scope === "conversation" ? (
                <label className="grid gap-1 text-sm">
                  <span className="text-muted-foreground">会话</span>
                  <select
                    value={conversationId ?? ""}
                    onChange={(event) => {
                      setConversationId(Number(event.target.value) || null);
                      setConfig(null);
                      setLoading(true);
                    }}
                    className="h-9 rounded-lg border border-border bg-background px-3 outline-none focus:border-primary focus:ring-2 focus:ring-primary/20"
                  >
                    <option value="">请选择会话</option>
                    {conversations.map((conversation) => (
                      <option key={conversation.id} value={conversation.id}>
                        {conversation.title}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
            </div>
          ) : null}
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <BudgetFields
            title="单次对话上限"
            value={conversationLimits}
            disabled={scope === "novel" || loading || saving}
            onChange={setConversationLimits}
          />
          <BudgetFields
            title="单本小说上限"
            value={novelLimits}
            disabled={scope === "conversation" || loading || saving}
            onChange={setNovelLimits}
          />
        </div>

        <label className="grid max-w-sm gap-1.5 text-sm">
          <span className="text-muted-foreground">
            叙事记忆聚合窗口（章）
          </span>
          <input
            type="number"
            min="1"
            max="5"
            value={arcWindowSize}
            disabled={scope === "conversation" || loading || saving}
            onChange={(event) => setArcWindowSize(Number(event.target.value) || 0)}
            className="h-9 rounded-lg border border-border bg-background px-3 outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 disabled:cursor-not-allowed disabled:opacity-60"
          />
          <span className="text-xs leading-5 text-muted-foreground">
            可设 1–5 章；默认 3。短篇建议 1–2，长篇建议 3–5。
          </span>
        </label>

        <div className="flex items-center gap-3">
          <button
            type="button"
            disabled={saving || loading || (scope !== "defaults" && config == null)}
            onClick={() => void save()}
            className="rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-[background-color,opacity] motion-duration-fast hover:bg-primary/85 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saving ? "保存中…" : "保存 AI 预算"}
          </button>
          {message ? (
            <span role="status" className="text-sm text-muted-foreground">
              {message}
            </span>
          ) : null}
        </div>
      </div>
    </SettingsSection>
  );
}
