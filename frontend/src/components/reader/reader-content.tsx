"use client";

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Chapter } from "@/lib/api";
import type { SelectionCoordinate } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  AlertTriangle,
  BookmarkPlus,
  ChevronLeft,
  ChevronRight,
  ImagePlus,
  MessageSquareText,
} from "lucide-react";
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
  /** 滚动模式的完整章节正文，按 chapter_number 顺序排列。 */
  chapters?: Chapter[];
  activeChapterId?: number;
  /** 本章滚动/翻页进度 0-100 */
  onChapterProgress?: (percent: number, chapterId?: number) => void;
  scrollContainerRef?: React.RefObject<HTMLElement | null>;
  /** 本章最后一页再点「下一页」时进入下一章 */
  onNextChapter?: () => void;
  /** 本章第一页再点「上一页」时进入上一章 */
  onPrevChapter?: () => void;
  hasNextChapter?: boolean;
  hasPrevChapter?: boolean;
  /** Fired when user activates ask-AI on a captured selection. */
  onAskSelection?: (payload: SelectionCoordinate) => void;
  /** Fired when user activates image generation on a captured selection. */
  onImageSelection?: (payload: SelectionCoordinate) => void;
  /** Fired when user saves a captured selection as a persistent bookmark. */
  onBookmarkSelection?: (payload: SelectionCoordinate) => void | Promise<void>;
  /** Highlight a code-point range within the current chapter (citation jump). */
  highlightRange?: { sourceStart: number; sourceEnd: number } | null;
  highlightChapterId?: number | null;
  /** 分页阅读或整章长页阅读。 */
  readingMode?: ReaderMode;
  /** 进入本章时要恢复的章内进度 0-100（0 = 从头开始） */
  initialProgress?: number;
  /** 正文字号 px */
  fontSize?: number;
  /** 正文行距（倍数） */
  lineHeight?: number;
  /** 正文最大行宽 px */
  contentWidth?: number;
}

const PAGE_CHARS = 3500;
const WARN_CHARS = 20_000;
const SCROLL_ADVANCE_THRESHOLD_PX = 64;
const EMPTY_CHAPTERS: Chapter[] = [];

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
  chapters = EMPTY_CHAPTERS,
  activeChapterId,
  onChapterProgress,
  scrollContainerRef,
  onNextChapter,
  onPrevChapter,
  hasNextChapter = false,
  hasPrevChapter = false,
  onAskSelection,
  onImageSelection,
  onBookmarkSelection,
  highlightRange = null,
  highlightChapterId = null,
  readingMode = "paged",
  initialProgress = 0,
  fontSize = 18,
  lineHeight = 2.1,
  contentWidth = 760,
}: ReaderContentProps) {
  const [pageIndex, setPageIndex] = useState(0);
  const contentRef = useRef<HTMLDivElement>(null);
  const pageTextRef = useRef<HTMLDivElement>(null);
  /** 章内进度恢复进行中：屏蔽滚动上报与回顶，避免 0% 覆盖存档 */
  const restoreRef = useRef<{ percent: number } | null>(null);
  /** 防止滚动到底部时重复触发同一章的自动换章。 */
  const autoAdvanceChapterRef = useRef<number | null>(null);
  /** 防止回到顶部时重复触发同一章的自动换章。 */
  const autoRetreatChapterRef = useRef<number | null>(null);
  const previousScrollTopRef = useRef<number | null>(null);
  /** 滚动上报节流：避免 setChapterPercent 每秒触发 60 次导致卡顿 */
  const lastScrollReportRef = useRef(0);
  const SCROLL_THROTTLE_MS = 150;
  const chapterId = activeChapterId ?? chapter?.id;
  const [captured, setCaptured] = useState<{
    coords: ChapterSelectionCoords;
    anchor: { top: number; left: number };
    chapterId: number;
    chapterContent: string;
  } | null>(null);
  const [bookmarkState, setBookmarkState] = useState<"idle" | "saving" | "saved" | "error">("idle");

  const pages = useMemo(
    () => splitPagesWithBases(chapter?.content || "", PAGE_CHARS),
    [chapter?.content]
  );
  const isScrollMode = readingMode === "scroll";
  const scrollChapters = isScrollMode ? chapters : EMPTY_CHAPTERS;
  const isMultiChapterScroll = scrollChapters.length > 0;
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

  useEffect(() => {
    if (!highlightRange || !chapter?.content || !isScrollMode) return;
    const frame = requestAnimationFrame(() => {
      const selector = highlightChapterId
        ? `[data-reader-chapter-id="${highlightChapterId}"] [data-testid="reader-citation-highlight"]`
        : '[data-testid="reader-citation-highlight"]';
      document
        .querySelector<HTMLElement>(selector)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
    return () => cancelAnimationFrame(frame);
  }, [highlightChapterId, highlightRange, chapter?.content, isScrollMode]);

  // 换章：有存档则恢复章内位置，否则回顶从头开始
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- chapter boundary reset
    setCaptured(null);
    if (isMultiChapterScroll) return;
    autoAdvanceChapterRef.current = null;
    autoRetreatChapterRef.current = null;
    previousScrollTopRef.current = null;
    const target = Math.min(100, Math.max(0, initialProgress || 0));
    // 长页（或单页短章）按滚动百分比恢复；多页翻页模式按页序恢复
    const restoreByScroll = target > 0 && (isScrollMode || pages.length <= 1);

    if (restoreByScroll) {
      setPageIndex(0);
      restoreRef.current = { percent: target };
      const applyRestore = () => {
        const el = scrollContainerRef?.current;
        if (!el) return;
        const max = el.scrollHeight - el.clientHeight;
        if (max > 0) el.scrollTop = (target / 100) * max;
      };
        requestAnimationFrame(() => {
          applyRestore();
          requestAnimationFrame(() => {
            applyRestore();
            previousScrollTopRef.current = scrollContainerRef?.current?.scrollTop ?? null;
            restoreRef.current = null;
            onChapterProgress?.(target);
          });
      });
      return;
    }

    if (target > 0 && pages.length > 1) {
      // 翻页进度由页序推导：percent = (pageIndex + 1) / pages.length
      const restoredPage = Math.min(
        pages.length - 1,
        Math.max(0, Math.ceil((target / 100) * pages.length) - 1)
      );
      setPageIndex(restoredPage);
    } else {
      setPageIndex(0);
      onChapterProgress?.(target);
    }
    scrollToTop(scrollContainerRef?.current, "auto");
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only on chapter change
  }, [chapterId, isMultiChapterScroll]);

  useEffect(() => {
    // 恢复章内进度期间不回顶（恢复完成后再恢复正常行为）
    if (restoreRef.current || isMultiChapterScroll) return;
    scrollToTop(scrollContainerRef?.current, "auto");
  }, [pageIndex, chapterId, isMultiChapterScroll, scrollContainerRef, readingMode]);

  useEffect(() => {
    const el = scrollContainerRef?.current;
    if (!el) return;

    if (isMultiChapterScroll) {
      let progressTimer: number | null = null;
      let latestProgress: { percent: number; chapterId: number } | null = null;
      const reportProgress = (percent: number, reportChapterId: number) => {
        if (progressTimer === null) {
          onChapterProgress?.(percent, reportChapterId);
          progressTimer = window.setTimeout(() => {
            progressTimer = null;
            const latest = latestProgress;
            latestProgress = null;
            if (latest) onChapterProgress?.(latest.percent, latest.chapterId);
          }, 200);
        } else {
          latestProgress = { percent, chapterId: reportChapterId };
        }
      };
      const reportMultiChapterScroll = () => {
        const containerTop = el.getBoundingClientRect().top;
        let activeChapter = scrollChapters[0];
        let activeElement: HTMLElement | null = null;

        for (const candidate of scrollChapters) {
          const candidateElement = el.querySelector<HTMLElement>(
            `[data-reader-chapter-id="${candidate.id}"]`
          );
          if (!candidateElement) continue;
          const top = candidateElement.getBoundingClientRect().top - containerTop;
          if (top <= 80) {
            activeChapter = candidate;
            activeElement = candidateElement;
          } else {
            break;
          }
        }

        if (!activeElement) {
          activeElement = el.querySelector<HTMLElement>(
            `[data-reader-chapter-id="${activeChapter.id}"]`
          );
        }
        if (!activeElement) return;

        const chapterTop =
          activeElement.getBoundingClientRect().top - containerTop + el.scrollTop;
        const chapterMax = Math.max(
          0,
          activeElement.offsetHeight - el.clientHeight
        );
        const localScroll = Math.max(0, el.scrollTop - chapterTop);
        const percent =
          chapterMax <= 0 ? 100 : Math.min(100, (localScroll / chapterMax) * 100);
        reportProgress(percent, activeChapter.id);
      };

      const onScroll = () => {
        const now = Date.now();
        if (now - lastScrollReportRef.current < SCROLL_THROTTLE_MS) return;
        lastScrollReportRef.current = now;
        reportMultiChapterScroll();
      };
      el.addEventListener("scroll", onScroll, { passive: true });
      return () => {
        el.removeEventListener("scroll", onScroll);
        if (progressTimer !== null) window.clearTimeout(progressTimer);
      };
    }

    if (!chapterId || (!isScrollMode && pages.length > 1)) return;

    let progressTimer: number | null = null;
    let latestProgress: { percent: number; chapterId: number } | null = null;
    const reportProgress = (percent: number, reportChapterId: number) => {
      if (progressTimer === null) {
        onChapterProgress?.(percent, reportChapterId);
        progressTimer = window.setTimeout(() => {
          progressTimer = null;
          const latest = latestProgress;
          latestProgress = null;
          if (latest) onChapterProgress?.(latest.percent, latest.chapterId);
        }, 200);
      } else {
        latestProgress = { percent, chapterId: reportChapterId };
      }
    };
    const reportScroll = (allowAutoAdvance: boolean) => {
      // 恢复章内进度期间不上报，避免把存档冲掉
      if (restoreRef.current) return;
      const max = el.scrollHeight - el.clientHeight;
      const pct = max <= 0 ? 100 : (el.scrollTop / max) * 100;
      const previousScrollTop = previousScrollTopRef.current;
      const scrollingUp =
        previousScrollTop !== null && el.scrollTop < previousScrollTop;
      previousScrollTopRef.current = el.scrollTop;
      reportProgress(Math.min(100, Math.max(0, pct)), chapterId);

      const shouldRetreat =
        allowAutoAdvance &&
        isScrollMode &&
        max > 0 &&
        hasPrevChapter &&
        onPrevChapter &&
        scrollingUp &&
        el.scrollTop <= SCROLL_ADVANCE_THRESHOLD_PX &&
        autoRetreatChapterRef.current !== chapterId;
      const shouldAdvance =
        allowAutoAdvance &&
        isScrollMode &&
        max > 0 &&
        hasNextChapter &&
        onNextChapter &&
        el.scrollTop + el.clientHeight >=
          el.scrollHeight - SCROLL_ADVANCE_THRESHOLD_PX &&
        autoAdvanceChapterRef.current !== chapterId;

      if (shouldRetreat) {
        autoRetreatChapterRef.current = chapterId;
        onPrevChapter();
      } else if (shouldAdvance) {
        autoAdvanceChapterRef.current = chapterId;
        onNextChapter();
      }
    };
    const onScroll = () => reportScroll(true);
    // 初始检查只上报进度，不因恢复位置或切换模式时恰好在底部而自动换章。
    reportScroll(false);
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      el.removeEventListener("scroll", onScroll);
      if (progressTimer !== null) window.clearTimeout(progressTimer);
    };
  }, [
    chapterId,
    pages.length,
    scrollContainerRef,
    onChapterProgress,
    isMultiChapterScroll,
    scrollChapters,
    isScrollMode,
    hasNextChapter,
    onNextChapter,
    hasPrevChapter,
    onPrevChapter,
  ]);

  useEffect(() => {
    if (!chapterId || isScrollMode || pages.length <= 1) return;
    const pct = ((pageIndex + 1) / pages.length) * 100;
    onChapterProgress?.(pct);
  }, [pageIndex, pages.length, chapterId, onChapterProgress, isScrollMode]);

  const clearCaptured = useCallback(() => {
    setCaptured(null);
  }, []);

  const handleSelectionChange = useCallback(() => {
    if (!onAskSelection && !onImageSelection && !onBookmarkSelection) return;
    const sel = window.getSelection();
    // Selection cleared / collapsed → hide floating 「问 AI」 immediately.
    if (!sel || sel.rangeCount === 0 || sel.isCollapsed) {
      setCaptured(null);
      return;
    }
    const range = sel.getRangeAt(0);

    let selectionChapter = chapter;
    let selectionRoot: HTMLElement | null = pageTextRef.current;
    let base = 0;
    if (isMultiChapterScroll) {
      const commonAncestor =
        range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
          ? (range.commonAncestorContainer as Element)
          : range.commonAncestorContainer.parentElement;
      const chapterHost = commonAncestor?.closest<HTMLElement>(
        "[data-reader-chapter-id]"
      );
      const selectedChapterId = Number(chapterHost?.dataset.readerChapterId);
      selectionChapter = scrollChapters.find(
        (item) => item.id === selectedChapterId
      ) ?? null;
      selectionRoot =
        chapterHost?.querySelector<HTMLElement>(
          '[data-testid="reader-page-text"]'
        ) ?? null;
    }

    if (!selectionChapter || !selectionRoot) {
      setCaptured(null);
      return;
    }
    if (!selectionRoot.contains(range.commonAncestorContainer)) {
      setCaptured(null);
      return;
    }

    if (!isMultiChapterScroll) {
      const page = displayPages[Math.min(pageIndex, Math.max(displayPages.length - 1, 0))];
      base = page?.sourceStartUtf16 ?? 0;
    }
    const coords = captureSelectionFromRange(
      selectionRoot,
      range,
      base,
      selectionChapter.content
    );
    if (!coords) {
      setCaptured(null);
      return;
    }

    const rect =
      typeof range.getBoundingClientRect === "function"
        ? range.getBoundingClientRect()
        : { bottom: 0, left: 0 };
    const host = contentRef.current?.getBoundingClientRect();
    const top = host ? rect.bottom - host.top + 8 : rect.bottom;
    const left = host
      ? Math.min(Math.max(rect.left - host.left, 8), host.width - 120)
      : rect.left;

    // Capture immutable coords before native selection can collapse (mobile/menus).
    setCaptured({
      coords,
      anchor: { top, left },
      chapterId: selectionChapter.id,
      chapterContent: selectionChapter.content,
    });
  }, [
    chapter,
    displayPages,
    isMultiChapterScroll,
    onAskSelection,
    onImageSelection,
    onBookmarkSelection,
    pageIndex,
    scrollChapters,
  ]);

  useEffect(() => {
    if (!onAskSelection && !onImageSelection && !onBookmarkSelection) return;
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
  }, [
    handleSelectionChange,
    onAskSelection,
    onImageSelection,
    onBookmarkSelection,
    clearCaptured,
  ]);

  // Page flip / chapter change drops the floating action (stale anchors).
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- drop stale selection anchor
    clearCaptured();
  }, [pageIndex, chapter?.id, activeChapterId, clearCaptured]);

  // 键盘翻页：←/→（仅翻页模式；输入控件聚焦、有选区或调宽手柄聚焦时不劫持）
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (!chapter || isScrollMode) return;
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
      const target = e.target;
      if (
        target instanceof Element &&
        target.closest(
          "input, textarea, select, [contenteditable='true'], [role='separator']"
        )
      ) {
        return;
      }
      const selection = window.getSelection();
      if (selection && !selection.isCollapsed) return;
      if (e.key === "ArrowLeft") {
        goPrevPage();
      } else {
        goNextPage();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  const handleAsk = async () => {
    if (!captured || !onAskSelection) return;
    const payload = await buildSelectionPayload(
      captured.chapterId,
      captured.chapterContent,
      captured.coords
    );
    onAskSelection(payload);
    setCaptured(null);
    window.getSelection()?.removeAllRanges();
  };

  const handleImage = async () => {
    if (!captured || !onImageSelection) return;
    const payload = await buildSelectionPayload(
      captured.chapterId,
      captured.chapterContent,
      captured.coords
    );
    onImageSelection(payload);
    setCaptured(null);
    window.getSelection()?.removeAllRanges();
  };

  const handleBookmark = async () => {
    if (!captured || !onBookmarkSelection) return;
    setBookmarkState("saving");
    try {
      const payload = await buildSelectionPayload(
        captured.chapterId,
        captured.chapterContent,
        captured.coords
      );
      await onBookmarkSelection(payload);
      setBookmarkState("saved");
      setCaptured(null);
      window.getSelection()?.removeAllRanges();
      window.setTimeout(() => setBookmarkState("idle"), 1800);
    } catch {
      setBookmarkState("error");
    }
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

  if (isMultiChapterScroll) {
    return (
      <div
        ref={contentRef}
        className="relative mx-auto w-full"
        style={{ maxWidth: contentWidth }}
        data-testid="reader-multi-chapter-content"
      >
        {scrollChapters.map((scrollChapter, index) => {
          const isHighlightedChapter =
            highlightRange && highlightChapterId === scrollChapter.id;
          let renderedScrollContent: React.ReactNode = scrollChapter.content;
          if (isHighlightedChapter) {
            const chars = Array.from(scrollChapter.content);
            const start = Math.max(0, highlightRange.sourceStart);
            const end = Math.min(chars.length, highlightRange.sourceEnd);
            if (end > start) {
              renderedScrollContent = (
                <>
                  {chars.slice(0, start).join("")}
                  <mark
                    data-testid="reader-citation-highlight"
                    data-source-start={highlightRange.sourceStart}
                    className="rounded bg-amber-200/80 px-0.5 text-inherit"
                  >
                    {chars.slice(start, end).join("")}
                  </mark>
                  {chars.slice(end).join("")}
                </>
              );
            }
          }

          return (
            <article
              key={scrollChapter.id}
              data-reader-chapter-id={scrollChapter.id}
              className="relative px-5 py-10 sm:px-8 sm:py-14"
            >
              <header className="relative mb-8 text-center sm:mb-10">
                <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-primary">
                  Chapter {index + 1}
                </p>
                <h1 className="mt-3 font-serif text-3xl font-semibold leading-tight tracking-tight text-foreground sm:text-4xl">
                  {scrollChapter.title}
                </h1>
                <p className="mt-3 text-xs text-muted-foreground">
                  {scrollChapter.content.length.toLocaleString()} 字 · 长页模式
                </p>
                <div aria-hidden className="mt-6 flex items-center justify-center gap-3">
                  <span className="h-px w-16 bg-gradient-to-r from-transparent to-[#d6ab54]/50" />
                  <span className="font-serif text-sm text-[#d6ab54]/80">❦</span>
                  <span className="h-px w-16 bg-gradient-to-l from-transparent to-[#d6ab54]/50" />
                </div>
              </header>
              <div
                data-testid="reader-page-text"
                data-source-start-utf16="0"
                className="relative whitespace-pre-wrap font-reading tracking-[0.02em] text-foreground/90"
                style={{ fontSize, lineHeight }}
              >
                {renderedScrollContent}
              </div>
            </article>
          );
        })}

        {captured && (onAskSelection || onImageSelection || onBookmarkSelection) ? (
          <div
            data-testid="reader-selection-action"
            className="absolute z-30"
            style={{ top: captured.anchor.top, left: captured.anchor.left }}
          >
            <div className="flex items-center gap-1 rounded-lg bg-background/95 p-1 shadow-md ring-1 ring-border/70">
              {onAskSelection ? (
                <Button
                  type="button"
                  size="sm"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => void handleAsk()}
                >
                  <MessageSquareText className="size-3.5" />
                  问 AI
                </Button>
              ) : null}
              {onImageSelection ? (
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => void handleImage()}
                >
                  <ImagePlus className="size-3.5" />
                  画图
                </Button>
              ) : null}
              {onBookmarkSelection ? (
                <Button
                  type="button"
                  size="icon-sm"
                  variant="ghost"
                  disabled={bookmarkState === "saving"}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => void handleBookmark()}
                  aria-label={
                    bookmarkState === "saving"
                      ? "保存书签中"
                      : bookmarkState === "saved"
                        ? "书签已保存"
                        : bookmarkState === "error"
                          ? "书签保存失败"
                          : "保存书签"
                  }
                  title={
                    bookmarkState === "saving"
                      ? "保存书签中"
                      : bookmarkState === "saved"
                        ? "书签已保存"
                        : bookmarkState === "error"
                          ? "书签保存失败"
                          : "保存书签"
                  }
                >
                  <BookmarkPlus className="size-3.5" />
                </Button>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>
    );
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
      className="relative mx-auto px-5 py-10 sm:px-8 sm:py-14"
      style={{ maxWidth: contentWidth }}
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
        className="relative whitespace-pre-wrap font-reading tracking-[0.02em] text-foreground/90"
        style={{ fontSize, lineHeight }}
      >
        {renderedPage}
      </div>

      {captured && (onAskSelection || onImageSelection || onBookmarkSelection) ? (
        <div
          data-testid="reader-selection-action"
          className="absolute z-30"
          style={{ top: captured.anchor.top, left: captured.anchor.left }}
        >
          <div className="flex items-center gap-1 rounded-lg bg-background/95 p-1 shadow-md ring-1 ring-border/70">
            {onAskSelection ? (
              <Button
                type="button"
                size="sm"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => void handleAsk()}
              >
                <MessageSquareText className="size-3.5" />
                问 AI
              </Button>
            ) : null}
            {onImageSelection ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => void handleImage()}
              >
                <ImagePlus className="size-3.5" />
                画图
              </Button>
            ) : null}
            {onBookmarkSelection ? (
              <Button
                type="button"
                size="icon-sm"
                variant="ghost"
                disabled={bookmarkState === "saving"}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => void handleBookmark()}
                aria-label={
                  bookmarkState === "saving"
                    ? "保存书签中"
                    : bookmarkState === "saved"
                      ? "书签已保存"
                      : bookmarkState === "error"
                        ? "书签保存失败"
                        : "保存书签"
                }
                title={
                  bookmarkState === "saving"
                    ? "保存书签中"
                    : bookmarkState === "saved"
                      ? "书签已保存"
                      : bookmarkState === "error"
                        ? "书签保存失败"
                        : "保存书签"
                }
              >
                <BookmarkPlus className="size-3.5" />
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}

      {!isScrollMode && (
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
