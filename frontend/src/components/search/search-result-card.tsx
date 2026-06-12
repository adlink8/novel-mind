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

interface SearchResultCardProps {
  result: SearchResult;
}

/** 将 content_snippet 中的 <mark> 标签渲染为高亮 <span> */
function renderSnippet(html: string) {
  return (
    <span
      dangerouslySetInnerHTML={{
        __html: html.replace(
          /<mark>/g,
          '<mark class="bg-yellow-200 text-yellow-900 rounded-sm px-0.5 dark:bg-yellow-500/30 dark:text-yellow-200">'
        ),
      }}
    />
  );
}

/** 构建阅读页链接 */
function buildReadUrl(
  novelId: number,
  chapterId: number | null,
  chunkIndex: number
): string {
  if (chapterId !== null) {
    return `/novels/${novelId}/read?chapter=${chapterId}&chunk=${chunkIndex}`;
  }
  return `/novels/${novelId}/read?chunk=${chunkIndex}`;
}

export function SearchResultCard({ result }: SearchResultCardProps) {
  return (
    <Link href={buildReadUrl(result.novel_id, result.chapter_id, result.chunk_index)}>
      <Card className="cursor-pointer transition-colors hover:bg-muted/50">
        <CardContent className="flex flex-col gap-1.5 p-4">
          {/* 小说名 + 分数 */}
          <div className="flex items-center justify-between gap-2">
            <span className="font-semibold text-sm truncate">
              {result.novel_title ?? `小说 #${result.novel_id}`}
            </span>
            <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
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
          <p className="text-sm leading-relaxed line-clamp-3">
            {renderSnippet(result.content_snippet)}
          </p>
        </CardContent>
      </Card>
    </Link>
  );
}
