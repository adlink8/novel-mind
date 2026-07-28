"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { searchApi, type SearchResult } from "@/lib/api";
import { useDismissableLayer } from "@/lib/use-dismissable-layer";
import { cn } from "@/lib/utils";
import { X } from "lucide-react";

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
  const layerRef = useRef<HTMLElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Backdrop owns outside click; Escape still via shared layer.
  const { present, closing } = useDismissableLayer({
    open: isOpen,
    onDismiss: onClose,
    layerRef,
    closeOnOutside: false,
  });

  /** 面板打开时重置上次搜索并自动聚焦输入框 */
  useEffect(() => {
    if (!isOpen) return;

    const timer = setTimeout(() => {
      setQuery("");
      setResults([]);
      setError(null);
      setHasSearched(false);
      inputRef.current?.focus();
    }, 100);

    return () => clearTimeout(timer);
  }, [isOpen]);

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

  if (!present) return null;

  return (
    <>
      <div
        className={cn(
          "fixed inset-0 z-40 bg-black/20 transition-[opacity] motion-duration-spatial motion-ease-enter",
          isOpen && !closing ? "opacity-100" : "pointer-events-none opacity-0 motion-ease-exit"
        )}
        onClick={onClose}
        aria-hidden="true"
      />

      {/* 视口裁切层：面板右滑退出时不撑出文档横向滚动 */}
      <div className="pointer-events-none fixed inset-0 z-50 overflow-hidden">
        <aside
          ref={layerRef}
          aria-label="小说内搜索"
          aria-hidden={closing || undefined}
          className={cn(
            "absolute right-0 top-0 flex h-full w-[400px] max-w-[90vw] flex-col border-l border-border bg-background shadow-xl transition-[opacity,transform] motion-duration-spatial motion-ease-enter",
            isOpen && !closing
              ? "pointer-events-auto translate-x-0 opacity-100"
              : "translate-x-full opacity-0 motion-ease-exit"
          )}
        >
        <div className="flex items-center gap-2 border-b border-border p-4">
          <Input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索小说内容..."
            className="flex-1"
          />
          <Button variant="ghost" size="sm" onClick={onClose}>
            <X className="size-4" />
          </Button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {loading && (
            <div
              className="flex items-center justify-center py-12 text-muted-foreground"
              role="status"
              aria-busy="true"
            >
              <p>{"搜索中..."}</p>
            </div>
          )}

          {error && (
            <div className="flex items-center justify-center py-12 text-red-500">
              <p>{error}</p>
            </div>
          )}

          {!loading && !error && !query.trim() && (
            <div className="flex items-center justify-center py-12 text-muted-foreground">
              <p>{"输入关键词开始搜索"}</p>
            </div>
          )}

          {!loading && !error && hasSearched && results.length === 0 && (
            <div className="flex items-center justify-center py-12 text-muted-foreground">
              <p>{"未找到匹配内容"}</p>
            </div>
          )}

          {!loading && results.length > 0 && (
            <div className="space-y-3">
              {results.map((result, idx) => (
                <button
                  key={`${result.chunk_id}-${idx}`}
                  className="w-full rounded-lg border border-border p-3 text-left transition-[background-color,border-color] motion-duration-fast motion-ease-enter hover:bg-accent"
                  onClick={() => {
                    if (result.chapter_id && onNavigate) {
                      onNavigate(result.chapter_id);
                      onClose();
                    }
                  }}
                >
                  <p className="mb-1 text-xs text-muted-foreground">
                    {result.chapter_title || `第${result.chapter_id}章`}
                  </p>

                  <p className="mb-1 text-sm leading-relaxed">
                    {highlightText(result.content_snippet, query)}
                  </p>

                  <p className="text-right text-xs text-muted-foreground">
                    相关度: {Math.round(result.score * 100)}%
                  </p>
                </button>
              ))}
            </div>
          )}
        </div>
      </aside>
      </div>
    </>
  );
}
