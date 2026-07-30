"use client";

import React, { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";
import type { ChapterSummary } from "@/lib/api";
import { BookOpenText, ChevronLeft, ChevronRight, X } from "lucide-react";

interface ChapterSidebarProps {
  chapters: ChapterSummary[];
  currentChapterId: number;
  onSelectChapter: (chapterId: number) => void;
  isOpen: boolean;
  onToggle: () => void;
  /** 沉浸模式：始终以抽屉形态呈现（含遮罩），不用桌面内嵌栏/收起轨道 */
  forceDrawer?: boolean;
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
  forceDrawer = false,
}: ChapterSidebarProps) {
  const activeRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    activeRef.current?.scrollIntoView({
      block: "center",
      behavior: prefersReducedMotion() ? "auto" : "smooth",
    });
  }, [isOpen, currentChapterId, chapters.length]);

  return (
    <>
      {/* Mobile backdrop（forceDrawer 时全断点显示） */}
      <div
        className={cn(
          "fixed inset-0 z-40 bg-black/30 transition-[opacity] motion-duration-spatial",
          !forceDrawer && "xl:hidden",
          isOpen
            ? "pointer-events-auto opacity-100 motion-ease-enter"
            : "pointer-events-none opacity-0 motion-ease-exit",
        )}
        onClick={onToggle}
        aria-hidden="true"
      />

      {/*
        Single aside for mobile drawer + desktop width rail.
        Desktop animates width (not display:none) so collapse/expand is smooth.
        forceDrawer（沉浸模式）：保持抽屉形态，不转为桌面内嵌栏。
      */}
      <aside
        data-testid="chapter-sidebar"
        data-open={isOpen ? "true" : "false"}
        className={cn(
          "relative z-50 flex shrink-0 flex-col overflow-hidden border-border/70 bg-sidebar/95 backdrop-blur-xl",
          // Mobile/drawer: fixed drawer slides
          "fixed inset-y-0 left-0 w-72 border-r transition-[transform,opacity] motion-duration-spatial",
          isOpen
            ? "translate-x-0 opacity-100 motion-ease-enter"
            : "pointer-events-none -translate-x-full opacity-0 motion-ease-exit",
          !forceDrawer &&
            "xl:pointer-events-auto xl:static xl:z-auto xl:mr-4 xl:h-full xl:translate-x-0 xl:rounded-[28px] xl:border xl:border-border/70 xl:opacity-100",
          !forceDrawer &&
            "xl:transition-[width] xl:motion-duration-spatial xl:motion-ease-enter",
          !forceDrawer && (isOpen ? "xl:w-64" : "xl:w-10"),
        )}
      >
        {/* Expanded body — always in DOM; clipped when rail width */}
        <div
          className={cn(
            "flex h-full min-h-0 w-72 flex-col transition-[opacity,transform] motion-duration-spatial",
            isOpen
              ? "opacity-100 motion-ease-enter xl:w-64"
              : "pointer-events-none opacity-0 motion-ease-exit xl:absolute xl:inset-0",
          )}
        >
          <div className="flex shrink-0 items-center justify-between border-b border-border/70 p-4">
            <h3 className="flex items-center gap-2 font-serif font-semibold">
              <BookOpenText className="size-4 text-primary" />
              章节目录
            </h3>
            <button
              type="button"
              onClick={onToggle}
              className="cursor-pointer rounded-lg p-1.5 transition-[background-color] motion-duration-fast motion-ease-enter hover:bg-muted"
              title="收起目录"
              aria-label="收起目录"
            >
              <X className="size-4 xl:hidden" />
              <ChevronLeft className="hidden size-4 xl:block" />
            </button>
          </div>

          <div className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2.5">
            {chapters.map((chapter) => {
              const active = currentChapterId === chapter.id;
              return (
                <button
                  key={chapter.id}
                  ref={active ? activeRef : undefined}
                  type="button"
                  onClick={() => {
                    onSelectChapter(chapter.id);
                    if (
                      forceDrawer ||
                      (typeof window !== "undefined" && window.innerWidth < 1024)
                    ) {
                      onToggle();
                    }
                  }}
                  className={cn(
                    "w-full cursor-pointer rounded-xl px-3 py-2.5 text-left text-sm transition-[background-color,color,box-shadow] motion-duration-fast motion-ease-enter",
                    "hover:bg-card/70",
                    active
                      ? "bg-foreground font-medium text-background shadow-sm"
                      : "text-muted-foreground",
                  )}
                >
                  <div className="line-clamp-1">{chapter.title}</div>
                  <div
                    className={cn(
                      "mt-1 text-xs",
                      active ? "text-background/55" : "text-muted-foreground",
                    )}
                  >
                    {chapter.word_count.toLocaleString()} 字
                  </div>
                </button>
              );
            })}
          </div>
        </div>

        {/* Desktop rail when collapsed — fades in as width settles */}
        <button
          type="button"
          onClick={onToggle}
          title="展开目录"
          aria-label="展开目录"
          className={cn(
            "absolute inset-0 flex-col items-center justify-center gap-2 text-muted-foreground transition-[opacity,background-color,color] motion-duration-standard motion-ease-enter hover:bg-card hover:text-foreground",
            forceDrawer ? "hidden" : "hidden xl:flex",
            isOpen
              ? "pointer-events-none opacity-0"
              : "pointer-events-auto opacity-100",
          )}
        >
          <ChevronRight className="size-4" />
          <span
            className="text-[11px] tracking-widest"
            style={{ writingMode: "vertical-rl" }}
          >
            目录
          </span>
        </button>
      </aside>
    </>
  );
}
