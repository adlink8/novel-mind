"use client";

import React from "react";
import Link from "next/link";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { Novel } from "@/lib/api";

interface NovelCardProps {
  novel: Novel;
}

const statusLabels: Record<Novel["status"], string> = {
  importing: "\u5BFC\u5165\u4E2D",
  ready: "\u5C31\u7EEA",
  analyzing: "\u5206\u6790\u4E2D",
  analyzed: "\u5DF2\u5206\u6790",
};

const statusStyles: Record<Novel["status"], string> = {
  importing: "bg-yellow-100 text-yellow-800",
  ready: "bg-green-100 text-green-800",
  analyzing: "bg-blue-100 text-blue-800",
  analyzed: "bg-novel-100 text-novel-800",
};

// Generate a deterministic gradient based on novel title
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

function formatWordCount(count: number): string {
  if (count >= 10000) {
    return `${(count / 10000).toFixed(1)}\u4E07\u5B57`;
  }
  return `${count}\u5B57`;
}

export function NovelCard({ novel }: NovelCardProps) {
  return (
    <Link href={`/novels/${novel.id}`}>
      <Card className="h-full cursor-pointer hover:ring-2 hover:ring-novel-400/50 hover:shadow-lg transition-all">
        {/* Gradient cover placeholder */}
        <div
          className={`h-32 bg-gradient-to-br ${getGradient(novel.title)} flex items-center justify-center`}
        >
          <span className="text-4xl text-white/80">{"\uD83D\uDCD6"}</span>
        </div>

        <CardHeader>
          <CardTitle className="line-clamp-1">{novel.title}</CardTitle>
          <CardDescription className="line-clamp-1">
            {novel.author}
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
            <span>{novel.chapter_count} \u7AE0</span>
            <span>{formatWordCount(novel.word_count)}</span>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
