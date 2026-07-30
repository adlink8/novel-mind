"use client";

import React, { useState, useEffect, useCallback, useRef, Suspense } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { ChapterSidebar } from "@/components/reader/chapter-sidebar";
import { ReaderContent } from "@/components/reader/reader-content";
import { ReaderChatPanel } from "@/components/reader/reader-chat-panel";
import { ProgressBar } from "@/components/reader/progress-bar";
import { SearchPanel } from "@/components/reader/search-panel";
import { ReaderBookmarks } from "@/components/reader/reader-bookmarks";
import {
  loadReaderPreferences,
  ReaderPreferencesPanel,
  saveReaderPreferences,
  type ReaderPreferences,
} from "@/components/reader/reader-preferences";
import { cn } from "@/lib/utils";
import {
  novelsApi,
  type Bookmark,
  type Novel,
  type Chapter,
  type ChapterSummary,
  type SelectionCoordinate,
} from "@/lib/api";
import {
  clampReaderChatWidth,
  loadReaderChatPresentation,
  READER_CHAT_WIDTH_DEFAULT,
  saveReaderChatPresentation,
} from "@/lib/reader-selection";
import {
  ArrowLeft,
  BookOpenText,
  ChevronLeft,
  ChevronRight,
  Menu,
  MessageSquareText,
  PanelLeft,
  Search,
} from "lucide-react";
import { BookLoader } from "@/components/book-loader";

const AUTO_SCROLL_BASE_PX_PER_SECOND = 80;

function getStorageKey(novelId: string): string {
  return `novelmind:reading:${novelId}`;
}

function loadProgress(
  novelId: string
): { chapterId: number; chapterPercent?: number } | null {
  try {
    const raw = localStorage.getItem(getStorageKey(novelId));
    if (raw) return JSON.parse(raw);
  } catch {
    /* ignore */
  }
  return null;
}

function saveProgress(
  novelId: string,
  chapterId: number,
  chapterPercent: number
): void {
  try {
    localStorage.setItem(
      getStorageKey(novelId),
      JSON.stringify({
        chapterId,
        chapterPercent,
        updatedAt: Date.now(),
      })
    );
  } catch {
    /* ignore */
  }
}

function resolveChapterFromQuery(
  chapterList: ChapterSummary[],
  chapterParam: string | null
): ChapterSummary | null {
  if (!chapterParam || !chapterList.length) return null;
  const n = Number(chapterParam);
  if (!Number.isFinite(n)) return null;
  // 优先当 DB id；再当 chapter_number（第几章）
  return (
    chapterList.find((c) => c.id === n) ??
    chapterList.find((c) => c.chapter_number === n) ??
    null
  );
}

function NovelReaderInner() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const novelId = String(params.id);
  const chapterQuery = searchParams.get("chapter");
  const fromTimeline = searchParams.get("from") === "timeline";

  const [novel, setNovel] = useState<Novel | null>(null);
  const [chapters, setChapters] = useState<ChapterSummary[]>([]);
  const [chapterContents, setChapterContents] = useState<Chapter[]>([]);
  const [currentChapterId, setCurrentChapterId] = useState<number>(0);
  // 桌面（≥1280）默认展开目录，窄屏默认收起
  // 惰性初始，避免 effect 同步 setState
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    if (typeof window === "undefined") return true;
    return window.innerWidth >= 1280;
  });
  const [searchOpen, setSearchOpen] = useState(false);
  const [bookmarksOpen, setBookmarksOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [chapterPercent, setChapterPercent] = useState(0);
  /** 换章时要恢复的章内进度（0 = 从头开始；时间线定位章恒为 0） */
  const [restorePercent, setRestorePercent] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [preferences, setPreferences] = useState<ReaderPreferences>(() =>
    loadReaderPreferences()
  );
  const [preferencesOpen, setPreferencesOpen] = useState(false);
  /** 沉浸模式下的章节目录抽屉开关 */
  const [immersiveTocOpen, setImmersiveTocOpen] = useState(false);
  /** 沉浸模式控制层显隐（点按正文切换） */
  const [immersiveChrome, setImmersiveChrome] = useState(true);
  const chaptersRef = useRef<ChapterSummary[]>([]);
  const pendingScrollChapterRef = useRef<number | null>(null);
  const syncedProgressChapterRef = useRef<string | null>(null);
  /** 时间线定位模式：不写入阅读进度，避免污染「上次读到」 */
  const [progressWritable, setProgressWritable] = useState(!fromTimeline);
  const jumpedChapterIdRef = useRef<number | null>(null);

  // Phase 10 reader chat — presentation only in localStorage; truth is PostgreSQL
  const [chatOpen, setChatOpen] = useState(() => {
    if (typeof window === "undefined") return false;
    return Boolean(loadReaderChatPresentation(novelId).open);
  });
  const [chatCollapsed, setChatCollapsed] = useState(() => {
    if (typeof window === "undefined") return false;
    const saved = loadReaderChatPresentation(novelId).collapsed;
    // 无存档时，较窄桌面（<1536px）默认收成轨道：目录 + 面板同开会挤窄正文
    return saved ?? window.innerWidth < 1536;
  });
  const [chatWidthPx, setChatWidthPx] = useState(() => {
    if (typeof window === "undefined") return READER_CHAT_WIDTH_DEFAULT;
    const saved = loadReaderChatPresentation(novelId).panelWidthPx;
    return clampReaderChatWidth(saved ?? READER_CHAT_WIDTH_DEFAULT);
  });
  const chatResizeRef = useRef<{ startX: number; startW: number } | null>(null);
  const [pendingSelection, setPendingSelection] =
    useState<SelectionCoordinate | null>(null);
  const [highlightRange, setHighlightRange] = useState<{
    sourceStart: number;
    sourceEnd: number;
  } | null>(null);
  const [isDesktop, setIsDesktop] = useState(() => {
    if (typeof window === "undefined") return true;
    return window.innerWidth >= 1280;
  });

  const chapterContent =
    chapterContents.find((item) => item.id === currentChapterId) ?? null;

  useEffect(() => {
    const onResize = () => setIsDesktop(window.innerWidth >= 1280);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // Persist chat presentation width (desktop).
  useEffect(() => {
    const prev = loadReaderChatPresentation(novelId);
    saveReaderChatPresentation(novelId, {
      ...prev,
      open: chatOpen,
      collapsed: chatCollapsed,
      panelWidthPx: chatWidthPx,
    });
  }, [novelId, chatOpen, chatCollapsed, chatWidthPx]);

  const onChatResizePointerDown = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      chatResizeRef.current = { startX: e.clientX, startW: chatWidthPx };
      const target = e.currentTarget;
      target.setPointerCapture(e.pointerId);
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    },
    [chatWidthPx]
  );

  const onChatResizePointerMove = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      const drag = chatResizeRef.current;
      if (!drag) return;
      // Dragging the left handle: move left → wider panel.
      const delta = drag.startX - e.clientX;
      setChatWidthPx(clampReaderChatWidth(drag.startW + delta));
    },
    []
  );

  const onChatResizePointerUp = useCallback(
    (e: React.PointerEvent<HTMLDivElement>) => {
      if (!chatResizeRef.current) return;
      chatResizeRef.current = null;
      try {
        e.currentTarget.releasePointerCapture(e.pointerId);
      } catch {
        /* ignore */
      }
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    },
    []
  );

  useEffect(() => {
    saveReaderPreferences(preferences);
  }, [preferences]);

  useEffect(() => {
    if (
      preferences.mode !== "scroll" ||
      !preferences.autoScroll ||
      chapterContents.length === 0
    ) {
      return;
    }
    const el = scrollRef.current;
    if (!el) return;

    let frame = 0;
    let previous = performance.now();
    const tick = (now: number) => {
      const elapsed = Math.min(50, now - previous);
      previous = now;
      el.scrollTop +=
        (AUTO_SCROLL_BASE_PX_PER_SECOND * preferences.autoScrollSpeed * elapsed) /
        1000;
      const reachedEnd = el.scrollTop + el.clientHeight >= el.scrollHeight - 2;
      if (reachedEnd) {
        setPreferences((current) => ({ ...current, autoScroll: false }));
        return;
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [
    chapterContents.length,
    preferences.autoScroll,
    preferences.autoScrollSpeed,
    preferences.mode,
  ]);

  useEffect(() => {
    chaptersRef.current = chapters;
  }, [chapters]);

  useEffect(() => {
    let active = true;

    async function loadNovel() {
      try {
        setLoading(true);
        setError(null);
        const [res, chapterContentsRes] = await Promise.all([
          novelsApi.get(novelId),
          novelsApi.getChapterContents(novelId),
        ]);
        // 批量接口一次 SQL 返回所有正文；前端只在内存中拆出目录摘要。
        const chapterResponses = chapterContentsRes.data.sort(
          (a, b) => a.chapter_number - b.chapter_number || a.id - b.id
        );
        const chapterList = chapterResponses.map(
          ({ id, novel_id, chapter_number, title, word_count, summary, created_at, updated_at }) => ({
            id,
            novel_id,
            chapter_number,
            title,
            word_count,
            summary,
            created_at,
            updated_at,
          })
        );
        if (!active) return;

        setNovel(res.data);
        setChapters(chapterList);
        setChapterContents(chapterResponses);

        // A1: ?chapter= 且 from=timeline → 优先时间线定位，不覆盖真实阅读进度
        const fromQuery = resolveChapterFromQuery(chapterList, chapterQuery);
        let initialChapterId = 0;
        let initialPercent = 0;

        if (fromQuery && (fromTimeline || chapterQuery)) {
          initialChapterId = fromQuery.id;
          initialPercent = 0;
          if (fromTimeline) {
            jumpedChapterIdRef.current = fromQuery.id;
            setProgressWritable(false);
          }
        } else {
          // 恢复：localStorage > 服务端 reading_progress > 第一章
          const saved = loadProgress(novelId);
          if (saved?.chapterId && chapterList.some((c) => c.id === saved.chapterId)) {
            initialChapterId = saved.chapterId;
            initialPercent = saved.chapterPercent ?? 0;
          } else {
            const serverChapterId = (res.data as Novel & {
              reading_progress?: { chapter_id?: number };
            }).reading_progress?.chapter_id;
            if (
              serverChapterId &&
              chapterList.some((c) => c.id === serverChapterId)
            ) {
              initialChapterId = serverChapterId;
            } else if (chapterList.length > 0) {
              initialChapterId = chapterList[0].id;
            }
          }
          setProgressWritable(true);
        }
        pendingScrollChapterRef.current = initialChapterId;
        setChapterPercent(initialPercent);
        setRestorePercent(initialPercent);
        setCurrentChapterId(initialChapterId);
      } catch {
        if (active) setError("加载小说失败，请重试");
      } finally {
        if (active) setLoading(false);
      }
    }
    loadNovel();
    return () => {
      active = false;
    };
  }, [novelId, chapterQuery, fromTimeline]);

  // 滚动模式已一次性渲染全部章节；目录/书签跳转时只滚到目标章节，不重新请求正文。
  useEffect(() => {
    if (
      preferences.mode !== "scroll" ||
      !currentChapterId ||
      chapterContents.length === 0 ||
      pendingScrollChapterRef.current !== currentChapterId
    ) {
      return;
    }
    const frame = requestAnimationFrame(() => {
      const container = scrollRef.current;
      const target = container?.querySelector<HTMLElement>(
        `[data-reader-chapter-id="${currentChapterId}"]`
      );
      if (!container || !target) return;
      const chapterMax = Math.max(0, target.offsetHeight - container.clientHeight);
      const percent = Math.min(100, Math.max(0, restorePercent || 0));
      container.scrollTop = target.offsetTop + chapterMax * (percent / 100);
      pendingScrollChapterRef.current = null;
    });
    return () => cancelAnimationFrame(frame);
  }, [
    chapterContents.length,
    currentChapterId,
    preferences.mode,
    restorePercent,
  ]);

  // 全量预取后不再依赖“加载当前章”触发服务端进度同步，首次进入每章时补回同样的契约。
  useEffect(() => {
    if (!currentChapterId || chapterContents.length === 0) return;
    const key = `${novelId}:${currentChapterId}`;
    if (syncedProgressChapterRef.current === key) return;
    syncedProgressChapterRef.current = key;
    void novelsApi
      .updateProgress(novelId, currentChapterId, chapterPercent)
      .catch(() => {
        /* 服务端进度失败不影响本地阅读 */
      });
  }, [chapterContents.length, chapterPercent, currentChapterId, novelId]);

  const persistProgress = useCallback(
    (chapterId: number, percent: number) => {
      if (!progressWritable || !chapterId) return;
      saveProgress(novelId, chapterId, percent);
    },
    [novelId, progressWritable]
  );

  const handleChapterProgress = useCallback(
    (percent: number, progressChapterId = currentChapterId) => {
      setChapterPercent(percent);
      if (
        preferences.mode === "scroll" &&
        progressChapterId &&
        progressChapterId !== currentChapterId
      ) {
        setCurrentChapterId(progressChapterId);
      }
      // 时间线定位：用户在本章滚动不写进度；翻章后才写
      if (
        progressWritable ||
        (jumpedChapterIdRef.current != null &&
          progressChapterId !== jumpedChapterIdRef.current)
      ) {
        if (!progressWritable) setProgressWritable(true);
        if (progressChapterId) {
          saveProgress(novelId, progressChapterId, percent);
        }
      }
    },
    [currentChapterId, novelId, preferences.mode, progressWritable]
  );

  const handleSelectChapter = useCallback((chapterId: number) => {
    // 用户主动换章 → 恢复真实阅读进度写入
    if (
      jumpedChapterIdRef.current != null &&
      chapterId !== jumpedChapterIdRef.current
    ) {
      setProgressWritable(true);
    }
    pendingScrollChapterRef.current = chapterId;
    setChapterPercent(0);
    setRestorePercent(0);
    setCurrentChapterId(chapterId);
  }, []);

  const handlePrevChapter = useCallback(() => {
    const list = chaptersRef.current;
    const idx = list.findIndex((c) => c.id === currentChapterId);
    if (idx > 0) handleSelectChapter(list[idx - 1].id);
  }, [currentChapterId, handleSelectChapter]);

  const handleNextChapter = useCallback(() => {
    const list = chaptersRef.current;
    const idx = list.findIndex((c) => c.id === currentChapterId);
    if (idx >= 0 && idx < list.length - 1) {
      handleSelectChapter(list[idx + 1].id);
    }
  }, [currentChapterId, handleSelectChapter]);

  const handleAskSelection = useCallback((payload: SelectionCoordinate) => {
    setPendingSelection(payload);
    setChatOpen(true);
    setChatCollapsed(false);
  }, []);

  const handleBookmarkSelection = useCallback(
    async (payload: SelectionCoordinate) => {
      await novelsApi.createBookmark(novelId, payload);
    },
    [novelId]
  );

  const handleBookmarkNavigate = useCallback(
    (bookmark: Bookmark) => {
      setHighlightRange({
        sourceStart: bookmark.source_start,
        sourceEnd: bookmark.source_end,
      });
      if (bookmark.chapter_id !== currentChapterId) {
        handleSelectChapter(bookmark.chapter_id);
      }
      window.setTimeout(() => setHighlightRange(null), 8000);
    },
    [currentChapterId, handleSelectChapter]
  );

  /** 沉浸模式：点按正文切换控制层；选中文本或点击交互控件时不触发 */
  const handleImmersiveSurfaceTap = useCallback(
    (event: React.MouseEvent) => {
      if (!preferences.immersive) return;
      const target = event.target;
      if (
        target instanceof Element &&
        target.closest("button, a, input, textarea, select")
      ) {
        return;
      }
      const selection = window.getSelection();
      if (selection && !selection.isCollapsed) return;
      setImmersiveChrome((visible) => !visible);
    },
    [preferences.immersive]
  );

  /** 阅读偏好变更：进入沉浸模式时把控制层重置为可见 */
  const handlePreferencesChange = useCallback(
    (next: ReaderPreferences) => {
      if (next.immersive && !preferences.immersive) setImmersiveChrome(true);
      setPreferences(next);
    },
    [preferences.immersive]
  );

  const handleCitationNavigate = useCallback(
    (target: {
      chapter_id: number;
      source_start: number;
      source_end: number;
    }) => {
      setHighlightRange({
        sourceStart: target.source_start,
        sourceEnd: target.source_end,
      });
      if (target.chapter_id !== currentChapterId) {
        handleSelectChapter(target.chapter_id);
      }
      // Clear highlight after a few seconds so reading can continue
      window.setTimeout(() => setHighlightRange(null), 8000);
    },
    [currentChapterId, handleSelectChapter]
  );

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "f") {
        e.preventDefault();
        setSearchOpen(true);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  // ESC：沉浸模式下退出沉浸（搜索/目录抽屉/设置面板打开、输入控件聚焦或有选区时不抢）
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || !preferences.immersive) return;
      if (searchOpen || immersiveTocOpen || preferencesOpen) return;
      const target = e.target;
      if (
        target instanceof Element &&
        target.closest("input, textarea, select, [contenteditable='true']")
      ) {
        return;
      }
      const selection = window.getSelection();
      if (selection && !selection.isCollapsed) return;
      setPreferences((current) => ({ ...current, immersive: false }));
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [preferences.immersive, searchOpen, immersiveTocOpen, preferencesOpen]);

  // 离开页面前再存一次（时间线定位模式且仍停在跳转章时不写）
  useEffect(() => {
    return () => {
      if (!progressWritable || !currentChapterId) return;
      saveProgress(novelId, currentChapterId, chapterPercent);
    };
  }, [novelId, currentChapterId, chapterPercent, progressWritable]);

  if (loading) {
    return (
      <div className="flex min-h-[70vh] items-center justify-center">
        <BookLoader label="正在翻开书本…" />
      </div>
    );
  }

  if (error || !novel) {
    return (
      <div className="flex min-h-[70vh] items-center justify-center">
        <div className="text-center">
          <p className="mb-4 text-muted-foreground">{error || "小说不存在"}</p>
          <Button onClick={() => router.push("/novels")}>返回书架</Button>
        </div>
      </div>
    );
  }

  const currentIndex = chapters.findIndex((c) => c.id === currentChapterId);
  const currentChapterTitle = chapterContent?.title || "-";

  const showDesktopChat = chatOpen && isDesktop;
  const showMobileChat = chatOpen && !isDesktop;
  return (
    <div
      className={cn(
        "relative flex h-[calc(100dvh-4rem)] overflow-hidden bg-background text-foreground md:h-dvh xl:p-4 xl:pl-0",
        preferences.theme === "dark" && "dark",
        preferences.immersive && "fixed inset-0 z-[60] h-dvh p-0 xl:p-0"
      )}
      data-reader-theme={preferences.theme}
      data-reader-mode={preferences.mode}
    >
      {!preferences.immersive ? (
        <ChapterSidebar
          chapters={chapters}
          currentChapterId={currentChapterId}
          onSelectChapter={handleSelectChapter}
          isOpen={sidebarOpen}
          onToggle={() => setSidebarOpen((v) => !v)}
        />
      ) : null}

      <main
        className={cn(
          "relative flex min-w-0 flex-1 flex-col overflow-hidden bg-card/75",
          !preferences.immersive &&
            "xl:rounded-[28px] xl:border xl:border-border/70 xl:shadow-[0_25px_70px_-45px_rgba(52,42,32,0.55)]"
        )}
      >
        {!preferences.immersive ? (
        <header className="z-20 flex items-center justify-between border-b border-border/70 bg-card/80 px-3 py-3 backdrop-blur-xl sm:px-5">
          <div className="flex items-center gap-2 sm:gap-3">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSidebarOpen((v) => !v)}
              title={sidebarOpen ? "收起目录" : "展开目录"}
            >
              {sidebarOpen ? (
                <PanelLeft className="size-4" />
              ) : (
                <Menu className="size-4" />
              )}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                persistProgress(currentChapterId, chapterPercent);
                router.push("/novels");
              }}
              className="hidden sm:inline-flex"
            >
              <ArrowLeft className="size-4" />
              书架
            </Button>
            <div className="h-5 w-px bg-border/80" />
            <h1 className="max-w-[150px] truncate font-serif text-base font-semibold sm:max-w-md sm:text-lg">
              {novel.title}
            </h1>
          </div>

          <div className="flex items-center gap-2">
            <ReaderPreferencesPanel
              preferences={preferences}
              onChange={handlePreferencesChange}
              open={preferencesOpen}
              onOpenChange={setPreferencesOpen}
            />
            <ReaderBookmarks
              novelId={novelId}
              chapters={chapters}
              open={bookmarksOpen}
              onOpenChange={setBookmarksOpen}
              onNavigate={handleBookmarkNavigate}
            />
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setChatOpen((current) => {
                  if (!current) setChatCollapsed(false);
                  return !current;
                });
              }}
              title="选区对话"
              data-testid="reader-chat-open"
              data-reader-chat-toggle
            >
              <MessageSquareText className="size-4" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSearchOpen(true)}
              title="本书内搜索 (Ctrl+F)"
            >
              <Search className="size-4" />
            </Button>
            {preferences.mode !== "scroll" ? (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handlePrevChapter}
                  disabled={currentIndex <= 0}
                >
                  <ChevronLeft className="size-4" />
                  <span className="hidden sm:inline">上一章</span>
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleNextChapter}
                  disabled={currentIndex < 0 || currentIndex >= chapters.length - 1}
                >
                  <span className="hidden sm:inline">下一章</span>
                  <ChevronRight className="size-4" />
                </Button>
              </>
            ) : null}
          </div>
        </header>
        ) : null}

        {/* Desktop: reading column + reserved chat column (no permanent overlay). */}
        <div className="flex min-h-0 flex-1">
          <div
            ref={scrollRef}
            data-testid="reader-scroll-column"
            data-reader-surface
            data-reader-theme={preferences.theme}
            onClick={handleImmersiveSurfaceTap}
            className={cn(
              "min-w-0 flex-1 overflow-y-auto",
              // 手机端为悬浮底部导航/沉浸控制层预留空间（md 起无底部导航）
              preferences.immersive ? "pb-24" : "pb-24 md:pb-6"
            )}
          >
            <ReaderContent
              chapter={chapterContent}
              chapters={chapterContents}
              activeChapterId={currentChapterId}
              onChapterProgress={handleChapterProgress}
              scrollContainerRef={scrollRef}
              onNextChapter={handleNextChapter}
              onPrevChapter={handlePrevChapter}
              hasNextChapter={
                currentIndex >= 0 && currentIndex < chapters.length - 1
              }
              hasPrevChapter={currentIndex > 0}
              onAskSelection={handleAskSelection}
              onBookmarkSelection={handleBookmarkSelection}
              highlightRange={highlightRange}
              highlightChapterId={currentChapterId}
              readingMode={preferences.mode}
              initialProgress={restorePercent}
              fontSize={preferences.fontSize}
              lineHeight={preferences.lineHeight}
              contentWidth={preferences.contentWidth}
            />
          </div>
          {showDesktopChat ? (
            <div
              data-testid="reader-chat-column"
              className="relative shrink-0 overflow-hidden transition-[width] motion-duration-spatial motion-ease-enter"
              style={{
                width: chatCollapsed ? 44 : chatWidthPx,
              }}
            >
              {!chatCollapsed ? (
                <div
                  data-testid="reader-chat-resize-handle"
                  role="separator"
                  aria-orientation="vertical"
                  aria-label="调整对话面板宽度"
                  aria-valuenow={chatWidthPx}
                  tabIndex={0}
                  className="absolute inset-y-0 left-0 z-20 w-1.5 cursor-col-resize touch-none bg-transparent transition-[background-color] motion-duration-fast motion-ease-enter hover:bg-primary/25 active:bg-primary/40"
                  onPointerDown={onChatResizePointerDown}
                  onPointerMove={onChatResizePointerMove}
                  onPointerUp={onChatResizePointerUp}
                  onPointerCancel={onChatResizePointerUp}
                  onKeyDown={(e) => {
                    if (e.key === "ArrowLeft") {
                      e.preventDefault();
                      setChatWidthPx((w) => clampReaderChatWidth(w + 16));
                    } else if (e.key === "ArrowRight") {
                      e.preventDefault();
                      setChatWidthPx((w) => clampReaderChatWidth(w - 16));
                    }
                  }}
                />
              ) : null}
              <ReaderChatPanel
                novelId={novelId}
                currentChapterId={currentChapterId}
                layout="desktop"
                open={chatOpen}
                collapsed={chatCollapsed}
                onOpenChange={setChatOpen}
                onCollapsedChange={setChatCollapsed}
                pendingSelection={pendingSelection}
                onClearSelection={() => setPendingSelection(null)}
                onCitationNavigate={handleCitationNavigate}
              />
            </div>
          ) : null}
        </div>

        {!preferences.immersive ? (
          <div className="mb-[calc(4.75rem_+_env(safe-area-inset-bottom))] shrink-0 md:mb-0">
            <ProgressBar
              chapterPercent={chapterPercent}
              chapterTitle={currentChapterTitle}
              chapterIndex={currentIndex >= 0 ? currentIndex + 1 : 0}
              chapterTotal={chapters.length}
            />
          </div>
        ) : null}
      </main>

      {preferences.immersive ? (
        <>
          {/* 沉浸模式补回章节目录：抽屉形态 + 左上悬浮入口 */}
          <ChapterSidebar
            chapters={chapters}
            currentChapterId={currentChapterId}
            onSelectChapter={handleSelectChapter}
            isOpen={immersiveTocOpen}
            onToggle={() => setImmersiveTocOpen((v) => !v)}
            forceDrawer
          />
          {/* 目录入口与沉浸控制层解耦：控制栏隐藏时仍可打开目录。 */}
          <Button
            type="button"
            variant="outline"
            size="icon"
            onClick={() => setImmersiveTocOpen(true)}
            title="章节目录"
            aria-label="章节目录"
            className="fixed left-4 top-4 z-50 border-white/20 bg-black/65 text-white shadow-lg hover:bg-black/75 hover:text-white"
          >
            <BookOpenText className="size-4" />
          </Button>
          {immersiveChrome ? (
            <>
              <ReaderPreferencesPanel
                preferences={preferences}
                onChange={handlePreferencesChange}
                open={preferencesOpen}
                onOpenChange={setPreferencesOpen}
                floating
                floatingOffsetRight={
                  // 桌面 AI 会话打开时右移让位，避免与会话窗口重叠
                  showDesktopChat ? (chatCollapsed ? 44 : chatWidthPx) + 16 : 16
                }
              />
              {/* 底部控制层：翻章 / 进度 / AI 对话入口；点按正文任意处可隐藏 */}
              <div className="fixed inset-x-0 bottom-0 z-50 flex justify-center px-4 pb-[calc(0.75rem_+_env(safe-area-inset-bottom))]">
                <div className="flex items-center gap-1 rounded-full border border-white/15 bg-black/65 px-2 py-1.5 text-white shadow-lg backdrop-blur">
                  {preferences.mode !== "scroll" ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={handlePrevChapter}
                      disabled={currentIndex <= 0}
                      className="text-white hover:bg-white/10 hover:text-white"
                    >
                      <ChevronLeft className="size-4" />
                      上一章
                    </Button>
                  ) : null}
                  <span className="px-2 text-xs tabular-nums text-white/80">
                    {Math.round(chapterPercent)}%
                    {chapters.length > 0
                      ? ` · ${currentIndex + 1}/${chapters.length} 章`
                      : null}
                  </span>
                  {preferences.mode !== "scroll" ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={handleNextChapter}
                      disabled={
                        currentIndex < 0 || currentIndex >= chapters.length - 1
                      }
                      className="text-white hover:bg-white/10 hover:text-white"
                    >
                      下一章
                      <ChevronRight className="size-4" />
                    </Button>
                  ) : null}
                  <div className="h-5 w-px bg-white/20" aria-hidden />
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setChatOpen((current) => {
                        if (!current) setChatCollapsed(false);
                        return !current;
                      });
                    }}
                    title="选区对话"
                    className="text-white hover:bg-white/10 hover:text-white"
                  >
                    <MessageSquareText className="size-4" />
                  </Button>
                </div>
              </div>
            </>
          ) : null}
        </>
      ) : null}

      {showMobileChat ? (
        <ReaderChatPanel
          novelId={novelId}
          currentChapterId={currentChapterId}
          layout="mobile"
          open={chatOpen}
          collapsed={chatCollapsed}
          onOpenChange={setChatOpen}
          onCollapsedChange={setChatCollapsed}
          pendingSelection={pendingSelection}
          onClearSelection={() => setPendingSelection(null)}
          onCitationNavigate={handleCitationNavigate}
        />
      ) : null}

      <SearchPanel
        novelId={Number(novelId)}
        isOpen={searchOpen}
        onClose={() => setSearchOpen(false)}
        onNavigate={(chapterId) => handleSelectChapter(chapterId)}
      />
      {fromTimeline && !progressWritable && (
        <div className="pointer-events-none absolute bottom-[calc(5rem_+_env(safe-area-inset-bottom))] left-1/2 z-30 -translate-x-1/2 rounded-full border border-amber-300/80 bg-amber-50 px-4 py-1.5 text-xs text-amber-950 shadow-sm">
          时间线定位模式 · 未改动你的阅读进度
        </div>
      )}
    </div>
  );
}

export default function NovelReaderPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-[70vh] items-center justify-center">
          <BookLoader label="加载阅读器…" />
        </div>
      }
    >
      <NovelReaderInner />
    </Suspense>
  );
}
