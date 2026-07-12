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
import { FileQuestion, Search, SearchX, TriangleAlert } from "lucide-react";
import { PageContainer, PageHeader } from "@/components/page-header";

function SearchContent() {
  const searchParams = useSearchParams();
  const query = searchParams.get("q") ?? "";

  const [results, setResults] = useState<SearchResult[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (query.trim().length === 0) {
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
      <PageContainer className="space-y-8">
        <PageHeader eyebrow="Semantic retrieval" title="原文检索" description="跨越章节与作品，查找人物、事件、对白和伏笔对应的原文证据。" />
        <div className="paper-surface mx-auto w-full max-w-2xl rounded-3xl p-4 sm:p-6">
          <SearchBar />
        </div>
        <EmptyState
          icon={<Search className="size-6" />}
          title="输入关键词开始搜索"
          description="搜索小说名称、章节标题或正文内容"
        />
      </PageContainer>
    );
  }

  return (
    <PageContainer className="space-y-7">
      <PageHeader eyebrow="Search results" title={`“${query}”`} description={!loading && total > 0 ? `从你的故事库中找到 ${total} 条相关原文` : "正在你的故事库中寻找相关证据"} />

      {/* 搜索栏 */}
      <div className="paper-surface rounded-3xl p-4 sm:p-5">
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
          icon={<TriangleAlert className="size-6" />}
          title="搜索出错"
          description={error}
        />
      )}

      {/* 空结果 */}
      {!loading && !error && results.length === 0 && (
        <EmptyState
          icon={<SearchX className="size-6" />}
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
    </PageContainer>
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
