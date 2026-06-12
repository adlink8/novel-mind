/**
 * 搜索结果页
 *
 * URL: /search?q=xxx
 * - 根据 URL 查询参数发起全局搜索
 * - 展示搜索结果列表（加载/空/错误三种状态）
 * - 每项可点击跳转到小说阅读页
 */

"use client";

import React, { useEffect, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { searchApi, type SearchResult } from "@/lib/api";
import { SearchResultCard } from "@/components/search/search-result-card";
import { SearchBar } from "@/components/search/search-bar";
import { EmptyState } from "@/components/empty-state";

function SearchContent() {
  const searchParams = useSearchParams();
  const query = searchParams.get("q") ?? "";

  const [results, setResults] = useState<SearchResult[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (query.trim().length === 0) {
      setResults([]);
      setTotal(0);
      setError(null);
      return;
    }

    let cancelled = false;

    async function doSearch() {
      setLoading(true);
      setError(null);
      try {
        const res = await searchApi.global(query, 20);
        if (!cancelled) {
          setResults(res.data.results);
          setTotal(res.data.total);
        }
      } catch {
        if (!cancelled) {
          setError("搜索失败，请稍后重试");
          setResults([]);
          setTotal(0);
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }

    doSearch();

    return () => {
      cancelled = true;
    };
  }, [query]);

  // ========== 无查询参数 ==========
  if (query.trim().length === 0) {
    return (
      <div className="flex flex-col items-center gap-8 py-16">
        <div className="w-full max-w-lg">
          <SearchBar />
        </div>
        <EmptyState
          icon="🔍"
          title="输入关键词开始搜索"
          description="搜索小说名称、章节标题或正文内容"
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-8">
      {/* 标题 */}
      <h1 className="text-xl font-bold mb-6">
        搜索：<span className="text-primary">{query}</span>
        {!loading && total > 0 && (
          <span className="ml-2 text-sm font-normal text-muted-foreground">
            共 {total} 个结果
          </span>
        )}
      </h1>

      {/* 搜索栏 */}
      <div className="mb-6">
        <SearchBar />
      </div>

      {/* 加载中 */}
      {loading && (
        <div className="space-y-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div
              key={i}
              className="animate-pulse rounded-lg border border-border bg-muted/30 p-4"
            >
              <div className="h-4 w-2/3 rounded bg-muted mb-3" />
              <div className="h-3 w-1/3 rounded bg-muted mb-2" />
              <div className="h-3 w-full rounded bg-muted" />
            </div>
          ))}
        </div>
      )}

      {/* 错误 */}
      {!loading && error && (
        <EmptyState
          icon="⚠️"
          title="搜索出错"
          description={error}
        />
      )}

      {/* 空结果 */}
      {!loading && !error && results.length === 0 && (
        <EmptyState
          icon="📭"
          title="未找到相关结果"
          description={`没有找到与「${query}」相关的内容，请尝试其他关键词`}
        />
      )}

      {/* 结果列表 */}
      {!loading && !error && results.length > 0 && (
        <div className="space-y-3">
          {results.map((result) => (
            <SearchResultCard
              key={`${result.novel_id}-${result.chunk_id}`}
              result={result}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function SearchPage() {
  return (
    <Suspense
      fallback={
        <div className="mx-auto max-w-3xl px-4 py-8">
          <div className="h-10 w-64 rounded-lg bg-muted animate-pulse mb-6" />
          <div className="space-y-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div
                key={i}
                className="animate-pulse rounded-lg border border-border bg-muted/30 p-4"
              >
                <div className="h-4 w-2/3 rounded bg-muted mb-3" />
                <div className="h-3 w-1/3 rounded bg-muted mb-2" />
                <div className="h-3 w-full rounded bg-muted" />
              </div>
            ))}
          </div>
        </div>
      }
    >
      <SearchContent />
    </Suspense>
  );
}
