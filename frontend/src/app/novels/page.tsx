"use client";

import React, { useState, useMemo } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { NovelCard } from "@/components/novel-card";
import { NovelUploadDialog } from "@/components/novel-upload-dialog";
import { EmptyState } from "@/components/empty-state";
import { useNovels } from "@/hooks/use-novels";
import type { Novel } from "@/lib/api";

type SortOption = "date" | "title" | "wordCount";
type FilterStatus = "all" | Novel["status"];

export default function NovelsPage() {
  const { novels, loading, fetchNovels } = useNovels();
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState<SortOption>("date");
  const [filterStatus, setFilterStatus] = useState<FilterStatus>("all");

  const filteredNovels = useMemo(() => {
    let result = [...novels];

    // Search filter
    if (search.trim()) {
      const q = search.toLowerCase().trim();
      result = result.filter(
        (n) =>
          n.title.toLowerCase().includes(q) ||
          n.author.toLowerCase().includes(q)
      );
    }

    // Status filter
    if (filterStatus !== "all") {
      result = result.filter((n) => n.status === filterStatus);
    }

    // Sort
    switch (sortBy) {
      case "title":
        result.sort((a, b) => a.title.localeCompare(b.title, "zh"));
        break;
      case "date":
        result.sort(
          (a, b) =>
            new Date(b.created_at).getTime() -
            new Date(a.created_at).getTime()
        );
        break;
      case "wordCount":
        result.sort((a, b) => b.word_count - a.word_count);
        break;
    }

    return result;
  }, [novels, search, sortBy, filterStatus]);

  const handleUploadComplete = () => {
    fetchNovels();
  };

  const statusOptions: { value: FilterStatus; label: string }[] = [
    { value: "all", label: "\u5168\u90E8\u72B6\u6001" },
    { value: "importing", label: "\u5BFC\u5165\u4E2D" },
    { value: "ready", label: "\u5C31\u7EEA" },
    { value: "analyzing", label: "\u5206\u6790\u4E2D" },
    { value: "analyzed", label: "\u5DF2\u5206\u6790" },
  ];

  const sortOptions: { value: SortOption; label: string }[] = [
    { value: "date", label: "\u6700\u65B0\u4E0A\u4F20" },
    { value: "title", label: "\u6309\u6807\u9898" },
    { value: "wordCount", label: "\u6309\u5B57\u6570" },
  ];

  return (
    <div className="p-6 md:p-8 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold">{"\u6211\u7684\u4E66\u67B6"}</h2>
          <p className="text-muted-foreground mt-1">
            {"\u7BA1\u7406\u4F60\u7684\u5C0F\u8BF4\u5E93"}
            {novels.length > 0 && (
              <span className="ml-2 text-sm">
                ({novels.length} {"\u672C"})
              </span>
            )}
          </p>
        </div>

        <NovelUploadDialog onUploadComplete={handleUploadComplete}>
          <Button size="lg">+ {"\u5BFC\u5165\u5C0F\u8BF4"}</Button>
        </NovelUploadDialog>
      </div>

      {/* Search and filters */}
      <div className="flex flex-col sm:flex-row gap-3 mb-6">
        <div className="flex-1">
          <Input
            placeholder={"\u641C\u7D22\u5C0F\u8BF4\u6807\u9898\u3001\u4F5C\u8005..."}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-10"
          />
        </div>

        {/* Status filter */}
        <div className="flex gap-2">
          <select
            value={filterStatus}
            onChange={(e) =>
              setFilterStatus(e.target.value as FilterStatus)
            }
            className="h-10 rounded-lg border border-input bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring/50"
          >
            {statusOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>

          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as SortOption)}
            className="h-10 rounded-lg border border-input bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring/50"
          >
            {sortOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Loading state */}
      {loading && novels.length === 0 && (
        <div className="flex items-center justify-center py-20">
          <div className="text-center">
            <div className="text-4xl mb-4 animate-pulse">{"\u23F3"}</div>
            <p className="text-muted-foreground">{"\u52A0\u8F7D\u4E2D..."}</p>
          </div>
        </div>
      )}

      {/* Empty state */}
      {!loading && novels.length === 0 && (
        <EmptyState
          icon={"\uD83D\uDCDA"}
          title={"\u4E66\u67B6\u662F\u7A7A\u7684"}
          description={
            "\u5BFC\u5165\u4F60\u7684\u7B2C\u4E00\u672C\u5C0F\u8BF4\uFF0CAI \u5C06\u5E2E\u4F60\u6DF1\u5EA6\u7406\u89E3\u548C\u5206\u6790"
          }
          actionLabel={"\u5BFC\u5165 TXT \u6587\u4EF6"}
          onAction={() => {
            // Trigger the upload dialog programmatically via URL
            window.history.pushState(
              {},
              "",
              "/novels?action=import"
            );
            document.querySelector<HTMLButtonElement>(
              "[data-upload-trigger]"
            )?.click();
          }}
        />
      )}

      {/* No results after filtering */}
      {!loading && novels.length > 0 && filteredNovels.length === 0 && (
        <EmptyState
          icon={"\uD83D\uDD0D"}
          title={"\u6CA1\u6709\u627E\u5230\u5339\u914D\u7684\u5C0F\u8BF4"}
          description={"\u8BD5\u8BD5\u4FEE\u6539\u641C\u7D22\u6761\u4EF6\u6216\u7B5B\u9009\u5668"}
        />
      )}

      {/* Novel grid */}
      {filteredNovels.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
          {filteredNovels.map((novel) => (
            <NovelCard key={novel.id} novel={novel} />
          ))}
        </div>
      )}
    </div>
  );
}
