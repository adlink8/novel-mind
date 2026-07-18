"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Chapter } from "@/lib/api";
import type { SelectionCoordinate } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { AlertTriangle, ChevronLeft, ChevronRight, MessageSquareText } from "lucide-react";
import type { ReaderMode } from "@/components/reader/reader-preferences";
import {
  buildSelectionPayload,
  captureSelectionFromRange,
  codePointSlice,
  codePointToUtf16Index,
  splitPagesWithBases,
  type ChapterSelectionCoords,
} from "@/lib/reader-selection";

interface ReaderContentProps {
  chapter: Chapter | null;
  /** 本章滚动/翻页进度 0-100 */
  onChapterProgress?: (percent: number) => void;
  scrollContainerRef?: React.RefObject<HTMLElement | null>;
  /** 本章最后一页再点「下一页」时进入下一章 */
  onNextChapter?: () => void;
  /** 本章第一页再点「上一页」时进入上一章 */
  onPrevChapter?: () => void;
  hasNextChapter?: boolean;
  hasPrevChapter?: boolean;
  /** Fired when user activates ask-AI on a captured selection. */
  onAskSelection?: (payload: SelectionCoordinate) => void;
  /** Highlight a code-point range within the current chapter (citation jump). */
  highlightRange?: { sourceStart: number; sourceEnd: number } | null;
  /** 分页阅读或整章长页阅读。 */
  readingMode?: ReaderMode;
}

const PAGE_CHARS = 3500;
const WARN_CHARS = 20_000;

/** 强制滚到顶部；instant 避免 smooth 与换页内容切换打架 */
function scrollToTop(
  el: HTMLElement | null | undefined,
  behavior: ScrollBehavior = "auto"
) {
  if (!el) return;
  el.scrollTo({ top: 0, left: 0, behavior });
  requestAnimationFrame(() => {
    el.scrollTop = 0;
    requestAnimationFrame(() => {
      el.scrollTop = 0;
    });
  });
}

export function ReaderContent({
  chapter,
  onChapterProgress,
  scrollContainerRef,
  onNextChapter,
  onPrevChapter,
  hasNextChapter = false,
  hasPrevChapter = false,
  onAskSelection,
  highlightRange = null,
  readingMode = "paged",
}: ReaderContentProps) {
  const [pageIndex, setPageIndex] = useState(0);
  const contentRef = useRef<HTMLDivElement>(null);
  const pageTextRef = useRef<HTMLDivElement>(null);
  const chapterId = chapter?.id;
  const [captured, setCaptured] = useState<{
    coords: ChapterSelectionCoords;
    anchor: { top: number; left: number };
  } | null>(null);

  const pages = useMemo(
    () => splitPagesWithBases(chapter?.content || "", PAGE_CHARS),
    [chapter?.content]
  );
  const isScrollMode = readingMode === "scroll";
  const displayPages = useMemo(
    () =>
      isScrollMode
        ? [{ text: chapter?.content || "", sourceStartUtf16: 0 }]
        : pages,
    [chapter?.content, isScrollMode, pages]
  );

  // Citation highlight: jump to the page containing the range
  useEffect(() => {
    if (!highlightRange || !chapter?.content || isScrollMode) return;
    const utf16Start = codePointToUtf16Index(
      chapter.content,
      highlightRange.sourceStart
    );
    const idx = pages.findIndex((p, i) => {
      const next = pages[i + 1];
      const end = next ? next.sourceStartUtf16 : p.sourceStartUtf16 + p.text.length;
      return utf16Start >= p.sourceStartUtf16 && utf16Start < end;
    });
    if (idx >= 0 && idx !== pageIndex) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- citation navigation
      setPageIndex(idx);
    }
  }, [highlightRange, chapter?.content, pages, pageIndex, isScrollMode]);

  // 换章：页码归零 + 滚到顶
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- chapter boundary reset
    setPageIndex(0);
    setCaptured(null);
    onChapterProgress?.(0);
    scrollToTop(scrollContainerRef?.current, "auto");
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only on chapter change
  }, [chapterId]);

  useEffect(() => {
    scrollToTop(scrollContainerRef?.current, "auto");
  }, [pageIndex, chapterId, scrollContainerRef, readingMode]);

  useEffect(() => {
    if (!chapterId || (!isScrollMode && pages.length > 1)) return;
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
  }, [chapterId, pages.length, scrollContainerRef, onChapterProgress, isScrollMode]);

  useEffect(() => {
    if (!chapterId || isScrollMode || pages.length <= 1) return;
    const pct = ((pageIndex + 1) / pages.length) * 100;
    onChapterProgress?.(pct);
  }, [pageIndex, pages.length, chapterId, onChapterProgress, isScrollMode]);

  const clearCaptured = useCallback(() => {
    setCaptured(null);
  }, []);

  const handleSelectionChange = useCallback(() => {
    if (!chapter || !pageTextRef.current || !onAskSelection) return;
    const sel = window.getSelection();
    // Selection cleared / collapsed → hide floating 「问 AI」 immediately.
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) {
      setCaptured(null);
      return;
    }
    const range = sel.getRangeAt(0);
    if (!pageTextRef.current.contains(range.commonAncestorContainer)) {
      setCaptured(null);
      return;
    }

    const page = displayPages[Math.min(pageIndex, Math.max(displayPages.length - 1, 0))];
    const base = page?.sourceStartUtf16 ?? 0;
    const coords = captureSelectionFromRange(
      pageTextRef.current,
      range,
      base,
      chapter.content || ""
    );
    if (!coords) {
      setCaptured(null);
      return;
    }

    const rect = range.getBoundingClientRect();
    const host = contentRef.current?.getBoundingClientRect();
    const top = host ? rect.bottom - host.top + 8 : rect.bottom;
    const left = host
      ? Math.min(Math.max(rect.left - host.left, 8), host.width - 120)
      : rect.left;

    // Capture immutable coords before native selection can collapse (mobile/menus).
    setCaptured({ coords, anchor: { top, left } });
  }, [chapter, onAskSelection, pageIndex, displayPages]);

  useEffect(() => {
    if (!onAskSelection) return;
    document.addEventListener("selectionchange", handleSelectionChange);
    // mouseup/touchend catch selection finalization on some browsers
    document.addEventListener("mouseup", handleSelectionChange);
    document.addEventListener("touchend", handleSelectionChange);
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") clearCaptured();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("selectionchange", handleSelectionChange);
      document.removeEventListener("mouseup", handleSelectionChange);
      document.removeEventListener("touchend", handleSelectionChange);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [handleSelectionChange, onAskSelection, clearCaptured]);

  // Page flip / chapter change drops the floating action (stale anchors).
  useEffect(() => {
    clearCaptured();
  }, [pageIndex, chapter?.id, clearCaptured]);

  const handleAsk = async () => {
    if (!chapter || !captured || !onAskSelection) return;
    const payload = await buildSelectionPayload(
      chapter.id,
      chapter.content || "",
      captured.coords
    );
    onAskSelection(payload);
    setCaptured(null);
    window.getSelection()?.removeAllRanges();
  };

  const safeIndex = Math.min(pageIndex, Math.max(displayPages.length - 1, 0));
  const page = displayPages[safeIndex] || { text: "", sourceStartUtf16: 0 };
  const pageText = page.text;

  // Render page text with optional highlight overlay spans (hooks before early return)
  const chapterContent = chapter?.content ?? "";
  const pageBaseUtf16 = page.sourceStartUtf16;
  let renderedPage: React.ReactNode = pageText;
  if (chapterContent && highlightRange) {
    const prefix = chapterContent.slice(0, pageBaseUtf16);
    const pageCpStart = Array.from(prefix).length;
    const pageCpEnd = pageCpStart + Array.from(pageText).length;
    const hs = highlightRange.sourceStart;
    const he = highlightRange.sourceEnd;
    if (he > pageCpStart && hs < pageCpEnd) {
      const localStart = Math.max(0, hs - pageCpStart);
      const localEnd = Math.min(Array.from(pageText).length, he - pageCpStart);
      const chars = Array.from(pageText);
      const before = chars.slice(0, localStart).join("");
      const mid = chars.slice(localStart, localEnd).join("");
      const after = chars.slice(localEnd).join("");
      renderedPage = (
        <>
          {before}
          <mark
            data-testid="reader-citation-highlight"
            data-source-start={hs}
            className="rounded bg-amber-200/80 px-0.5 text-inherit"
          >
            {mid}
          </mark>
          {after}
        </>
      );
    }
  }

  if (!chapter) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground">
        选择章节开始阅读
      </div>
    );
  }

  const totalChars = chapter.content?.length || chapter.word_count || 0;
  const isHuge = totalChars > WARN_CHARS;
  const atFirstPage = safeIndex <= 0;
  const atLastPage = safeIndex >= pages.length - 1;

  function goPrevPage() {
    if (!atFirstPage) {
      setPageIndex((i) => Math.max(0, i - 1));
      return;
    }
    if (hasPrevChapter && onPrevChapter) {
      onPrevChapter();
    }
  }

  function goNextPage() {
    if (!atLastPage) {
      setPageIndex((i) => Math.min(pages.length - 1, i + 1));
      return;
    }
    if (hasNextChapter && onNextChapter) {
      onNextChapter();
    }
  }

  // Expose exact page base for tests / debugging
  const pageMeta = {
    sourceStartUtf16: page.sourceStartUtf16,
  };

  return (
    <article
      ref={contentRef}
      className="relative mx-auto max-w-[760px] px-5 py-10 sm:px-8 sm:py-14"
      data-page-source-start-utf16={pageMeta.sourceStartUtf16}
    >
      <header className="relative mb-8 text-center sm:mb-10">
        <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-primary">
          Chapter
        </p>
        <h1 className="mt-3 font-serif text-3xl font-semibold leading-tight tracking-tight text-foreground sm:text-4xl">
          {chapter.title}
        </h1>
        <p className="mt-3 text-xs text-muted-foreground">
          {totalChars.toLocaleString()} 字
          {!isScrollMode && pages.length > 1
            ? ` · 第 ${safeIndex + 1}/${pages.length} 页`
            : isScrollMode
              ? " · 长页模式"
              : null}
        </p>
        <div aria-hidden className="mt-6 flex items-center justify-center gap-3">
          <span className="h-px w-16 bg-gradient-to-r from-transparent to-[#d6ab54]/50" />
          <span className="font-serif text-sm text-[#d6ab54]/80">❦</span>
          <span className="h-px w-16 bg-gradient-to-l from-transparent to-[#d6ab54]/50" />
        </div>
      </header>

      {isHuge && !isScrollMode && (
        <div className="relative mb-6 flex items-start gap-2 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" />
          <div>
            <p className="font-medium">本章体量较大，已自动分页显示</p>
            <p className="mt-1 text-xs text-amber-800/80">
              底部进度条表示「本章」阅读进度，不是整本书进度。翻页会回到顶部。
            </p>
          </div>
        </div>
      )}

      {/* key 强制页内容节点重建；单一 text 节点便于选区映射 */}
      <div
        key={`${chapter.id}-${safeIndex}`}
        ref={pageTextRef}
        data-testid="reader-page-text"
        data-source-start-utf16={page.sourceStartUtf16}
        className="relative whitespace-pre-wrap font-reading text-[18px] leading-[2.1] tracking-[0.02em] text-foreground/90 sm:text-[19px]"
      >
        {renderedPage}
      </div>

      {captured && onAskSelection ? (
        <div
          data-testid="reader-selection-action"
          className="absolute z-30"
          style={{ top: captured.anchor.top, left: captured.anchor.left }}
        >
          <Button
            type="button"
            size="sm"
            className="shadow-md"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => void handleAsk()}
          >
            <MessageSquareText className="size-3.5" />
            问 AI
          </Button>
        </div>
      ) : null}

      {!isScrollMode && pages.length > 1 && (
        <div className="relative mt-12 flex items-center justify-between gap-3 border-t border-border/50 pt-6">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="rounded-full px-4"
            disabled={atFirstPage && !hasPrevChapter}
            onClick={goPrevPage}
          >
            <ChevronLeft className="size-4" />
            {atFirstPage && hasPrevChapter ? "上一章" : "上一页"}
          </Button>
          <span className="font-serif text-xs tracking-[0.2em] text-muted-foreground">
            {safeIndex + 1} / {pages.length}
          </span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="rounded-full px-4"
            disabled={atLastPage && !hasNextChapter}
            onClick={goNextPage}
          >
            {atLastPage && hasNextChapter ? "下一章" : "下一页"}
            <ChevronRight className="size-4" />
          </Button>
        </div>
      )}
    </article>
  );
}

/** Helper exported for tests: exact code-point excerpt for a highlight. */
export function excerptAt(
  content: string,
  sourceStart: number,
  sourceEnd: number
): string {
  return codePointSlice(content, sourceStart, sourceEnd);
}
