"use client";

import React, { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import type { Chapter } from "@/lib/api";
import { BookOpenText, ChevronLeft, ChevronRight, X } from "lucide-react";

interface ChapterSidebarProps {
  chapters: Chapter[];
  currentChapterId: number;
  onSelectChapter: (chapterId: number) => void;
  isOpen: boolean;
  onToggle: () => void;
}

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function ChapterSidebar({
  chapters,
  currentChapterId,
  onSelectChapter,
  isOpen,
  onToggle,
}: ChapterSidebarProps) {
  const activeRef = useRef<HTMLButtonElement | null>(null);

  // 打开时滚动到当前章节；reduced-motion 使用 auto
  useEffect(() => {
    if (!isOpen) return;
    activeRef.current?.scrollIntoView({
      block: "center",
      behavior: prefersReducedMotion() ? "auto" : "smooth",
    });
  }, [isOpen, currentChapterId, chapters.length]);

  return (
    <>
      {/* 移动端遮罩 */}
      {isOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/30 transition-[opacity] motion-duration-spatial motion-ease-enter lg:hidden"
          onClick={onToggle}
          aria-hidden="true"
        />
      )}

      {/* 桌面收起时的展开条 */}
      {!isOpen && (
        <button
          type="button"
          onClick={onToggle}
          className="mb-0 hidden h-full w-10 shrink-0 flex-col items-center justify-center gap-2 rounded-[20px] border border-border/70 bg-card/80 text-muted-foreground shadow-sm transition-[background-color,color,border-color] motion-duration-fast motion-ease-enter hover:bg-card hover:text-foreground lg:flex"
          title="展开目录"
        >
          <ChevronRight className="size-4" />
          <span
            className="text-[11px] tracking-widest"
            style={{ writingMode: "vertical-rl" }}
          >
            目录
          </span>
        </button>
      )}

      {/* 侧边栏：桌面也可收起；spatial transform/opacity only */}
      <aside
        className={cn(
          "z-50 flex flex-col border-border/70 bg-sidebar/95 backdrop-blur-xl transition-[opacity,transform] motion-duration-spatial motion-ease-enter",
          // 移动：抽屉
          "fixed inset-y-0 left-0 w-72 border-r",
          isOpen ? "translate-x-0 opacity-100" : "pointer-events-none -translate-x-full opacity-0 motion-ease-exit",
          // 桌面：静态栏，可收起宽度（保留占位语义，关闭时 lg:hidden 与业务一致）
          "lg:static lg:mr-4 lg:rounded-[28px] lg:border lg:border-border/70",
          isOpen ? "lg:w-64 lg:translate-x-0 lg:opacity-100 lg:pointer-events-auto" : "lg:hidden"
        )}
      >
        <div className="flex items-center justify-between border-b border-border/70 p-4">
          <h3 className="flex items-center gap-2 font-serif font-semibold">
            <BookOpenText className="size-4 text-primary" />
            章节目录
          </h3>
          <button
            type="button"
            onClick={onToggle}
            className="cursor-pointer rounded-lg p-1.5 transition-[background-color] motion-duration-fast motion-ease-enter hover:bg-muted"
            title="收起目录"
          >
            <X className="size-4 lg:hidden" />
            <ChevronLeft className="hidden size-4 lg:block" />
          </button>
        </div>

        <div className="flex-1 space-y-1 overflow-y-auto p-2.5">
          {chapters.map((chapter) => {
            const active = currentChapterId === chapter.id;
            return (
              <button
                key={chapter.id}
                ref={active ? activeRef : undefined}
                type="button"
                onClick={() => {
                  onSelectChapter(chapter.id);
                  // 移动端选章后收起；桌面保持打开便于连读
                  if (typeof window !== "undefined" && window.innerWidth < 1024) {
                    onToggle();
                  }
                }}
                className={cn(
                  "w-full cursor-pointer rounded-xl px-3 py-2.5 text-left text-sm transition-[background-color,color,box-shadow] motion-duration-fast motion-ease-enter",
                  "hover:bg-card/70",
                  active
                    ? "bg-foreground font-medium text-background shadow-sm"
                    : "text-muted-foreground"
                )}
              >
                <div className="line-clamp-1">{chapter.title}</div>
                <div
                  className={cn(
                    "mt-1 text-xs",
                    active ? "text-background/55" : "text-muted-foreground"
                  )}
                >
                  {chapter.word_count.toLocaleString()} 字
                </div>
              </button>
            );
          })}
        </div>
      </aside>
    </>
  );
}
