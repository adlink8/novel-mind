"use client";

import React, { useState, useEffect, useCallback, useRef, Suspense } from "react";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { ChapterSidebar } from "@/components/reader/chapter-sidebar";
import { ReaderContent } from "@/components/reader/reader-content";
import { ReaderChatPanel } from "@/components/reader/reader-chat-panel";
import { ProgressBar } from "@/components/reader/progress-bar";
import { SearchPanel } from "@/components/reader/search-panel";
import {
  loadReaderPreferences,
  ReaderPreferencesPanel,
  saveReaderPreferences,
  type ReaderPreferences,
} from "@/components/reader/reader-preferences";
import { cn } from "@/lib/utils";
import {
  novelsApi,
  type Novel,
  type Chapter,
  type SelectionCoordinate,
} from "@/lib/api";
import { loadReaderChatPresentation } from "@/lib/reader-selection";
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  LoaderCircle,
  Menu,
  MessageSquareText,
  PanelLeft,
  Search,
} from "lucide-react";

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
  chapterList: Chapter[],
  chapterParam: string | null
): Chapter | null {
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
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [currentChapterId, setCurrentChapterId] = useState<number>(0);
  const [chapterContent, setChapterContent] = useState<Chapter | null>(null);
  // 桌面默认展开目录，移动默认收起
  // 桌面默认展开；移动端默认收起（惰性初始，避免 effect 同步 setState）
  const [sidebarOpen, setSidebarOpen] = useState(() => {
    if (typeof window === "undefined") return true;
    return window.innerWidth >= 1024;
  });
  const [searchOpen, setSearchOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [chapterPercent, setChapterPercent] = useState(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [preferences, setPreferences] = useState<ReaderPreferences>(() =>
    loadReaderPreferences()
  );
  const [preferencesOpen, setPreferencesOpen] = useState(false);
  const chaptersRef = useRef<Chapter[]>([]);
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
    return Boolean(loadReaderChatPresentation(novelId).collapsed);
  });
  const [pendingSelection, setPendingSelection] =
    useState<SelectionCoordinate | null>(null);
  const [highlightRange, setHighlightRange] = useState<{
    sourceStart: number;
    sourceEnd: number;
  } | null>(null);
  const [isDesktop, setIsDesktop] = useState(() => {
    if (typeof window === "undefined") return true;
    return window.innerWidth >= 1024;
  });

  useEffect(() => {
    const onResize = () => setIsDesktop(window.innerWidth >= 1024);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => {
    saveReaderPreferences(preferences);
  }, [preferences]);

  useEffect(() => {
    if (
      preferences.mode !== "scroll" ||
      !preferences.autoScroll ||
      !chapterContent
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
    chapterContent,
    preferences.autoScroll,
    preferences.autoScrollSpeed,
    preferences.mode,
  ]);

  useEffect(() => {
    chaptersRef.current = chapters;
  }, [chapters]);

  useEffect(() => {
    async function loadNovel() {
      try {
        setLoading(true);
        const res = await novelsApi.get(novelId);
        setNovel(res.data);

        const chaptersRes = await novelsApi.getChapters(novelId);
        const chapterList = chaptersRes.data;
        setChapters(chapterList);

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
        setChapterPercent(initialPercent);
        setCurrentChapterId(initialChapterId);
      } catch {
        setError("加载小说失败，请重试");
      } finally {
        setLoading(false);
      }
    }
    loadNovel();
  }, [novelId, chapterQuery, fromTimeline]);

  useEffect(() => {
    if (!currentChapterId) return;

    async function loadChapter() {
      try {
        const res = await novelsApi.getChapter(novelId, String(currentChapterId));
        setChapterContent(res.data);
        // 换章立刻顶到开头（instant）；布局后再顶一次，避免沿用上一章滚位
        const el = scrollRef.current;
        if (el) {
          el.scrollTop = 0;
          requestAnimationFrame(() => {
            el.scrollTop = 0;
          });
        }

        // 持久化当前章节：换章默认从 0% 起，不沿用上一章百分比
        const saved = loadProgress(novelId);
        const sameChapter = saved?.chapterId === currentChapterId;
        const pct = sameChapter ? (saved.chapterPercent ?? 0) : 0;
        setChapterPercent(pct);
        // 仅同章恢复才写回进度；新章由 ReaderContent 从顶部开始
        if (sameChapter) {
          saveProgress(novelId, currentChapterId, pct);
        } else if (progressWritable) {
          saveProgress(novelId, currentChapterId, 0);
        }

        // 同步到服务端（整书章节位置）
        try {
          await novelsApi.updateProgress(
            novelId,
            currentChapterId,
            sameChapter ? pct : 0
          );
        } catch {
          /* 后端进度失败不影响阅读 */
        }
      } catch {
        setChapterContent(null);
      }
    }

    loadChapter();
  }, [currentChapterId, novelId, progressWritable]);

  const persistProgress = useCallback(
    (chapterId: number, percent: number) => {
      if (!progressWritable || !chapterId) return;
      saveProgress(novelId, chapterId, percent);
    },
    [novelId, progressWritable]
  );

  const handleChapterProgress = useCallback(
    (percent: number) => {
      setChapterPercent(percent);
      // 时间线定位：用户在本章滚动不写进度；翻章后才写
      if (
        progressWritable ||
        (jumpedChapterIdRef.current != null &&
          currentChapterId !== jumpedChapterIdRef.current)
      ) {
        if (!progressWritable) setProgressWritable(true);
        if (currentChapterId) {
          saveProgress(novelId, currentChapterId, percent);
        }
      }
    },
    [currentChapterId, novelId, progressWritable]
  );

  const handleSelectChapter = useCallback((chapterId: number) => {
    // 用户主动换章 → 恢复真实阅读进度写入
    if (
      jumpedChapterIdRef.current != null &&
      chapterId !== jumpedChapterIdRef.current
    ) {
      setProgressWritable(true);
    }
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
        <div className="text-center">
          <LoaderCircle className="mx-auto mb-4 size-7 animate-spin text-primary" />
          <p className="text-muted-foreground">加载中...</p>
        </div>
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
        "relative flex h-[calc(100vh-4rem)] overflow-hidden bg-background text-foreground lg:h-screen lg:p-4 lg:pl-0",
        preferences.theme === "dark" && "dark",
        preferences.immersive && "fixed inset-0 z-[60] h-screen p-0 lg:p-0"
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
            "lg:rounded-[28px] lg:border lg:border-border/70 lg:shadow-[0_25px_70px_-45px_rgba(52,42,32,0.55)]"
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
              onChange={setPreferences}
              open={preferencesOpen}
              onOpenChange={setPreferencesOpen}
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
            className={cn(
              "min-w-0 flex-1 overflow-y-auto",
              preferences.immersive ? "pb-8" : "pb-6"
            )}
          >
            <ReaderContent
              chapter={chapterContent}
              onChapterProgress={handleChapterProgress}
              scrollContainerRef={scrollRef}
              onNextChapter={handleNextChapter}
              onPrevChapter={handlePrevChapter}
              hasNextChapter={
                currentIndex >= 0 && currentIndex < chapters.length - 1
              }
              hasPrevChapter={currentIndex > 0}
              onAskSelection={handleAskSelection}
              highlightRange={highlightRange}
              readingMode={preferences.mode}
            />
          </div>
          {showDesktopChat ? (
            <div
              data-testid="reader-chat-column"
              className={
                chatCollapsed
                  ? "w-12 shrink-0"
                  : "w-[min(360px,38vw)] shrink-0"
              }
            >
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
          <ProgressBar
            chapterPercent={chapterPercent}
            chapterTitle={currentChapterTitle}
            chapterIndex={currentIndex >= 0 ? currentIndex + 1 : 0}
            chapterTotal={chapters.length}
          />
        ) : null}
      </main>

      {preferences.immersive ? (
        <ReaderPreferencesPanel
          preferences={preferences}
          onChange={setPreferences}
          open={preferencesOpen}
          onOpenChange={setPreferencesOpen}
          floating
        />
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
        <div className="pointer-events-none absolute bottom-20 left-1/2 z-30 -translate-x-1/2 rounded-full border border-amber-300/80 bg-amber-50 px-4 py-1.5 text-xs text-amber-950 shadow-sm">
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
        <div className="flex min-h-[70vh] items-center justify-center text-muted-foreground">
          加载阅读器…
        </div>
      }
    >
      <NovelReaderInner />
    </Suspense>
  );
}
