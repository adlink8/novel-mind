/**
 * 全局搜索栏
 * - 可选指定小说（全部 / 某本）
 * - 防抖预览、回车跳转 /search?q=...&novel=...
 */

"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { novelsApi, searchApi, type Novel, type SearchResult } from "@/lib/api";
import { BookOpen, Search } from "lucide-react";

export interface SearchBarProps {
  /** 初始关键词（结果页回填） */
  initialQuery?: string;
  /** 初始选中的小说 ID，空字符串表示全部 */
  initialNovelId?: string;
  /** 是否显示书本选择器，默认 true */
  showNovelSelect?: boolean;
  className?: string;
}

export function SearchBar({
  initialQuery = "",
  initialNovelId = "",
  showNovelSelect = true,
  className = "",
}: SearchBarProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [query, setQuery] = useState(initialQuery);
  const [novelId, setNovelId] = useState(initialNovelId);
  const [novels, setNovels] = useState<Novel[]>([]);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // URL 变化时同步
  useEffect(() => {
    const q = searchParams.get("q") ?? "";
    const n = searchParams.get("novel") ?? "";
    setQuery(q);
    setNovelId(n);
  }, [searchParams]);

  // 加载用户书架供选择
  useEffect(() => {
    if (!showNovelSelect) return;
    let cancelled = false;
    novelsApi
      .list()
      .then((res) => {
        if (!cancelled) setNovels(res.data.items || []);
      })
      .catch(() => {
        if (!cancelled) setNovels([]);
      });
    return () => {
      cancelled = true;
    };
  }, [showNovelSelect]);

  // 防抖预览搜索
  useEffect(() => {
    if (query.trim().length === 0) {
      setResults([]);
      setIsOpen(false);
      return;
    }

    const timer = setTimeout(async () => {
      setLoading(true);
      setError(null);
      try {
        const res = novelId
          ? await searchApi.inNovel(Number(novelId), query, 5)
          : await searchApi.global(query, 5);
        setResults(res.data.results || []);
        setIsOpen(true);
      } catch {
        setError("搜索失败，请稍后重试");
        setResults([]);
        setIsOpen(true);
      } finally {
        setLoading(false);
      }
    }, 300);

    return () => clearTimeout(timer);
  }, [query, novelId]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        inputRef.current?.focus();
      }
      if (e.key === "Escape") {
        setIsOpen(false);
        inputRef.current?.blur();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const buildSearchUrl = useCallback(
    (q: string, n: string) => {
      const params = new URLSearchParams();
      params.set("q", q);
      if (n) params.set("novel", n);
      return `/search?${params.toString()}`;
    },
    []
  );

  const handleSearch = useCallback(() => {
    const trimmed = query.trim();
    if (trimmed.length === 0) return;
    setIsOpen(false);
    router.push(buildSearchUrl(trimmed, novelId));
  }, [query, novelId, router, buildSearchUrl]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleSearch();
    }
  };

  const renderSnippet = (html: string) =>
    (html || "").replace(/<\/?mark>/gi, "");

  const showDropdown = isOpen && (results.length > 0 || loading || error);
  const selectedNovel = novels.find((n) => String(n.id) === novelId);

  return (
    <div ref={containerRef} className={`relative w-full ${className}`}>
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        {showNovelSelect && (
          <div className="relative sm:w-52 sm:shrink-0">
            <BookOpen className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <select
              value={novelId}
              onChange={(e) => setNovelId(e.target.value)}
              className="h-10 w-full cursor-pointer appearance-none rounded-xl border border-border bg-card py-2 pl-9 pr-8 text-sm focus:outline-none focus:ring-2 focus:ring-ring/30"
              aria-label="选择要检索的书本"
              title="限定在某本书内搜索，或选「全部作品」"
            >
              <option value="">全部作品</option>
              {novels.map((n) => (
                <option key={n.id} value={String(n.id)}>
                  {n.title}
                  {(n.chunk_count ?? 0) > 0 ? "" : "（未建索引）"}
                </option>
              ))}
            </select>
          </div>
        )}

        <div className="flex min-w-0 flex-1 items-center gap-2">
          <div className="relative flex-1">
            <Input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              onFocus={() => {
                if (query.trim().length > 0 && results.length > 0) {
                  setIsOpen(true);
                }
              }}
              placeholder={
                selectedNovel
                  ? `在《${selectedNovel.title}》中搜索…`
                  : "搜索全部作品中的原文…"
              }
              className="pr-16"
            />
            <kbd className="pointer-events-none absolute right-2 top-1/2 hidden -translate-y-1/2 items-center gap-0.5 rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground sm:inline-flex">
              <span className="text-xs">⌘</span>K
            </kbd>
          </div>
          <Button
            onClick={handleSearch}
            size="default"
            disabled={query.trim().length === 0}
          >
            <Search className="mr-1.5 size-4" />
            搜索
          </Button>
        </div>
      </div>

      {showNovelSelect && novelId && selectedNovel && (
        <p className="mt-1.5 text-xs text-muted-foreground">
          当前范围：仅《{selectedNovel.title}》
          {(selectedNovel.chunk_count ?? 0) === 0
            ? " · 提示：本书可能尚未建检索索引，结果可能为空"
            : ""}
        </p>
      )}

      {showDropdown && (
        <div className="absolute left-0 right-0 top-full z-50 mt-1 rounded-lg border border-border bg-popover shadow-lg">
          {loading && (
            <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
              <div className="size-4 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-muted-foreground" />
              搜索中...
            </div>
          )}

          {error && !loading && (
            <div className="p-4 text-sm text-destructive">{error}</div>
          )}

          {!loading && !error && results.length > 0 && (
            <ul className="max-h-80 overflow-y-auto py-1">
              {results.map((result) => (
                <li
                  key={`${result.novel_id}-${result.chunk_id}`}
                  className="cursor-pointer px-4 py-3 transition-colors hover:bg-muted"
                  onClick={() =>
                    router.push(
                      result.chapter_id != null
                        ? `/novels/${result.novel_id}?chapter=${result.chapter_id}&chunk=${result.chunk_index}`
                        : `/novels/${result.novel_id}?chunk=${result.chunk_index}`
                    )
                  }
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium">
                      {result.novel_title ?? `小说 #${result.novel_id}`}
                    </span>
                    <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                      {((result.score ?? 0) * 100).toFixed(0)}%
                    </span>
                  </div>
                  {result.chapter_title && (
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">
                      {result.chapter_title}
                    </p>
                  )}
                  <p className="mt-1 line-clamp-2 text-sm leading-relaxed">
                    {renderSnippet(result.content_snippet || "")}
                  </p>
                </li>
              ))}
            </ul>
          )}

          {!loading &&
            !error &&
            results.length === 0 &&
            query.trim().length > 0 && (
              <div className="p-4 text-center text-sm text-muted-foreground">
                未找到相关结果
              </div>
            )}
        </div>
      )}
    </div>
  );
}
