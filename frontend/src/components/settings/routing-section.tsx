"use client";

/**
 * 设置中心 · 智能路由策略 — 三本书立在迷你书架上，抽出的那本即当前策略。
 * 偏好通过 settingsApi 持久化（GET/PUT /api/settings/routing）。
 */

import { useCallback, useEffect, useState, type ComponentType } from "react";
import { CircleDollarSign, Scale, Sparkles } from "lucide-react";

import { bookHeight, toneOf } from "@/components/bookshelf/book-visual";
import type { RoutingPreference } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useAIConfigStore } from "@/stores/aiConfigStore";
import { SettingsSection } from "./settings-section";

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
  icon: ComponentType<{ className?: string }>;
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

export function RoutingSection({ chapter }: { chapter: string }) {
  const routingPreference = useAIConfigStore((s) => s.routingPreference);
  const fetchRoutingPreference = useAIConfigStore((s) => s.fetchRoutingPreference);
  const setRoutingPreference = useAIConfigStore((s) => s.setRoutingPreference);
  const [saving, setSaving] = useState(false);

  // 挂载时拉取服务端持久化的偏好（store action 内部容错，失败保留默认值）
  useEffect(() => {
    void fetchRoutingPreference();
  }, [fetchRoutingPreference]);

  const handleSelect = useCallback(
    async (value: RoutingPreference) => {
      if (saving || value === routingPreference) return;
      setSaving(true);
      try {
        await setRoutingPreference(value);
      } finally {
        setSaving(false);
      }
    },
    [saving, routingPreference, setRoutingPreference]
  );

  return (
    <SettingsSection chapter={chapter} title="智能路由策略">
      {/* 迷你木框书架：三本书错落而立，抽出的那本为当前策略 */}
      <div className="rounded-[20px] bg-gradient-to-b from-[#e6cda1] to-[#b98f5c] p-3 shadow-[0_24px_50px_-30px_rgba(90,65,30,0.45)]">
        <div className="rounded-xl bg-[#eee0c2] px-4 pt-8 sm:px-8">
          <div className="grid grid-cols-3 items-end gap-2 sm:gap-6">
            {routingOptions.map((option) => {
              const selected = routingPreference === option.value;
              const [from, to] = toneOf(option.label);
              const OptionIcon = option.icon;
              return (
                <div key={option.value} className="flex justify-center">
                  <button
                    type="button"
                    aria-pressed={selected}
                    disabled={saving}
                    title={`${option.label}：${option.description}`}
                    onClick={() => void handleSelect(option.value)}
                    className={cn(
                      "relative flex w-16 flex-col items-center justify-between rounded-[3px] pb-3 pt-4 shadow-[inset_0_1px_2px_rgba(255,255,255,0.25),0_10px_18px_-10px_rgba(60,40,20,0.55)] transition-transform duration-300 disabled:cursor-wait sm:w-20",
                      selected
                        ? "-translate-y-2.5 ring-2 ring-[#b03a2e]/60"
                        : "hover:-translate-y-1.5"
                    )}
                    style={{
                      height: bookHeight(option.label) - 30,
                      background: `linear-gradient(165deg, ${from}, ${to})`,
                    }}
                  >
                    <span
                      className="font-serif text-sm font-semibold tracking-[0.25em] text-[#f3e6c2]"
                      style={{ writingMode: "vertical-rl" }}
                    >
                      {option.label}
                    </span>
                    <OptionIcon className="size-4 text-white/60" />
                    {/* 当前策略印章（朱砂白文） */}
                    {selected && (
                      <span
                        aria-hidden
                        className="absolute -bottom-2 left-1/2 grid size-5 -translate-x-1/2 rotate-3 place-items-center rounded-[3px] border border-white/55 bg-[#b03a2e]/95 font-serif text-[11px] font-semibold text-white"
                      >
                        用
                      </span>
                    )}
                  </button>
                </div>
              );
            })}
          </div>
          {/* 木搁板 */}
          <div
            aria-hidden
            className="mx-[-8px] mt-0 h-[11px] rounded-sm bg-gradient-to-b from-[#e2c08a] via-[#c69f66] to-[#a97f4e] shadow-[inset_0_1px_0_rgba(255,245,220,0.5),0_8px_14px_-4px_rgba(100,70,30,0.4)]"
          />
        </div>
        {/* 策略说明 */}
        <div className="grid grid-cols-3 gap-2 px-2 pb-1 pt-3 sm:gap-6">
          {routingOptions.map((option) => (
            <p
              key={option.value}
              className={cn(
                "text-center text-xs leading-5",
                routingPreference === option.value
                  ? "font-medium text-[#4a3a22]"
                  : "text-[#6b5636]/80"
              )}
            >
              {option.description}
            </p>
          ))}
        </div>
      </div>
    </SettingsSection>
  );
}
