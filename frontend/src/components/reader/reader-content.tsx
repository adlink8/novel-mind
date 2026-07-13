"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import type { Chapter } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { AlertTriangle, ChevronLeft, ChevronRight } from "lucide-react";

interface ReaderContentProps {
  chapter: Chapter | null;
  /** 本章滚动/翻页进度 0-100 */
  onChapterProgress?: (percent: number) => void;
  scrollContainerRef?: React.RefObject<HTMLElement | null>;
}

const PAGE_CHARS = 3500;
const WARN_CHARS = 20_000;

function splitPages(text: string, size: number): string[] {
  if (!text) return [""];
  if (text.length <= size) return [text];
  const pages: string[] = [];
  let start = 0;
  while (start < text.length) {
    let end = Math.min(start + size, text.length);
    if (end < text.length) {
      const window = text.slice(start, end);
      const breakAt = Math.max(window.lastIndexOf("\n\n"), window.lastIndexOf("\n"));
      if (breakAt > size * 0.4) {
        end = start + breakAt + 1;
      }
    }
    pages.push(text.slice(start, end));
    start = end;
  }
  return pages;
}

export function ReaderContent({
  chapter,
  onChapterProgress,
  scrollContainerRef,
}: ReaderContentProps) {
  const [pageIndex, setPageIndex] = useState(0);
  const contentRef = useRef<HTMLDivElement>(null);

  const pages = useMemo(
    () => splitPages(chapter?.content || "", PAGE_CHARS),
    [chapter?.id, chapter?.content]
  );

  // 换章时重置页码（异步调度，避免 effect 同步 setState）
  useEffect(() => {
    const t = window.setTimeout(() => {
      setPageIndex(0);
      onChapterProgress?.(0);
    }, 0);
    return () => window.clearTimeout(t);
  }, [chapter?.id, onChapterProgress]);

  // 单页模式：根据滚动位置计算本章进度
  useEffect(() => {
    if (!chapter || pages.length > 1) return;
    const el = scrollContainerRef?.current;
    if (!el) return;

    const onScroll = () => {
      const max = el.scrollHeight - el.clientHeight;
      const pct = max <= 0 ? 100 : (el.scrollTop / max) * 100;
      onChapterProgress?.(Math.min(100, Math.max(0, pct)));
    };
    onScroll();
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [chapter?.id, pages.length, scrollContainerRef, onChapterProgress]);

  // 分页模式：页码即进度
  useEffect(() => {
    if (!chapter || pages.length <= 1) return;
    const pct = ((pageIndex + 1) / pages.length) * 100;
    onChapterProgress?.(pct);
  }, [pageIndex, pages.length, chapter?.id, onChapterProgress]);

  if (!chapter) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        选择章节开始阅读
      </div>
    );
  }

  const totalChars = chapter.content?.length || chapter.word_count || 0;
  const isHuge = totalChars > WARN_CHARS;
  const safeIndex = Math.min(pageIndex, Math.max(pages.length - 1, 0));
  const pageText = pages[safeIndex] || "";

  return (
    <article ref={contentRef} className="mx-auto max-w-[760px] px-5 py-10 sm:px-8 sm:py-14">
      <header className="mb-8 border-b border-border/60 pb-8 text-center sm:mb-10">
        <p className="mb-3 text-[11px] font-semibold uppercase tracking-[0.22em] text-primary">
          Chapter
        </p>
        <h1 className="mb-3 font-serif text-3xl font-semibold leading-tight tracking-tight sm:text-4xl">
          {chapter.title}
        </h1>
        <p className="text-xs text-muted-foreground">
          {totalChars.toLocaleString()} 字
          {pages.length > 1 ? ` · 第 ${safeIndex + 1}/${pages.length} 页` : null}
        </p>
      </header>

      {isHuge && (
        <div className="mb-6 flex items-start gap-2 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <div>
            <p className="font-medium">本章体量较大，已自动分页显示</p>
            <p className="mt-1 text-xs text-amber-800/80">
              底部进度条表示「本章」阅读进度，不是整本书进度。
            </p>
          </div>
        </div>
      )}

      <div className="whitespace-pre-wrap font-reading text-[17px] leading-[2.05] tracking-[0.015em] text-foreground/90 sm:text-[18px]">
        {pageText}
      </div>

      {pages.length > 1 && (
        <div className="mt-10 flex items-center justify-between gap-3 border-t border-border/60 pt-6">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={safeIndex <= 0}
            onClick={() => {
              setPageIndex((i) => Math.max(0, i - 1));
              scrollContainerRef?.current?.scrollTo({ top: 0, behavior: "smooth" });
            }}
          >
            <ChevronLeft className="size-4" />
            上一页
          </Button>
          <span className="text-xs text-muted-foreground">
            {safeIndex + 1} / {pages.length}
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={safeIndex >= pages.length - 1}
            onClick={() => {
              setPageIndex((i) => Math.min(pages.length - 1, i + 1));
              scrollContainerRef?.current?.scrollTo({ top: 0, behavior: "smooth" });
            }}
          >
            下一页
            <ChevronRight className="size-4" />
          </Button>
        </div>
      )}
    </article>
  );
}
