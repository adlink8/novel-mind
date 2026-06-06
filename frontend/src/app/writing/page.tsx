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
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/empty-state";

// Placeholder fan fiction data
interface FanFictionDraft {
  id: string;
  title: string;
  sourceNovel: string;
  sourceNovelId: string;
  wordCount: number;
  status: "draft" | "writing" | "completed";
  lastEdited: string;
}

const placeholderDrafts: FanFictionDraft[] = [];

const statusLabels: Record<FanFictionDraft["status"], string> = {
  draft: "\u8349\u7A3F",
  writing: "\u521B\u4F5C\u4E2D",
  completed: "\u5DF2\u5B8C\u6210",
};

const statusStyles: Record<FanFictionDraft["status"], string> = {
  draft: "bg-gray-100 text-gray-700",
  writing: "bg-blue-100 text-blue-700",
  completed: "bg-green-100 text-green-700",
};

function formatWordCount(count: number): string {
  if (count >= 10000) {
    return `${(count / 10000).toFixed(1)}\u4E07\u5B57`;
  }
  return `${count}\u5B57`;
}

function formatDate(dateStr: string): string {
  if (!dateStr) return "";
  const date = new Date(dateStr);
  return date.toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export default function WritingPage() {
  const [drafts] = useState<FanFictionDraft[]>(placeholderDrafts);

  return (
    <div className="p-6 md:p-8 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold">{"\u521B\u4F5C\u4E2D\u5FC3"}</h2>
          <p className="text-muted-foreground mt-1">
            {"\u57FA\u4E8E\u539F\u4F5C\u98CE\u683C\uFF0C\u7EED\u5199\u4F60\u7684\u540C\u4EBA\u6545\u4E8B"}
          </p>
        </div>

        <Button size="lg" disabled>
          + {"\u65B0\u5EFA\u540C\u4EBA\u6587"}
        </Button>
      </div>

      {/* How it works section */}
      {drafts.length === 0 && (
        <Card className="mb-8">
          <CardContent>
            <h3 className="font-semibold mb-4">{"\u5982\u4F55\u5F00\u59CB\u521B\u4F5C\uFF1F"}</h3>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              <div className="flex gap-3">
                <div className="w-10 h-10 rounded-full bg-novel-100 flex items-center justify-center text-novel-600 font-bold shrink-0">
                  1
                </div>
                <div>
                  <h4 className="font-medium text-sm">
                    {"\u5BFC\u5165\u539F\u4F5C"}
                  </h4>
                  <p className="text-xs text-muted-foreground mt-1">
                    {"\u4E0A\u4F20\u4F60\u559C\u6B22\u7684\u5C0F\u8BF4\uFF0CAI \u4F1A\u81EA\u52A8\u5206\u6790\u98CE\u683C\u548C\u5185\u5BB9"}
                  </p>
                </div>
              </div>

              <div className="flex gap-3">
                <div className="w-10 h-10 rounded-full bg-novel-100 flex items-center justify-center text-novel-600 font-bold shrink-0">
                  2
                </div>
                <div>
                  <h4 className="font-medium text-sm">
                    {"\u9009\u62E9\u5206\u652F\u70B9"}
                  </h4>
                  <p className="text-xs text-muted-foreground mt-1">
                    {"\u9009\u62E9\u60F3\u8981\u6539\u5199\u7684\u5267\u60C5\u8282\u70B9\uFF0C\u6216\u521B\u5EFA\u5168\u65B0\u7684\u6545\u4E8B\u7EBF"}
                  </p>
                </div>
              </div>

              <div className="flex gap-3">
                <div className="w-10 h-10 rounded-full bg-novel-100 flex items-center justify-center text-novel-600 font-bold shrink-0">
                  3
                </div>
                <div>
                  <h4 className="font-medium text-sm">
                    {"AI \u8F85\u52A9\u521B\u4F5C"}
                  </h4>
                  <p className="text-xs text-muted-foreground mt-1">
                    {"\u4E0E AI \u534F\u4F5C\uFF0C\u751F\u6210\u7B26\u5408\u539F\u4F5C\u98CE\u683C\u7684\u65B0\u7BC7\u7AE0"}
                  </p>
                </div>
              </div>
            </div>

            <div className="mt-6 text-center">
              <Link href="/novels">
                <Button variant="outline">
                  {"\u524D\u5F80\u4E66\u67B6\u5BFC\u5165\u5C0F\u8BF4"}
                </Button>
              </Link>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Drafts list */}
      {drafts.length === 0 ? (
        <EmptyState
          icon={"\u270D\uFE0F"}
          title={"\u8FD8\u6CA1\u6709\u540C\u4EBA\u6587\u4F5C\u54C1"}
          description={
            "\u5BFC\u5165\u4E00\u672C\u5C0F\u8BF4\u540E\uFF0C\u5373\u53EF\u5F00\u59CB AI \u8F85\u52A9\u7684\u540C\u4EBA\u521B\u4F5C"
          }
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {drafts.map((draft) => (
            <Card
              key={draft.id}
              className="cursor-pointer hover:ring-2 hover:ring-novel-400/50 hover:shadow-lg transition-all"
            >
              <CardHeader>
                <div className="flex items-start justify-between">
                  <CardTitle className="line-clamp-1">
                    {draft.title}
                  </CardTitle>
                  <span
                    className={`inline-flex h-5 items-center rounded-4xl px-2 text-xs font-medium shrink-0 ${statusStyles[draft.status]}`}
                  >
                    {statusLabels[draft.status]}
                  </span>
                </div>
                <CardDescription className="line-clamp-1">
                  {"\u539F\u4F5C\uFF1A"}{draft.sourceNovel}
                </CardDescription>
              </CardHeader>

              <CardContent>
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  <span>{formatWordCount(draft.wordCount)}</span>
                  <span>
                    {"\u6700\u540E\u7F16\u8F91\uFF1A"}{formatDate(draft.lastEdited)}
                  </span>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
