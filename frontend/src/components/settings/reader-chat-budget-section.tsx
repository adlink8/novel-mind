"use client";

import { useEffect, useState } from "react";

import {
  settingsApi,
  type ReaderChatBudgetScope,
  type ReaderChatBudgetSettings,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { SettingsSection } from "./settings-section";

const fields: Array<{ key: keyof ReaderChatBudgetScope; label: string }> = [
  { key: "max_calls", label: "最大调用次数" },
  { key: "max_input_tokens", label: "最大输入 Token" },
  { key: "max_output_tokens", label: "最大输出 Token" },
  { key: "max_cost_usd", label: "最大费用（USD）" },
];

export function ReaderChatBudgetSection({ chapter }: { chapter: string }) {
  const [value, setValue] = useState<ReaderChatBudgetSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let cancelled = false;
    void settingsApi.getReaderChatBudget().then(
      (response) => {
        if (!cancelled) setValue(response.data);
      },
      () => {
        if (!cancelled) setMessage("预算配置读取失败，保留服务端默认值");
      }
    );
    return () => {
      cancelled = true;
    };
  }, []);

  const update = (
    scope: keyof ReaderChatBudgetSettings,
    key: keyof ReaderChatBudgetScope,
    raw: string
  ) => {
    if (!value) return;
    const parsed = key === "max_cost_usd" ? Number.parseFloat(raw) : Number.parseInt(raw, 10);
    setValue({
      ...value,
      [scope]: { ...value[scope], [key]: Number.isFinite(parsed) ? parsed : 0 },
    });
  };

  async function save() {
    if (!value) return;
    setSaving(true);
    setMessage("");
    try {
      const response = await settingsApi.putReaderChatBudget(value);
      setValue(response.data);
      setMessage("已保存；只影响之后新建的 Reader Chat 预算账本");
    } catch {
      setMessage("保存失败，请检查数值范围后重试");
    } finally {
      setSaving(false);
    }
  }

  return (
    <SettingsSection chapter={chapter} title="Reader Chat 预算">
      <div className="paper-surface rounded-2xl p-4 sm:p-5">
        <p className="mb-4 text-sm text-muted-foreground">
          预算在模型调用前按最坏情况预留。已创建的账本保留原冻结上限，避免运行中改变预算口径。
        </p>
        <div className="grid gap-5 md:grid-cols-2">
          {(["conversation", "novel"] as const).map((scope) => (
            <fieldset key={scope} className="space-y-3 rounded-xl border border-border/60 p-3">
              <legend className="px-1 text-sm font-medium">
                {scope === "conversation" ? "单个会话" : "单本小说"}
              </legend>
              {fields.map(({ key, label }) => (
                <label key={key} className="block space-y-1 text-xs text-muted-foreground">
                  {label}
                  <Input
                    type="number"
                    min={key === "max_cost_usd" ? 0 : 1}
                    step={key === "max_cost_usd" ? "0.01" : 1}
                    value={value?.[scope][key] ?? ""}
                    onChange={(event) => update(scope, key, event.target.value)}
                  />
                </label>
              ))}
            </fieldset>
          ))}
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button type="button" onClick={() => void save()} disabled={!value || saving}>
            {saving ? "保存中…" : "保存预算"}
          </Button>
          {message && <span className="text-sm text-muted-foreground" role="status">{message}</span>}
        </div>
      </div>
    </SettingsSection>
  );
}
