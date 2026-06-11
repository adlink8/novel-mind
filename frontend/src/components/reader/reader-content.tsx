"use client";

import React from "react";
import type { Chapter } from "@/lib/api";

interface ReaderContentProps {
  chapter: Chapter | null;
}

export function ReaderContent({ chapter }: ReaderContentProps) {
  if (!chapter) {
    return (
      <div className="flex items-center justify-center h-full text-muted-foreground">
        {"选择章节开始阅读"}
      </div>
    );
  }

  return (
    <article className="max-w-3xl mx-auto py-8 px-6">
      {/* 章节标题 */}
      <header className="mb-8 text-center">
        <h1 className="text-2xl font-bold mb-2">{chapter.title}</h1>
        <p className="text-sm text-muted-foreground">
          {chapter.word_count.toLocaleString()} {"字"}
        </p>
      </header>

      {/* 章节内容 */}
      <div className="prose prose-lg max-w-none leading-loose text-foreground/90 whitespace-pre-wrap">
        {chapter.content}
      </div>
    </article>
  );
}
