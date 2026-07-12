/**
 * 搜索结果卡片组件
 *
 * 可复用的搜索结果展示卡片，显示小说名、章节名、高亮片段和分数。
 * <mark> 标签渲染为黄色高亮背景。
 *
 * 使用方式:
 *   <SearchResultCard result={result} />
 */

"use client";

import React from "react";
import Link from "next/link";
import type { SearchResult } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { ArrowUpRight, BookOpenText } from "lucide-react";

interface SearchResultCardProps {
  result: SearchResult;
}

/** 将 content_snippet 中的 <mark> 标签渲染为高亮 <span> */
function renderSnippet(html: string) {
  return html.split(/(<mark>|<\/mark>)/gi).reduce<{ highlighted: boolean; nodes: React.ReactNode[] }>(
    (state, part, index) => {
      if (part.toLowerCase() === "<mark>") return { ...state, highlighted: true };
      if (part.toLowerCase() === "</mark>") return { ...state, highlighted: false };
      if (!part) return state;
      state.nodes.push(state.highlighted ? <mark key={index} className="rounded bg-primary/15 px-0.5 text-foreground">{part}</mark> : <React.Fragment key={index}>{part}</React.Fragment>);
      return state;
    },
    { highlighted: false, nodes: [] },
  ).nodes;
}

/** 构建阅读页链接 */
function buildReadUrl(
  novelId: number,
  chapterId: number | null,
  chunkIndex: number
): string {
  if (chapterId !== null) {
    return `/novels/${novelId}?chapter=${chapterId}&chunk=${chunkIndex}`;
  }
  return `/novels/${novelId}?chunk=${chunkIndex}`;
}

export function SearchResultCard({ result }: SearchResultCardProps) {
  return (
    <Link href={buildReadUrl(result.novel_id, result.chapter_id, result.chunk_index)} className="group block">
      <Card className="paper-surface cursor-pointer rounded-3xl transition-all duration-200 group-hover:-translate-y-0.5 group-hover:border-primary/30 group-hover:shadow-lg">
        <CardContent className="flex flex-col gap-3 p-5 sm:p-6">
          {/* 小说名 + 分数 */}
          <div className="flex items-center justify-between gap-2">
            <span className="flex min-w-0 items-center gap-2 font-serif text-lg font-semibold">
              <BookOpenText className="size-4 shrink-0 text-primary" />
              <span className="truncate">
              {result.novel_title ?? `小说 #${result.novel_id}`}
              </span>
            </span>
            <span className="shrink-0 rounded-full bg-secondary px-2.5 py-1 text-xs font-medium text-foreground tabular-nums">
              {(result.score * 100).toFixed(1)}%
            </span>
          </div>

          {/* 章节名 */}
          {result.chapter_title && (
            <span className="text-xs text-muted-foreground">
              {result.chapter_title}
            </span>
          )}

          {/* 高亮片段 */}
          <p className="line-clamp-3 text-sm leading-7 text-foreground/80">
            {renderSnippet(result.content_snippet)}
          </p>
          <span className="mt-1 flex items-center gap-1 text-xs font-medium text-primary">打开原文 <ArrowUpRight className="size-3.5" /></span>
        </CardContent>
      </Card>
    </Link>
  );
}
