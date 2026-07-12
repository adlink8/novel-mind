"use client";

import React from "react";

interface ProgressBarProps {
  current: number;
  total: number;
  chapterTitle: string;
}

export function ProgressBar({ current, total, chapterTitle }: ProgressBarProps) {
  const percent = total > 0 ? Math.round((current / total) * 100) : 0;

  return (
    <div className="absolute inset-x-0 bottom-0 z-30 border-t border-border/70 bg-card/90 backdrop-blur-xl">
      <div className="mx-auto max-w-3xl px-4 py-2.5">
        {/* 章节信息 */}
        <div className="flex items-center justify-between text-xs text-muted-foreground mb-1">
          <span className="truncate flex-1 mr-4">{chapterTitle}</span>
          <span>
            {current} / {total} {"章"} ({percent}%)
          </span>
        </div>

        {/* 进度条 */}
        <div className="h-1 bg-muted rounded-full overflow-hidden">
          <div
            className="h-full bg-primary transition-all duration-300"
            style={{ width: `${percent}%` }}
          />
        </div>
      </div>
    </div>
  );
}
