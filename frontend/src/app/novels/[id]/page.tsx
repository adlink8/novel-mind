"use client";

import React, { useState, useEffect, useCallback, useRef } from "react";
import { useParams, useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { ChapterSidebar } from "@/components/reader/chapter-sidebar";
import { ReaderContent } from "@/components/reader/reader-content";
import { ProgressBar } from "@/components/reader/progress-bar";
import { SearchPanel } from "@/components/reader/search-panel";
import { novelsApi, type Novel, type Chapter } from "@/lib/api";
import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  LoaderCircle,
  Menu,
  PanelLeft,
  Search,
} from "lucide-react";

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

export default function NovelReaderPage() {
  const params = useParams();
  const router = useRouter();
  const novelId = String(params.id);

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
  const chaptersRef = useRef<Chapter[]>([]);

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

        // 恢复：localStorage > 服务端 reading_progress > 第一章
        const saved = loadProgress(novelId);
        let initialChapterId = 0;
        if (saved?.chapterId && chapterList.some((c) => c.id === saved.chapterId)) {
          initialChapterId = saved.chapterId;
          setChapterPercent(saved.chapterPercent ?? 0);
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
        setCurrentChapterId(initialChapterId);
      } catch {
        setError("加载小说失败，请重试");
      } finally {
        setLoading(false);
      }
    }
    loadNovel();
  }, [novelId]);

  useEffect(() => {
    if (!currentChapterId) return;

    async function loadChapter() {
      try {
        const res = await novelsApi.getChapter(novelId, String(currentChapterId));
        setChapterContent(res.data);
        scrollRef.current?.scrollTo({ top: 0 });

        // 持久化当前章节（本章进度由滚动/分页回调更新）
        const saved = loadProgress(novelId);
        const pct =
          saved?.chapterId === currentChapterId ? (saved.chapterPercent ?? 0) : 0;
        setChapterPercent(pct);
        saveProgress(novelId, currentChapterId, pct);

        // 同步到服务端（整书章节位置）
        try {
          await novelsApi.updateProgress(novelId, currentChapterId, pct);
        } catch {
          /* 后端进度失败不影响阅读 */
        }
      } catch {
        setChapterContent(null);
      }
    }

    loadChapter();
  }, [currentChapterId, novelId]);

  const handleChapterProgress = useCallback(
    (percent: number) => {
      setChapterPercent(percent);
      if (currentChapterId) {
        saveProgress(novelId, currentChapterId, percent);
      }
    },
    [currentChapterId, novelId]
  );

  const handleSelectChapter = useCallback((chapterId: number) => {
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

  // 离开页面前再存一次
  useEffect(() => {
    return () => {
      if (currentChapterId) {
        saveProgress(novelId, currentChapterId, chapterPercent);
      }
    };
  }, [novelId, currentChapterId, chapterPercent]);

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

  return (
    <div className="relative flex h-[calc(100vh-4rem)] overflow-hidden bg-[#f6f1e8]/70 lg:h-screen lg:p-4 lg:pl-0">
      <ChapterSidebar
        chapters={chapters}
        currentChapterId={currentChapterId}
        onSelectChapter={handleSelectChapter}
        isOpen={sidebarOpen}
        onToggle={() => setSidebarOpen((v) => !v)}
      />

      <main className="relative flex min-w-0 flex-1 flex-col overflow-hidden lg:rounded-[28px] lg:border lg:border-white/60 lg:bg-card/75 lg:shadow-[0_25px_70px_-45px_rgba(52,42,32,0.55)]">
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
                if (currentChapterId) {
                  saveProgress(novelId, currentChapterId, chapterPercent);
                }
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

        <div ref={scrollRef} className="flex-1 overflow-y-auto pb-16">
          <ReaderContent
            chapter={chapterContent}
            onChapterProgress={handleChapterProgress}
            scrollContainerRef={scrollRef}
          />
        </div>

        <ProgressBar
          chapterPercent={chapterPercent}
          chapterTitle={currentChapterTitle}
          chapterIndex={currentIndex >= 0 ? currentIndex + 1 : 0}
          chapterTotal={chapters.length}
        />
      </main>

      <SearchPanel
        novelId={Number(novelId)}
        isOpen={searchOpen}
        onClose={() => setSearchOpen(false)}
        onNavigate={(chapterId) => handleSelectChapter(chapterId)}
      />
    </div>
  );
}
