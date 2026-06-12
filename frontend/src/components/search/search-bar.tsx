/**
 * 全局搜索栏组件
 *
 * 提供防抖输入、下拉建议和 Command+K 快捷唤起。
 * - 输入 300ms 防抖后自动搜索
 * - 下拉展示 Top 5 搜索结果预览
 * - 回车或点击按钮跳转 /search?q=xxx
 * - 支持 Command+K / Ctrl+K 快捷键唤起
 *
 * 使用方式:
 *   <SearchBar />
 */

"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { searchApi, type SearchResult } from "@/lib/api";

export function SearchBar() {
  const router = useRouter();

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [isOpen, setIsOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  // ========== 防抖搜索 ==========
  useEffect(() => {
    if (query.trim().length === 0) {
      setResults([]);
      setIsOpen(false);
      setError(null);
      return;
    }

    const timer = setTimeout(async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await searchApi.global(query, 5);
        setResults(res.data.results);
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
  }, [query]);

  // ========== Command+K 快捷键 ==========
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

  // ========== 点击外部关闭 ==========
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

  // ========== 搜索 ==========
  const handleSearch = useCallback(() => {
    const trimmed = query.trim();
    if (trimmed.length === 0) return;
    setIsOpen(false);
    router.push(`/search?q=${encodeURIComponent(trimmed)}`);
  }, [query, router]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleSearch();
    }
  };

  // ========== 高亮片段渲染 ==========
  const renderSnippet = (html: string) => (
    <span
      dangerouslySetInnerHTML={{
        __html: html.replace(
          /<mark>/g,
          '<mark class="bg-yellow-200 text-yellow-900 rounded-sm px-0.5 dark:bg-yellow-500/30 dark:text-yellow-200">'
        ),
      }}
    />
  );

  // ========== 下拉是否可见 ==========
  const showDropdown = isOpen && (results.length > 0 || loading || error);

  return (
    <div ref={containerRef} className="relative w-full max-w-lg">
      <div className="flex items-center gap-2">
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
            placeholder="搜索小说、章节内容..."
            className="pr-16"
          />
          {/* 快捷键提示 */}
          <kbd className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 hidden sm:inline-flex items-center gap-0.5 rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
            <span className="text-xs">⌘</span>K
          </kbd>
        </div>
        <Button onClick={handleSearch} size="default" disabled={query.trim().length === 0}>
          搜索
        </Button>
      </div>

      {/* 下拉面板 */}
      {showDropdown && (
        <div className="absolute left-0 right-0 top-full z-50 mt-1 rounded-lg border border-border bg-popover shadow-lg">
          {/* 加载中 */}
          {loading && (
            <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
              <div className="size-4 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-muted-foreground" />
              搜索中...
            </div>
          )}

          {/* 错误 */}
          {error && !loading && (
            <div className="p-4 text-sm text-destructive">
              {error}
            </div>
          )}

          {/* 结果列表 */}
          {!loading && !error && results.length > 0 && (
            <ul className="max-h-80 overflow-y-auto py-1">
              {results.map((result) => (
                <li
                  key={`${result.novel_id}-${result.chunk_id}`}
                  className="cursor-pointer px-4 py-3 transition-colors hover:bg-muted"
                  onClick={() => router.push(
                    result.chapter_id !== null
                      ? `/novels/${result.novel_id}/read?chapter=${result.chapter_id}&chunk=${result.chunk_index}`
                      : `/novels/${result.novel_id}/read?chunk=${result.chunk_index}`
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium truncate">
                      {result.novel_title ?? `小说 #${result.novel_id}`}
                    </span>
                    <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                      {(result.score * 100).toFixed(0)}%
                    </span>
                  </div>
                  {result.chapter_title && (
                    <p className="text-xs text-muted-foreground truncate mt-0.5">
                      {result.chapter_title}
                    </p>
                  )}
                  <p className="text-sm leading-relaxed line-clamp-2 mt-1">
                    {renderSnippet(result.content_snippet)}
                  </p>
                </li>
              ))}
            </ul>
          )}

          {/* 无结果 */}
          {!loading && !error && results.length === 0 && query.trim().length > 0 && (
            <div className="p-4 text-center text-sm text-muted-foreground">
              未找到相关结果
            </div>
          )}
        </div>
      )}
    </div>
  );
}
