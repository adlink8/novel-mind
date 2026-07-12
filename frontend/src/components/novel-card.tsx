/**
 * 小说卡片组件
 *
 * 用于书架页的小说列表展示。每个卡片包含:
 * - 渐变色封面（基于标题哈希确定性生成）
 * - 标题和作者
 * - 类型标签和状态徽章
 * - 章节数和字数统计
 *
 * 点击卡片跳转到小说详情页 /novels/:id
 *
 * 渐变色算法: 对标题字符串做 hash，映射到 6 种预设渐变之一，
 * 保证同一本书每次渲染颜色一致。
 */

"use client";

import React from "react";
import Link from "next/link";
import {
  Card, CardContent, CardHeader, CardTitle, CardDescription,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ArrowUpRight, BookOpenText, FileText } from "lucide-react";
import type { Novel } from "@/lib/api";

interface NovelCardProps {
  novel: Novel;
}

/** 状态标签文字映射 */
const statusLabels: Record<Novel["status"], string> = {
  importing: "导入中",
  ready: "就绪",
  analyzing: "分析中",
  analyzed: "已分析",
};

/** 状态标签样式映射 */
const statusStyles: Record<Novel["status"], string> = {
  importing: "bg-yellow-100 text-yellow-800",
  ready: "bg-green-100 text-green-800",
  analyzing: "bg-blue-100 text-blue-800",
  analyzed: "bg-novel-100 text-novel-800",
};

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

/** 格式化字数显示（超过 1 万显示为 X.X 万字） */
function formatWordCount(count: number): string {
  if (count >= 10000) {
    return `${(count / 10000).toFixed(1)}万字`;
  }
  return `${count}字`;
}

export function NovelCard({ novel }: NovelCardProps) {
  return (
    <Link href={`/novels/${novel.id}`} className="group block h-full">
      <Card className="paper-surface h-full cursor-pointer overflow-hidden rounded-3xl border-white/60 transition-all duration-300 group-hover:-translate-y-1 group-hover:border-primary/25 group-hover:shadow-[0_24px_60px_-35px_rgba(52,42,32,0.6)]">
        <div className={`relative h-40 overflow-hidden bg-gradient-to-br ${getCoverTone(novel.title)} p-5 text-white`}>
          <div className="absolute -right-8 -top-10 size-32 rounded-full border border-white/10" />
          <div className="absolute -bottom-12 -left-6 size-28 rounded-full bg-white/[0.06]" />
          <div className="relative flex h-full flex-col justify-between">
            <div className="flex items-center justify-between"><BookOpenText className="size-5 text-white/70" /><ArrowUpRight className="size-4 text-white/60 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" /></div>
            <p className="line-clamp-2 max-w-[85%] font-serif text-xl font-semibold leading-tight tracking-tight">{novel.title}</p>
          </div>
        </div>

        <CardHeader>
          <CardTitle className="line-clamp-1 font-serif text-lg">{novel.title}</CardTitle>
          <CardDescription className="line-clamp-1">
            {novel.author || "未知作者"}
          </CardDescription>
        </CardHeader>

        <CardContent>
          <div className="flex flex-wrap items-center gap-2 mb-3">
            {novel.genre && (
              <Badge variant="secondary" className="text-xs">
                {novel.genre}
              </Badge>
            )}
            <span
              className={`inline-flex h-5 items-center rounded-4xl px-2 text-xs font-medium ${statusStyles[novel.status]}`}
            >
              {statusLabels[novel.status]}
            </span>
          </div>

          <div className="flex items-center justify-between border-t border-border/60 pt-3 text-xs text-muted-foreground">
            <span className="flex items-center gap-1.5"><BookOpenText className="size-3.5" />{novel.chapter_count} 章</span>
            <span className="flex items-center gap-1.5"><FileText className="size-3.5" />{formatWordCount(novel.word_count)}</span>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
