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

/**
 * 根据标题生成确定性渐变色
 * 使用 DJB2 哈希算法，保证同一标题每次返回相同颜色
 */
function getGradient(title: string): string {
  const gradients = [
    "from-novel-400 to-novel-600",
    "from-purple-400 to-indigo-600",
    "from-pink-400 to-rose-600",
    "from-blue-400 to-cyan-600",
    "from-emerald-400 to-teal-600",
    "from-amber-400 to-orange-600",
  ];
  let hash = 0;
  for (let i = 0; i < title.length; i++) {
    hash = title.charCodeAt(i) + ((hash << 5) - hash);
  }
  return gradients[Math.abs(hash) % gradients.length];
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
    <Link href={`/novels/${novel.id}`}>
      <Card className="h-full cursor-pointer hover:ring-2 hover:ring-novel-400/50 hover:shadow-lg transition-all">
        {/* 渐变色封面占位 */}
        <div
          className={`h-32 bg-gradient-to-br ${getGradient(novel.title)} flex items-center justify-center`}
        >
          <span className="text-4xl text-white/80">{"📖"}</span>
        </div>

        <CardHeader>
          <CardTitle className="line-clamp-1">{novel.title}</CardTitle>
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

          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>{novel.chapter_count} 章</span>
            <span>{formatWordCount(novel.word_count)}</span>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
