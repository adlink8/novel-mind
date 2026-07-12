"use client";

import React, { useState, useEffect, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { ChapterSidebar } from "@/components/reader/chapter-sidebar";
import { ReaderContent } from "@/components/reader/reader-content";
import { ProgressBar } from "@/components/reader/progress-bar";
import { SearchPanel } from "@/components/reader/search-panel";
import { novelsApi, type Novel, type Chapter } from "@/lib/api";
import { ArrowLeft, ChevronLeft, ChevronRight, LoaderCircle, Menu, Search } from "lucide-react";

/** 阅读进度 localStorage 键名 */
function getStorageKey(novelId: string): string {
  return `novelmind:reading:${novelId}`;
}

/** 从 localStorage 读取阅读进度 */
function loadProgress(novelId: string): { chapterId: number; progressPercent: number } | null {
  try {
    const raw = localStorage.getItem(getStorageKey(novelId));
    if (raw) return JSON.parse(raw);
  } catch {
    // 忽略解析错误
  }
  return null;
}

/** 保存阅读进度到 localStorage */
function saveProgress(novelId: string, chapterId: number, progressPercent: number): void {
  try {
    localStorage.setItem(
      getStorageKey(novelId),
      JSON.stringify({ chapterId, progressPercent, updatedAt: Date.now() })
    );
  } catch {
    // 忽略写入错误
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
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  /** 加载小说详情和章节列表 */
  useEffect(() => {
    async function loadNovel() {
      try {
        setLoading(true);
        const res = await novelsApi.get(novelId);
        setNovel(res.data);

        // 加载章节列表
        const chaptersRes = await novelsApi.getChapters(novelId);
        const chapterList = chaptersRes.data;
        setChapters(chapterList);

        // 确定初始章节：localStorage > 第一章
        const saved = loadProgress(novelId);
        let initialChapterId: number;
        if (saved && chapterList.find((c) => c.id === saved.chapterId)) {
          initialChapterId = saved.chapterId;
        } else if (chapterList.length > 0) {
          initialChapterId = chapterList[0].id;
        } else {
          initialChapterId = 0;
        }

        setCurrentChapterId(initialChapterId);
      } catch (err) {
        setError("加载小说失败，请重试");
      } finally {
        setLoading(false);
      }
    }

    loadNovel();
  }, [novelId]);

  /** 切换章节时加载章节内容 */
  useEffect(() => {
    if (!currentChapterId) return;

    async function loadChapter() {
      try {
        const res = await novelsApi.getChapter(novelId, String(currentChapterId));
        setChapterContent(res.data);

        // 保存阅读进度到 localStorage
        const chapterIndex = chapters.findIndex((c) => c.id === currentChapterId);
        const progressPercent = chapters.length > 0
          ? ((chapterIndex + 1) / chapters.length) * 100
          : 0;
        saveProgress(novelId, currentChapterId, progressPercent);
      } catch {
        setChapterContent(null);
      }
    }

    loadChapter();
  }, [currentChapterId, novelId, chapters]);

  /** 选择章节 */
  const handleSelectChapter = useCallback((chapterId: number) => {
    setCurrentChapterId(chapterId);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  /** 上一章 / 下一章 */
  const handlePrevChapter = useCallback(() => {
    const idx = chapters.findIndex((c) => c.id === currentChapterId);
    if (idx > 0) handleSelectChapter(chapters[idx - 1].id);
  }, [chapters, currentChapterId, handleSelectChapter]);

  const handleNextChapter = useCallback(() => {
    const idx = chapters.findIndex((c) => c.id === currentChapterId);
    if (idx >= 0 && idx < chapters.length - 1) {
      handleSelectChapter(chapters[idx + 1].id);
    }
  }, [chapters, currentChapterId, handleSelectChapter]);

  /** Ctrl+F 快捷键打开搜索面板 */
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

  // 加载中
  if (loading) {
    return (
      <div className="flex min-h-[70vh] items-center justify-center">
        <div className="text-center">
          <LoaderCircle className="mx-auto mb-4 size-7 animate-spin text-primary" />
          <p className="text-muted-foreground">{"加载中..."}</p>
        </div>
      </div>
    );
  }

  // 错误
  if (error || !novel) {
    return (
      <div className="flex min-h-[70vh] items-center justify-center">
        <div className="text-center">
          <p className="text-muted-foreground mb-4">{error || "小说不存在"}</p>
          <Button onClick={() => router.push("/novels")}>{"返回书架"}</Button>
        </div>
      </div>
    );
  }

  const currentIndex = chapters.findIndex((c) => c.id === currentChapterId);
  const currentChapterTitle = chapterContent?.title || "-";

  return (
    <div className="relative flex h-[calc(100vh-4rem)] overflow-hidden bg-[#f6f1e8]/70 lg:h-screen lg:p-4 lg:pl-0">
      {/* 章节侧边栏 */}
      <ChapterSidebar
        chapters={chapters}
        currentChapterId={currentChapterId}
        onSelectChapter={handleSelectChapter}
        isOpen={sidebarOpen}
        onToggle={() => setSidebarOpen(!sidebarOpen)}
      />

      {/* 主内容区 */}
      <main className="relative flex min-w-0 flex-1 flex-col overflow-hidden lg:rounded-[28px] lg:border lg:border-white/60 lg:bg-card/75 lg:shadow-[0_25px_70px_-45px_rgba(52,42,32,0.55)]">
        {/* 顶栏 */}
        <header className="z-20 flex items-center justify-between border-b border-border/70 bg-card/80 px-3 py-3 backdrop-blur-xl sm:px-5">
          <div className="flex items-center gap-3">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSidebarOpen(true)}
              className="lg:hidden"
            >
              <Menu className="size-4" />
            </Button>
            <Button variant="ghost" size="sm" onClick={() => router.push("/novels")} className="hidden sm:inline-flex"><ArrowLeft className="size-4" />书架</Button>
            <div className="h-5 w-px bg-border/80" />
            <h1 className="max-w-[150px] truncate font-serif text-base font-semibold sm:max-w-md sm:text-lg">{novel.title}</h1>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSearchOpen(true)}
              title="搜索 (Ctrl+F)"
            >
              <Search className="size-4" />
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handlePrevChapter}
              disabled={currentIndex <= 0}
            >
              <ChevronLeft className="size-4" /><span className="hidden sm:inline">上一章</span>
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleNextChapter}
              disabled={currentIndex >= chapters.length - 1}
            >
              <span className="hidden sm:inline">下一章</span><ChevronRight className="size-4" />
            </Button>
          </div>
        </header>

        {/* 阅读区 */}
        <div className="flex-1 overflow-y-auto pb-16">
          <ReaderContent chapter={chapterContent} />
        </div>

        {/* 底部进度条 */}
        <ProgressBar
          current={currentIndex + 1}
          total={chapters.length}
          chapterTitle={currentChapterTitle}
        />
      </main>

      {/* 搜索面板 */}
      <SearchPanel
        novelId={Number(novelId)}
        isOpen={searchOpen}
        onClose={() => setSearchOpen(false)}
        onNavigate={(chapterId) => handleSelectChapter(chapterId)}
      />
    </div>
  );
}
