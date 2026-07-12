"use client";

import React from "react";
import { cn } from "@/lib/utils";
import type { Chapter } from "@/lib/api";
import { BookOpenText, X } from "lucide-react";

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
          "fixed inset-y-0 left-0 z-50 w-72 border-r border-border/70 bg-sidebar/95 backdrop-blur-xl lg:static lg:mr-4 lg:rounded-[28px] lg:border lg:border-white/60",
          "transform transition-transform duration-300 ease-in-out",
          "flex flex-col",
          isOpen ? "translate-x-0" : "-translate-x-full lg:w-64 lg:translate-x-0"
        )}
      >
        {/* 头部 */}
        <div className="flex items-center justify-between border-b border-border/70 p-4">
          <h3 className="flex items-center gap-2 font-serif font-semibold"><BookOpenText className="size-4 text-primary" />章节目录</h3>
          <button
            onClick={onToggle}
            className="cursor-pointer rounded-lg p-1.5 hover:bg-muted lg:hidden"
          >
            <X className="size-4" />
          </button>
        </div>

        {/* 章节列表 */}
        <div className="flex-1 space-y-1 overflow-y-auto p-2.5">
          {chapters.map((chapter) => (
            <button
              key={chapter.id}
              onClick={() => {
                onSelectChapter(chapter.id);
                onToggle();
              }}
              className={cn(
                "w-full cursor-pointer rounded-xl px-3 py-2.5 text-left text-sm transition-colors",
                "hover:bg-white/70",
                currentChapterId === chapter.id
                  ? "bg-foreground text-background font-medium shadow-sm"
                  : "text-muted-foreground"
              )}
            >
              <div className="line-clamp-1">{chapter.title}</div>
              <div className={cn("mt-1 text-xs", currentChapterId === chapter.id ? "text-background/55" : "text-muted-foreground")}>
                {chapter.word_count.toLocaleString()} {"字"}
              </div>
            </button>
          ))}
        </div>
      </aside>
    </>
  );
}
