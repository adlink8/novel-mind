"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { searchApi, type SearchResult } from "@/lib/api";

interface SearchPanelProps {
  novelId: number;
  isOpen: boolean;
  onClose: () => void;
  onNavigate?: (chapterId: number) => void;
}

/** 在文本中高亮搜索关键词 */
function highlightText(text: string, query: string): React.ReactNode {
  if (!query.trim()) return text;

  const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const parts = text.split(new RegExp(`(${escaped})`, "gi"));

  return (
    <>
      {parts.map((part, i) =>
        part.toLowerCase() === query.toLowerCase() ? (
          <mark key={i} className="bg-yellow-200/50 rounded px-0.5">
            {part}
          </mark>
        ) : (
          <React.Fragment key={i}>{part}</React.Fragment>
        )
      )}
    </>
  );
}

export function SearchPanel({
  novelId,
  isOpen,
  onClose,
  onNavigate,
}: SearchPanelProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasSearched, setHasSearched] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  /** 面板打开时自动聚焦输入框，关闭时重置状态 */
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 100);
    } else {
      setQuery("");
      setResults([]);
      setError(null);
      setHasSearched(false);
    }
  }, [isOpen]);

  /** Esc 关闭面板 */
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!isOpen) return;
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isOpen, onClose]);

  /** 执行搜索 */
  const performSearch = useCallback(
    async (q: string) => {
      if (!q.trim()) {
        setResults([]);
        setHasSearched(false);
        setError(null);
        return;
      }

      setLoading(true);
      setError(null);
      try {
        const res = await searchApi.inNovel(novelId, q, 10);
        setResults(res.data.results);
        setHasSearched(true);
      } catch (err: any) {
        const msg = err?.response?.data?.detail 
          || err?.message 
          || "搜索失败，请重试";
        setError(msg);
        setResults([]);
      } finally {
        setLoading(false);
      }
    },
    [novelId]
  );

  /** 防抖 300ms */
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => performSearch(query), 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, performSearch]);

  if (!isOpen) return null;

  return (
    <>
      {/* 遮罩层 */}
      <div
        className="fixed inset-0 bg-black/20 z-40"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* 搜索面板 */}
      <aside className="fixed right-0 top-0 h-full w-[400px] max-w-[90vw] bg-background border-l border-border z-50 flex flex-col shadow-xl animate-in slide-in-from-right">
        {/* 头部：搜索输入框 + 关闭按钮 */}
        <div className="flex items-center gap-2 p-4 border-b border-border">
          <Input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索小说内容..."
            className="flex-1"
          />
          <Button variant="ghost" size="sm" onClick={onClose}>
            {"✕"}
          </Button>
        </div>

        {/* 结果区域 */}
        <div className="flex-1 overflow-y-auto p-4">
          {/* 加载中 */}
          {loading && (
            <div className="flex items-center justify-center py-12 text-muted-foreground">
              <p>{"搜索中..."}</p>
            </div>
          )}

          {/* 错误 */}
          {error && (
            <div className="flex items-center justify-center py-12 text-red-500">
              <p>{error}</p>
            </div>
          )}

          {/* 空输入提示 */}
          {!loading && !error && !query.trim() && (
            <div className="flex items-center justify-center py-12 text-muted-foreground">
              <p>{"输入关键词开始搜索"}</p>
            </div>
          )}

          {/* 无结果 */}
          {!loading && !error && hasSearched && results.length === 0 && (
            <div className="flex items-center justify-center py-12 text-muted-foreground">
              <p>{"未找到匹配内容"}</p>
            </div>
          )}

          {/* 搜索结果列表 */}
          {!loading && results.length > 0 && (
            <div className="space-y-3">
              {results.map((result, idx) => (
                <button
                  key={`${result.chunk_id}-${idx}`}
                  className="w-full text-left p-3 rounded-lg border border-border hover:bg-accent transition-colors"
                  onClick={() => {
                    if (result.chapter_id && onNavigate) {
                      onNavigate(result.chapter_id);
                      onClose();
                    }
                  }}
                >
                  {/* 章节名 */}
                  <p className="text-xs text-muted-foreground mb-1">
                    {result.chapter_title || `第${result.chapter_id}章`}
                  </p>

                  {/* 高亮片段 */}
                  <p className="text-sm leading-relaxed mb-1">
                    {highlightText(result.content_snippet, query)}
                  </p>

                  {/* 相关度 */}
                  <p className="text-xs text-muted-foreground text-right">
                    相关度: {Math.round(result.score * 100)}%
                  </p>
                </button>
              ))}
            </div>
          )}
        </div>
      </aside>
    </>
  );
}
