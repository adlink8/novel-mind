/**
 * 搜索结果页
 * URL: /search?q=xxx&novel=可选小说ID
 */

"use client";

import React, { useEffect, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { novelsApi, searchApi, type Novel, type SearchResult } from "@/lib/api";
import { SearchResultCard } from "@/components/search/search-result-card";
import { SearchBar } from "@/components/search/search-bar";
import { EmptyState } from "@/components/empty-state";
import { Search, SearchX, TriangleAlert } from "lucide-react";
import { PageContainer, PageHeader } from "@/components/page-header";
import { Skeleton } from "@/components/ui/skeleton";

/** 结果卡片形状的骨架占位 */
function ResultSkeleton() {
  return (
    <div className="rounded-lg border border-border bg-muted/30 p-4">
      <Skeleton className="mb-3 h-4 w-2/3" />
      <Skeleton className="mb-2 h-3 w-1/3" />
      <Skeleton className="h-3 w-full" />
    </div>
  );
}

function SearchContent() {
  const searchParams = useSearchParams();
  const query = searchParams.get("q") ?? "";
  const novelParam = searchParams.get("novel") ?? "";

  const [results, setResults] = useState<SearchResult[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scopeNovel, setScopeNovel] = useState<Novel | null>(null);

  // 解析选中书本标题（用于结果页文案）
  useEffect(() => {
    let cancelled = false;
    async function loadScope() {
      if (!novelParam) {
        await Promise.resolve();
        if (!cancelled) setScopeNovel(null);
        return;
      }
      try {
        const res = await novelsApi.get(novelParam);
        if (!cancelled) setScopeNovel(res.data);
      } catch {
        if (!cancelled) setScopeNovel(null);
      }
    }
    void loadScope();
    return () => {
      cancelled = true;
    };
  }, [novelParam]);

  useEffect(() => {
    let cancelled = false;

    async function doSearch() {
      if (query.trim().length === 0) {
        // async boundary avoids react-hooks/set-state-in-effect
        await Promise.resolve();
        if (!cancelled) {
          setResults([]);
          setTotal(0);
          setLoading(false);
        }
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const res = novelParam
          ? await searchApi.inNovel(Number(novelParam), query, 20)
          : await searchApi.global(query, 20);
        if (!cancelled) {
          setResults(res.data.results || []);
          setTotal(res.data.total ?? res.data.results?.length ?? 0);
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

    void doSearch();

    return () => {
      cancelled = true;
    };
  }, [query, novelParam]);

  const scopeLabel = novelParam
    ? scopeNovel
      ? `《${scopeNovel.title}》`
      : `小说 #${novelParam}`
    : "全部作品";

  if (query.trim().length === 0) {
    return (
      <PageContainer className="space-y-8">
        <PageHeader
          eyebrow="Semantic retrieval"
          title="原文检索"
          description="可先选择一本书，或在全部作品中查找人物、事件、对白对应的原文。"
        />
        <div className="paper-surface mx-auto w-full max-w-3xl rounded-3xl p-4 sm:p-6">
          <SearchBar showNovelSelect />
        </div>
        <EmptyState
          icon={<Search className="size-6" />}
          title="选择范围并输入关键词"
          description="左侧下拉可限定单本书；选「全部作品」则跨书检索"
        />
      </PageContainer>
    );
  }

  return (
    <PageContainer className="space-y-7">
      <PageHeader
        eyebrow="Search results"
        title={`“${query}”`}
        description={
          !loading && total > 0
            ? `在 ${scopeLabel} 中找到 ${total} 条相关原文`
            : `正在 ${scopeLabel} 中搜索…`
        }
      />

      <div className="paper-surface rounded-3xl p-4 sm:p-5">
        <SearchBar
          key={`${query}::${novelParam}`}
          initialQuery={query}
          initialNovelId={novelParam}
          showNovelSelect
        />
      </div>

      {loading && (
        <div className="space-y-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <ResultSkeleton key={i} />
          ))}
        </div>
      )}

      {!loading && error && (
        <EmptyState
          icon={<TriangleAlert className="size-6" />}
          title="搜索出错"
          description={error}
        />
      )}

      {!loading && !error && results.length === 0 && (
        <EmptyState
          icon={<SearchX className="size-6" />}
          title="未找到相关结果"
          description={
            novelParam
              ? `在 ${scopeLabel} 中没有找到「${query}」。可换关键词，或改选「全部作品」。若本书显示未建索引，检索可能为空。`
              : `没有找到与「${query}」相关的内容，请尝试其他关键词`
          }
        />
      )}

      {!loading && !error && results.length > 0 && (
        <div className="space-y-3">
          {results.map((result, index) => (
            <div
              key={`${result.novel_id}-${result.chunk_id}`}
              className="motion-stagger-item"
              style={{ "--stagger-index": Math.min(index, 10) } as React.CSSProperties}
            >
              <SearchResultCard result={result} />
            </div>
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
          <Skeleton className="mb-6 h-10 w-64" />
          <div className="space-y-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <ResultSkeleton key={i} />
            ))}
          </div>
        </div>
      }
    >
      <SearchContent />
    </Suspense>
  );
}
