"use client";

/**
 * 分析页顶部选书条 — 横向迷你书脊（书架同源视觉）。
 * 纯视觉选择器；页面同时保留 sr-only 的原生 <select> 供无障碍与测试。
 */

import type { CSSProperties } from "react";

import { cn } from "@/lib/utils";
import type { Novel } from "@/lib/api";
import { sealChar, toneOf } from "./book-visual";

type Props = {
  novels: Novel[];
  /** 当前选中的小说 id（字符串，可为空） */
  value: string;
  onSelect: (id: string) => void;
  className?: string;
};

export function NovelPickerStrip({ novels, value, onSelect, className }: Props) {
  return (
    <div
      className={cn("flex min-w-0 flex-1 flex-col", className)}
      data-testid="novel-picker-strip"
    >
      <div className="flex items-end gap-2 overflow-x-auto px-1 pt-1.5 [scrollbar-width:thin]">
        {novels.map((novel) => {
          const [from, to] = toneOf(novel.title);
          const selected = String(novel.id) === String(value);
          return (
            <button
              key={novel.id}
              type="button"
              onClick={() => onSelect(String(novel.id))}
              aria-pressed={selected}
              title={`${novel.title} · ${novel.chapter_count} 章`}
              className={cn(
                "relative flex shrink-0 flex-col items-center overflow-hidden rounded-[3px] text-left",
                "transition-[transform,box-shadow] motion-duration-fast motion-ease-enter",
                selected
                  ? "-translate-y-1 shadow-[0_10px_18px_-8px_rgba(60,40,15,0.55)] ring-2 ring-[#d6ab54]"
                  : "shadow-[0_3px_8px_-3px_rgba(60,40,15,0.4)] hover:-translate-y-0.5"
              )}
              style={
                {
                  width: 30,
                  height: selected ? 78 : 68,
                  background: `linear-gradient(165deg, ${from}, ${to})`,
                } as CSSProperties
              }
            >
              <span
                aria-hidden
                className="pointer-events-none mt-1.5 max-h-[46px] overflow-hidden font-serif text-[10px] font-semibold leading-tight tracking-wider text-[#f3e6c2]"
                style={{ writingMode: "vertical-rl" }}
              >
                {novel.title}
              </span>
              <span
                aria-hidden
                className="pointer-events-none absolute bottom-1 left-1/2 grid size-3.5 -translate-x-1/2 rotate-3 place-items-center rounded-[2px] border border-white/50 bg-[#b03a2e]/90 font-serif text-[8px] font-semibold text-white"
              >
                {sealChar(novel.status)}
              </span>
            </button>
          );
        })}
      </div>
      {/* 层板 */}
      <div
        aria-hidden
        className="mt-1 h-[7px] rounded-sm bg-gradient-to-b from-[#e2c08a] via-[#c69f66] to-[#a97f4e] shadow-[0_6px_10px_-4px_rgba(100,70,30,0.4)]"
      />
    </div>
  );
}
