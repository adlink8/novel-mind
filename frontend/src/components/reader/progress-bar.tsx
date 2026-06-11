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
    <div className="fixed bottom-0 left-0 right-0 bg-background/95 backdrop-blur border-t border-border z-30">
      <div className="max-w-3xl mx-auto px-4 py-2">
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
