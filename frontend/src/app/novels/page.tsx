/**
 * 书架页面 - app/novels/page.tsx
 * ================================
 * 用户的小说库管理页面，支持搜索、筛选、排序和导入小说。
 *
 * 主要职责：
 * 1. 展示用户导入的所有小说（卡片网格布局）
 * 2. 提供搜索功能（按标题/作者名模糊匹配）
 * 3. 提供状态筛选（全部/导入中/就绪/分析中/已分析）
 * 4. 提供排序功能（按上传时间/标题/字数）
 * 5. 空状态引导（未导入时引导用户上传，筛选无结果时提示修改条件）
 * 6. 集成上传对话框组件
 *
 * 数据流：
 * - 通过 useNovels Hook 从 Zustand store 获取小说列表
 * - 搜索/筛选/排序在前端通过 useMemo 计算派生数据
 * - 上传完成后自动刷新列表
 *
 * 布局：响应式网格，1-4 列自适应
 */

/**
 * 书架页 (Novels Page)
 *
 * 功能:
 * 1. 小说列表展示（卡片网格布局）
 * 2. 搜索过滤（标题/作者模糊匹配）
 * 3. 状态筛选（全部/导入中/就绪/分析中/已分析）
 * 4. 排序（最新上传/按标题/按字数）
 * 5. 上传小说（通过 NovelUploadDialog）
 *
 * 数据流:
 * useNovels() → Zustand Store → novelsApi.list() → GET /api/novels
 */

"use client";

import React, { useState, useMemo } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { NovelCard } from "@/components/novel-card";
import { NovelUploadDialog } from "@/components/novel-upload-dialog";
import { EmptyState } from "@/components/empty-state";
import { useNovels } from "@/hooks/use-novels";
import type { Novel } from "@/lib/api";

/** 排序选项类型：按日期、按标题、按字数 */
type SortOption = "date" | "title" | "wordCount";

/** 筛选状态类型：全部或特定小说状态 */
type FilterStatus = "all" | Novel["status"];

/**
 * 书架页面组件
 * 
 * 状态管理：
 * - search: 搜索关键词（string）
 * - sortBy: 当前排序方式（SortOption），默认按日期降序
 * - filterStatus: 当前筛选状态（FilterStatus），默认"全部"
 */
export default function NovelsPage() {
  // 从 Hook 获取小说列表、加载状态和刷新方法
  const { novels, loading, fetchNovels } = useNovels();
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState<SortOption>("date");
  const [filterStatus, setFilterStatus] = useState<FilterStatus>("all");

  /**
   * 派生数据：根据搜索、筛选、排序条件计算最终显示的小说列表
   * 使用 useMemo 缓存，仅在依赖项变化时重新计算
   * 
   * 处理流程：
   * 1. 复制原始列表（避免修改原数组）
   * 2. 按搜索关键词过滤（匹配标题或作者名，不区分大小写）
   * 3. 按状态过滤
   * 4. 根据排序方式排序
   */
  const filteredNovels = useMemo(() => {
    let result = [...novels];

    // 搜索过滤：模糊匹配标题或作者名
    if (search.trim()) {
      const q = search.toLowerCase().trim();
      result = result.filter(
        (n) =>
          n.title.toLowerCase().includes(q) ||
          (n.author ?? "").toLowerCase().includes(q)
      );
    }

    // 状态过滤：仅显示匹配状态的小说
    if (filterStatus !== "all") {
      result = result.filter((n) => n.status === filterStatus);
    }

    // 排序：根据选择的排序方式对结果排序
    switch (sortBy) {
      case "title":
        // 按标题排序，使用中文 locale 确保正确排序
        result.sort((a, b) => a.title.localeCompare(b.title, "zh"));
        break;
      case "date":
        // 按创建时间降序（最新的在前）
        result.sort(
          (a, b) =>
            new Date(b.created_at).getTime() -
            new Date(a.created_at).getTime()
        );
        break;
      case "wordCount":
        // 按字数降序（字数最多的在前）
        result.sort((a, b) => b.word_count - a.word_count);
        break;
    }

    return result;
  }, [novels, search, sortBy, filterStatus]);

  /** 上传完成回调 - 触发列表刷新 */
  const handleUploadComplete = () => {
    fetchNovels();
  };

  // ============================================================
  // 筛选和排序选项配置
  // ============================================================

  /** 状态筛选下拉框选项 */
  const statusOptions: { value: FilterStatus; label: string }[] = [
    { value: "all", label: "全部状态" },
    { value: "importing", label: "导入中" },
    { value: "ready", label: "就绪" },
    { value: "analyzing", label: "分析中" },
    { value: "analyzed", label: "已分析" },
  ];

  /** 排序下拉框选项 */
  const sortOptions: { value: SortOption; label: string }[] = [
    { value: "date", label: "最新上传" },
    { value: "title", label: "按标题" },
    { value: "wordCount", label: "按字数" },
  ];

  return (
    <div className="p-6 md:p-8 max-w-6xl mx-auto">
      {/* ========== 页面头部：标题 + 上传按钮 ========== */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold">{"我的书架"}</h2>
          <p className="text-muted-foreground mt-1">
            {"管理你的小说库"}
            {/* 显示小说总数（仅在有数据时显示） */}
            {novels.length > 0 && (
              <span className="ml-2 text-sm">
                ({novels.length} {"本"})
              </span>
            )}
          </p>
        </div>

        {/* 上传小说对话框触发按钮 */}
        <NovelUploadDialog onUploadComplete={handleUploadComplete}>
          <Button size="lg">+ {"导入小说"}</Button>
        </NovelUploadDialog>
      </div>

      {/* ========== 搜索栏和筛选/排序控件 ========== */}
      <div className="flex flex-col sm:flex-row gap-3 mb-6">
        {/* 搜索输入框 - 搜索标题和作者 */}
        <div className="flex-1">
          <Input
            placeholder={"搜索小说标题、作者..."}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-10"
          />
        </div>

        {/* 状态筛选和排序下拉框 */}
        <div className="flex gap-2">
          {/* 状态筛选下拉框 */}
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

          {/* 排序方式下拉框 */}
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

      {/* ========== 加载中状态 ========== */}
      {/* 仅在首次加载（列表为空且正在加载）时显示加载动画 */}
      {loading && novels.length === 0 && (
        <div className="flex items-center justify-center py-20">
          <div className="text-center">
            <div className="text-4xl mb-4 animate-pulse">{"⏳"}</div>
            <p className="text-muted-foreground">{"加载中..."}</p>
          </div>
        </div>
      )}

      {/* ========== 空状态：未导入任何小说 ========== */}
      {/* 引导用户上传第一本小说 */}
      {!loading && novels.length === 0 && (
        <EmptyState
          icon={"📚"}
          title={"书架是空的"}
          description={
            "导入你的第一本小说，AI 将帮你深度理解和分析"
          }
          actionLabel={"导入 TXT 文件"}
          onAction={() => {
            // 通过 URL 参数和 DOM 查询触发上传对话框
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

      {/* ========== 筛选无结果状态 ========== */}
      {/* 有小说但搜索/筛选后无匹配结果时显示 */}
      {!loading && novels.length > 0 && filteredNovels.length === 0 && (
        <EmptyState
          icon={"🔍"}
          title={"没有找到匹配的小说"}
          description={"试试修改搜索条件或筛选器"}
        />
      )}

      {/* ========== 小说卡片网格 ========== */}
      {/* 响应式网格布局：1列(sm) -> 2列(md) -> 3列(lg) -> 4列(xl) */}
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
