"use client";

import React from "react";

interface ProgressBarProps {
  /** 本章内阅读进度 0-100 */
  chapterPercent: number;
  chapterTitle: string;
  /** 全书位置：第几章 / 共几章（辅助信息） */
  chapterIndex: number;
  chapterTotal: number;
}

export function ProgressBar({
  chapterPercent,
  chapterTitle,
  chapterIndex,
  chapterTotal,
}: ProgressBarProps) {
  const pct = Math.min(100, Math.max(0, Math.round(chapterPercent)));

  return (
    <div className="z-30 shrink-0 border-t border-border/70 bg-card/90 backdrop-blur-xl">
      <div className="mx-auto max-w-3xl px-4 py-2.5">
        <div className="mb-1 flex items-center justify-between text-xs text-muted-foreground">
          <span className="mr-4 flex-1 truncate">{chapterTitle}</span>
          <span className="shrink-0 tabular-nums">
            本章 {pct}%
            {chapterTotal > 0
              ? ` · 第 ${chapterIndex}/${chapterTotal} 章`
              : null}
          </span>
        </div>

        <div className="h-1 overflow-hidden rounded-full bg-muted">
          <div
            className="h-full bg-primary transition-[width] motion-duration-fast motion-ease-enter"
            style={{ width: `${pct}%` }}
            role="progressbar"
            aria-valuenow={pct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="本章阅读进度"
          />
        </div>
      </div>
    </div>
  );
}
