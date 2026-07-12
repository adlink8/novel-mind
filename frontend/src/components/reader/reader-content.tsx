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
    <article className="mx-auto max-w-[760px] px-5 py-10 sm:px-8 sm:py-14">
      {/* 章节标题 */}
      <header className="mb-10 border-b border-border/60 pb-8 text-center sm:mb-14">
        <p className="mb-3 text-[11px] font-semibold uppercase tracking-[0.22em] text-primary">Chapter</p>
        <h1 className="mb-3 font-serif text-3xl font-semibold leading-tight tracking-tight sm:text-4xl">{chapter.title}</h1>
        <p className="text-xs text-muted-foreground">
          {chapter.word_count.toLocaleString()} {"字"}
        </p>
      </header>

      {/* 章节内容 */}
      <div className="whitespace-pre-wrap font-reading text-[17px] leading-[2.05] tracking-[0.015em] text-foreground/90 sm:text-[18px]">
        {chapter.content}
      </div>
    </article>
  );
}
