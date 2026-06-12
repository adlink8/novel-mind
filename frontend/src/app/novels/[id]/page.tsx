"use client";

import React, { useState, useEffect, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { ChapterSidebar } from "@/components/reader/chapter-sidebar";
import { ReaderContent } from "@/components/reader/reader-content";
import { ProgressBar } from "@/components/reader/progress-bar";
import { SearchPanel } from "@/components/reader/search-panel";
import { novelsApi, type Novel, type Chapter } from "@/lib/api";

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
      <div className="flex items-center justify-center h-screen">
        <div className="text-center">
          <div className="text-4xl mb-4 animate-pulse">{"⏳"}</div>
          <p className="text-muted-foreground">{"加载中..."}</p>
        </div>
      </div>
    );
  }

  // 错误
  if (error || !novel) {
    return (
      <div className="flex items-center justify-center h-screen">
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
    <div className="flex h-screen overflow-hidden bg-background">
      {/* 章节侧边栏 */}
      <ChapterSidebar
        chapters={chapters}
        currentChapterId={currentChapterId}
        onSelectChapter={handleSelectChapter}
        isOpen={sidebarOpen}
        onToggle={() => setSidebarOpen(!sidebarOpen)}
      />

      {/* 主内容区 */}
      <main className="flex-1 flex flex-col min-w-0">
        {/* 顶栏 */}
        <header className="flex items-center justify-between px-4 py-3 border-b border-border bg-background/95 backdrop-blur z-20">
          <div className="flex items-center gap-3">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSidebarOpen(true)}
              className="lg:hidden"
            >
              {"☰"}
            </Button>
            <h1 className="font-semibold text-lg truncate max-w-[200px] sm:max-w-md">
              {novel.title}
            </h1>
          </div>

          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSearchOpen(true)}
              title="搜索 (Ctrl+F)"
            >
              <svg
                xmlns="http://www.w3.org/2000/svg"
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <circle cx="11" cy="11" r="8" />
                <path d="m21 21-4.3-4.3" />
              </svg>
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handlePrevChapter}
              disabled={currentIndex <= 0}
            >
              {"上一章"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleNextChapter}
              disabled={currentIndex >= chapters.length - 1}
            >
              {"下一章"}
            </Button>
          </div>
        </header>

        {/* 阅读区 */}
        <div className="flex-1 overflow-y-auto pb-20">
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
