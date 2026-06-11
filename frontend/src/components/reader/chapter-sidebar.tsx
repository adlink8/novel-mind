"use client";

import React from "react";
import { cn } from "@/lib/utils";
import type { Chapter } from "@/lib/api";

interface ChapterSidebarProps {
  chapters: Chapter[];
  currentChapterId: number;
  onSelectChapter: (chapterId: number) => void;
  isOpen: boolean;
  onToggle: () => void;
}

export function ChapterSidebar({
  chapters,
  currentChapterId,
  onSelectChapter,
  isOpen,
  onToggle,
}: ChapterSidebarProps) {
  return (
    <>
      {/* 移动端遮罩 */}
      {isOpen && (
        <div
          className="fixed inset-0 bg-black/30 z-40 lg:hidden"
          onClick={onToggle}
        />
      )}

      {/* 侧边栏 */}
      <aside
        className={cn(
          "fixed lg:static inset-y-0 left-0 z-50 w-72 bg-background border-r border-border",
          "transform transition-transform duration-300 ease-in-out",
          "flex flex-col",
          isOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0 lg:w-64"
        )}
      >
        {/* 头部 */}
        <div className="flex items-center justify-between p-4 border-b border-border">
          <h3 className="font-semibold text-sm">{"章节目录"}</h3>
          <button
            onClick={onToggle}
            className="lg:hidden p-1 hover:bg-muted rounded"
          >
            {"✕"}
          </button>
        </div>

        {/* 章节列表 */}
        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {chapters.map((chapter) => (
            <button
              key={chapter.id}
              onClick={() => {
                onSelectChapter(chapter.id);
                onToggle();
              }}
              className={cn(
                "w-full text-left px-3 py-2 rounded-md text-sm transition-colors",
                "hover:bg-muted",
                currentChapterId === chapter.id
                  ? "bg-primary/10 text-primary font-medium"
                  : "text-muted-foreground"
              )}
            >
              <div className="line-clamp-1">{chapter.title}</div>
              <div className="text-xs text-muted-foreground mt-0.5">
                {chapter.word_count.toLocaleString()} {"字"}
              </div>
            </button>
          ))}
        </div>
      </aside>
    </>
  );
}
