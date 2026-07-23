"use client";

/**
 * 设置中心 · 用量概览 — 费用与 Token 消耗统计。
 * 数据来自 GET /api/usage/summary；加载失败显示「暂无数据」，不展示假数字。
 */

import { useEffect, useState } from "react";

import { usageApi, type UsageSummary } from "@/lib/api";
import { SettingsSection } from "./settings-section";

type LoadState = "loading" | "ready" | "error";

function formatUsd(value: number): string {
  return `$${value.toFixed(2)}`;
}

export function UsageSection({ chapter }: { chapter: string }) {
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [loadState, setLoadState] = useState<LoadState>("loading");

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await usageApi.summary();
        if (!cancelled) {
          setSummary(res.data);
          setLoadState("ready");
        }
      } catch {
        if (!cancelled) setLoadState("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const items: { label: string; value: string | null }[] = [
    {
      label: "今日花费",
      value: summary ? formatUsd(summary.today_cost_usd) : null,
    },
    {
      label: "本周花费",
      value: summary ? formatUsd(summary.week_cost_usd) : null,
    },
    {
      label: "本月花费",
      value: summary ? formatUsd(summary.month_cost_usd) : null,
    },
    {
      label: "总 Token 数",
      value: summary ? summary.total_tokens.toLocaleString("zh-CN") : null,
    },
  ];

  return (
    <SettingsSection chapter={chapter} title="用量概览">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {items.map((item) => (
          <div key={item.label} className="paper-surface rounded-2xl p-4 sm:p-5">
            <p className="mb-1 text-xs text-muted-foreground">{item.label}</p>
            <p className="font-serif text-xl font-semibold sm:text-2xl">
              {loadState === "loading"
                ? "…"
                : loadState === "error" || item.value === null
                  ? "暂无数据"
                  : item.value}
            </p>
          </div>
        ))}
      </div>
    </SettingsSection>
  );
}
