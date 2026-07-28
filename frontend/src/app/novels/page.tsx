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
import { Skeleton } from "@/components/ui/skeleton";
import { BookShelf } from "@/components/bookshelf/book-shelf";
import { NovelUploadDialog } from "@/components/novel-upload-dialog";
import { EmptyState } from "@/components/empty-state";
import { useNovels } from "@/hooks/use-novels";
import type { Novel } from "@/lib/api";
import { BookOpenText, CheckSquare, Plus, Search, SlidersHorizontal, Trash2, X } from "lucide-react";
import { PageContainer, PageHeader } from "@/components/page-header";

/** 排序选项类型：按日期、按标题、按字数 */
type SortOption = "date" | "title" | "wordCount";

/** 筛选状态类型：全部或特定小说状态 */
type FilterStatus = "all" | string;

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
  const { novels, loading, fetchNovels, deleteNovel, deleteNovels, renameNovel } = useNovels();
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState<SortOption>("date");
  const [filterStatus, setFilterStatus] = useState<FilterStatus>("all");
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [bulkDeleting, setBulkDeleting] = useState(false);

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
    { value: "chunking", label: "建索引中" },
    { value: "embedding", label: "向量化中" },
    { value: "ready", label: "可阅读" },
    { value: "analyzing", label: "分析中" },
    { value: "analyzed", label: "已分析" },
  ];

  const handleDelete = async (id: number) => {
    await deleteNovel(String(id));
  };

  const handleSelectedChange = (id: number, selected: boolean) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (selected) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  const exitSelectionMode = () => {
    setSelectionMode(false);
    setSelectedIds(new Set());
  };

  const handleBulkDelete = async () => {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    const ok = window.confirm(
      `确定删除选中的 ${ids.length} 本书？\n将同时移除章节与相关索引，此操作不可恢复。`
    );
    if (!ok) return;
    setBulkDeleting(true);
    try {
      await deleteNovels(ids);
      exitSelectionMode();
    } finally {
      setBulkDeleting(false);
    }
  };

  /** 排序下拉框选项 */
  const sortOptions: { value: SortOption; label: string }[] = [
    { value: "date", label: "最新上传" },
    { value: "title", label: "按标题" },
    { value: "wordCount", label: "按字数" },
  ];

  return (
    <PageContainer className="space-y-7">
      {/* ========== 页面头部：标题 + 上传按钮 ========== */}
      <PageHeader eyebrow="Library" title="我的书架" description={`管理已导入的长篇文本${novels.length > 0 ? `，当前共 ${novels.length} 本作品` : "，从第一本作品开始建立故事记忆"}`} action={
        <div className="flex flex-wrap justify-end gap-2">
          {novels.length > 0 && (
            <Button
              type="button"
              variant="outline"
              size="lg"
              className="rounded-full px-5"
              onClick={() => selectionMode ? exitSelectionMode() : setSelectionMode(true)}
            >
              {selectionMode ? <X className="mr-2 size-4" /> : <CheckSquare className="mr-2 size-4" />}
              {selectionMode ? "退出批量管理" : "批量管理"}
            </Button>
          )}
          <NovelUploadDialog onUploadComplete={handleUploadComplete}>
            <Button size="lg" className="rounded-full px-5" data-upload-trigger><Plus className="mr-2 size-4" />批量导入</Button>
          </NovelUploadDialog>
        </div>
      } />

      {selectionMode && (
        <div className="paper-surface flex flex-wrap items-center gap-3 rounded-2xl px-4 py-3" role="toolbar" aria-label="批量管理书籍">
          <span className="mr-auto text-sm font-medium">已选择 {selectedIds.size} 本</span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setSelectedIds(new Set(filteredNovels.map((novel) => novel.id)))}
          >
            全选当前结果
          </Button>
          <Button
            type="button"
            variant="destructive"
            size="sm"
            disabled={selectedIds.size === 0 || bulkDeleting}
            onClick={() => void handleBulkDelete()}
          >
            <Trash2 className="mr-2 size-4" />
            {bulkDeleting ? "删除中..." : "删除所选"}
          </Button>
        </div>
      )}

      {/* ========== 搜索栏和筛选/排序控件 ========== */}
      <div className="paper-surface flex flex-col gap-3 rounded-3xl p-3 sm:flex-row sm:items-center">
        {/* 搜索输入框 - 搜索标题和作者 */}
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder={"搜索小说标题、作者..."}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-11 rounded-2xl border-0 bg-transparent pl-10 shadow-none focus-visible:ring-1"
          />
        </div>

        {/* 状态筛选和排序下拉框 */}
        <div className="flex gap-2 border-t border-border/60 pt-3 sm:border-l sm:border-t-0 sm:pl-3 sm:pt-0">
          <SlidersHorizontal className="mt-3 hidden size-4 text-muted-foreground sm:block" />
          {/* 状态筛选下拉框 */}
          <select
            value={filterStatus}
            onChange={(e) =>
              setFilterStatus(e.target.value as FilterStatus)
            }
            className="h-10 min-w-0 flex-1 cursor-pointer rounded-xl border border-border bg-card px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring/30 sm:flex-none"
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
            className="h-10 min-w-0 flex-1 cursor-pointer rounded-xl border border-border bg-card px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring/30 sm:flex-none"
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
      {/* 仅在首次加载（列表为空且正在加载）时显示书架形态骨架 */}
      {loading && novels.length === 0 && (
        <div className="rounded-[20px] bg-gradient-to-b from-[#e6cda1] to-[#b98f5c] p-3 shadow-[0_24px_50px_-30px_rgba(90,65,30,0.45)]">
          <div className="flex min-h-56 flex-wrap items-end justify-start gap-2.5 rounded-xl bg-[#eee0c2] px-4 pb-2 pt-10">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton
                key={i}
                className="w-10 rounded-[3px] bg-[#d9c49c]"
                style={{ height: 150 + (i % 4) * 18 }}
              />
            ))}
          </div>
        </div>
      )}

      {/* ========== 空状态：未导入任何小说 ========== */}
      {/* 引导用户上传第一本小说 */}
      {!loading && novels.length === 0 && (
        <EmptyState
          icon={<BookOpenText className="size-6" />}
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
          icon={<Search className="size-6" />}
          title={"没有找到匹配的小说"}
          description={"试试修改搜索条件或筛选器"}
        />
      )}

      {/* ========== 仿真书架 ========== */}
      {/* 已入库的书竖立摆上木框层架；点击书本播放取出动画后进入阅读 */}
      {filteredNovels.length > 0 && (
        <BookShelf
          novels={filteredNovels}
          onDelete={handleDelete}
          onRename={renameNovel}
          selectionMode={selectionMode}
          selectedIds={selectedIds}
          onSelectedChange={handleSelectedChange}
        />
      )}
    </PageContainer>
  );
}
