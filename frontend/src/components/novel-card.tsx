/**
 * 小说卡片组件 — 书架列表
 */

"use client";

import React, { useState } from "react";
import Link from "next/link";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  ArrowUpRight,
  BookOpenText,
  FileText,
  Trash2,
  LoaderCircle,
} from "lucide-react";
import type { Novel } from "@/lib/api";

interface NovelCardProps {
  novel: Novel;
  onDelete?: (id: number) => Promise<void> | void;
}

/** 状态展示：导入 / 索引 / 分析 */
function statusMeta(status: string): { label: string; className: string; hint: string } {
  switch (status) {
    case "importing":
      return {
        label: "导入中",
        className: "bg-yellow-100 text-yellow-800",
        hint: "正在解析章节",
      };
    case "chunking":
    case "embedding":
      return {
        label: "建索引中",
        className: "bg-blue-100 text-blue-800",
        hint: "正在建立检索索引，完成后才可语义搜索",
      };
    case "analyzing":
      return {
        label: "分析中",
        className: "bg-indigo-100 text-indigo-800",
        hint: "正在进行剧情/人物分析",
      };
    case "analyzed":
      return {
        label: "已分析",
        className: "bg-violet-100 text-violet-800",
        hint: "已完成 AI 分析",
      };
    case "ready":
    default:
      return {
        label: "可阅读",
        className: "bg-green-100 text-green-800",
        hint:
          status === "ready"
            ? "已分章入库。检索需完成索引；剧情分析需单独触发（当前多为未分析）"
            : "状态未知",
      };
  }
}

function getCoverTone(title: string): string {
  const tones = [
    "from-[#2d3431] to-[#59665f]",
    "from-[#51352f] to-[#9b5d47]",
    "from-[#27374d] to-[#526d82]",
    "from-[#443c68] to-[#766a9c]",
    "from-[#344d3f] to-[#6b806f]",
    "from-[#5d4935] to-[#9b7b58]",
  ];
  let hash = 0;
  for (let i = 0; i < title.length; i++) {
    hash = title.charCodeAt(i) + ((hash << 5) - hash);
  }
  return tones[Math.abs(hash) % tones.length];
}

function formatWordCount(count: number): string {
  if (count >= 10000) {
    return `${(count / 10000).toFixed(1)}万字`;
  }
  return `${count}字`;
}

export function NovelCard({ novel, onDelete }: NovelCardProps) {
  const [deleting, setDeleting] = useState(false);
  const meta = statusMeta(novel.status);
  const indexed = (novel.chunk_count ?? 0) > 0;
  const analyzed = novel.status === "analyzed";

  const handleDelete = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!onDelete) return;
    const ok = window.confirm(
      `确定删除《${novel.title}》？\n将移除章节与相关索引，此操作不可恢复。`
    );
    if (!ok) return;
    setDeleting(true);
    try {
      await onDelete(novel.id);
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="group relative h-full">
      <Link href={`/novels/${novel.id}`} className="block h-full">
        <Card className="paper-surface h-full cursor-pointer overflow-hidden rounded-3xl border-white/60 transition-all duration-300 group-hover:-translate-y-1 group-hover:border-primary/25 group-hover:shadow-[0_24px_60px_-35px_rgba(52,42,32,0.6)]">
          <div
            className={`relative h-40 overflow-hidden bg-gradient-to-br ${getCoverTone(novel.title)} p-5 text-white`}
          >
            <div className="absolute -right-8 -top-10 size-32 rounded-full border border-white/10" />
            <div className="absolute -bottom-12 -left-6 size-28 rounded-full bg-white/[0.06]" />
            <div className="relative flex h-full flex-col justify-between">
              <div className="flex items-center justify-between">
                <BookOpenText className="size-5 text-white/70" />
                <ArrowUpRight className="size-4 text-white/60 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
              </div>
              <p className="line-clamp-2 max-w-[85%] font-serif text-xl font-semibold leading-tight tracking-tight">
                {novel.title}
              </p>
            </div>
          </div>

          <CardHeader>
            <CardTitle className="line-clamp-1 font-serif text-lg">
              {novel.title}
            </CardTitle>
            <CardDescription className="line-clamp-1">
              {novel.author || "未知作者"}
            </CardDescription>
          </CardHeader>

          <CardContent>
            <div className="mb-3 flex flex-wrap items-center gap-2">
              {novel.genre && (
                <Badge variant="secondary" className="text-xs">
                  {novel.genre}
                </Badge>
              )}
              <span
                title={meta.hint}
                className={`inline-flex h-5 items-center rounded-4xl px-2 text-xs font-medium ${meta.className}`}
              >
                {meta.label}
              </span>
              <span
                title={
                  indexed
                    ? `已建检索索引（${novel.chunk_count} 块）`
                    : "尚未建检索索引，全局/语义搜索可能搜不到本书"
                }
                className={`inline-flex h-5 items-center rounded-4xl px-2 text-xs font-medium ${
                  indexed
                    ? "bg-emerald-50 text-emerald-800"
                    : "bg-orange-50 text-orange-800"
                }`}
              >
                {indexed ? "可检索" : "未建索引"}
              </span>
              <span
                title={
                  analyzed
                    ? "已完成剧情/人物等 AI 分析"
                    : "未做剧情分析（当前产品分析功能多为占位）"
                }
                className={`inline-flex h-5 items-center rounded-4xl px-2 text-xs font-medium ${
                  analyzed
                    ? "bg-violet-50 text-violet-800"
                    : "bg-slate-100 text-slate-600"
                }`}
              >
                {analyzed ? "已分析" : "未分析"}
              </span>
            </div>

            <div className="flex items-center justify-between border-t border-border/60 pt-3 text-xs text-muted-foreground">
              <span className="flex items-center gap-1.5">
                <BookOpenText className="size-3.5" />
                {novel.chapter_count} 章
              </span>
              <span className="flex items-center gap-1.5">
                <FileText className="size-3.5" />
                {formatWordCount(novel.word_count)}
              </span>
            </div>
          </CardContent>
        </Card>
      </Link>

      {onDelete && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={deleting}
          onClick={handleDelete}
          className="absolute right-3 top-3 z-10 h-8 rounded-full border-white/40 bg-black/35 px-2.5 text-white backdrop-blur hover:bg-red-600/90 hover:text-white"
          title="删除本书"
        >
          {deleting ? (
            <LoaderCircle className="size-3.5 animate-spin" />
          ) : (
            <Trash2 className="size-3.5" />
          )}
        </Button>
      )}
    </div>
  );
}
