/**
 * 小说卡片组件 — 书架列表
 *
 * Phase 08 编排：卡片「全部分析」入口导向全局分析工作台（/analysis），
 * 不再把 plot_summary 当成主产品。层级准备由后端 start-or-resume 负责。
 */

"use client";

import React, { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  ArrowUpRight,
  BookOpenText,
  FileText,
  Trash2,
  LoaderCircle,
  Pencil,
} from "lucide-react";
import type { Novel } from "@/lib/api";

interface NovelCardProps {
  novel: Novel;
  onDelete?: (id: number) => Promise<void> | void;
  onRename?: (id: number, title: string) => Promise<void> | void;
  selectionMode?: boolean;
  selected?: boolean;
  onSelectedChange?: (id: number, selected: boolean) => void;
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
    case "indexing_failed":
      return {
        label: "索引失败",
        className: "bg-red-100 text-red-800",
        hint: "检索索引未完成，请重新建立索引后再使用搜索",
      };
    case "analyzing":
      return {
        label: "时间线分析中",
        className: "bg-indigo-100 text-indigo-800",
        hint: "Phase 08：正在基于场景层级抽取时间线事件",
      };
    case "analyzed":
      return {
        label: "已有时间线",
        className: "bg-violet-100 text-violet-800",
        hint: "时间线分析已完成，可在分析页查看",
      };
    case "ready":
    default:
      return {
        label: "可阅读",
        className: "bg-green-100 text-green-800",
        hint:
          status === "ready"
            ? "已分章入库。检索需建索引；时间线分析请进入分析页（首次会自动启动）"
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

/** 封面朱砂印章字：按书籍状态取一个单字（传统印章白文风格） */
function sealChar(status: string): string {
  switch (status) {
    case "importing":
      return "入";
    case "chunking":
    case "embedding":
      return "索";
    case "indexing_failed":
      return "错";
    case "analyzing":
      return "析";
    case "analyzed":
      return "线";
    case "ready":
    default:
      return "读";
  }
}

function formatWordCount(count: number): string {
  if (count >= 10000) {
    return `${(count / 10000).toFixed(1)}万字`;
  }
  return `${count}字`;
}

export function NovelCard({
  novel,
  onDelete,
  onRename,
  selectionMode = false,
  selected = false,
  onSelectedChange,
}: NovelCardProps) {
  const router = useRouter();
  const [deleting, setDeleting] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameValue, setRenameValue] = useState(novel.title);
  const [renaming, setRenaming] = useState(false);
  const [renameError, setRenameError] = useState("");
  const meta = statusMeta(novel.status);
  const indexed = (novel.chunk_count ?? 0) > 0;
  const analyzed = novel.status === "analyzed" || novel.status === "analyzing";

  const handleOpenTimeline = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    // 全部分析入口：全流程任务在分析工作台中启动并显示阶段进度
    router.push(`/analysis?novel=${novel.id}`);
  };

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

  const handleRename = async () => {
    const title = renameValue.trim();
    if (!title) {
      setRenameError("书籍名称不能为空");
      return;
    }
    if (title === novel.title) {
      setRenameOpen(false);
      return;
    }
    if (!onRename) return;
    setRenaming(true);
    setRenameError("");
    try {
      await onRename(novel.id, title);
      setRenameOpen(false);
    } catch {
      setRenameError("保存失败，请稍后重试");
    } finally {
      setRenaming(false);
    }
  };

  return (
    <div className="group">
      <Card className={`paper-surface flex flex-col overflow-hidden rounded-3xl border-border/70 transition-[border-color,box-shadow,transform] motion-duration-standard motion-ease-enter hover:-translate-y-0.5 hover:border-primary/25 hover:shadow-[0_24px_60px_-35px_rgba(52,42,32,0.6)] focus-within:border-primary/25 focus-within:shadow-[0_24px_60px_-35px_rgba(52,42,32,0.6)] ${selected ? "ring-2 ring-primary/50" : ""}`}>
        <Link href={`/novels/${novel.id}`} className="block cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <div
            className={`relative h-40 overflow-hidden bg-gradient-to-br ${getCoverTone(novel.title)} p-5 text-white`}
          >
            {/* 书脊：左侧装订阴影 + 高光，模拟实体书脊 */}
            <div aria-hidden className="absolute inset-y-0 left-0 w-2 bg-black/30" />
            <div aria-hidden className="absolute inset-y-0 left-2 w-px bg-white/15" />
            <div className="absolute -right-8 -top-10 size-32 rounded-full border border-white/10" />
            <div className="absolute -bottom-12 -left-6 size-28 rounded-full bg-white/[0.06]" />
            {/* 朱砂印章：状态单字，白文风格 */}
            <span
              aria-hidden
              className="absolute bottom-4 right-4 grid size-9 rotate-3 place-items-center rounded-[4px] border border-white/60 bg-[#b03a2e]/90 font-serif text-base font-semibold text-white shadow-sm"
            >
              {sealChar(novel.status)}
            </span>
            <div className="relative flex h-full flex-col justify-between">
              <div className="flex items-center justify-between">
                <BookOpenText className="size-5 text-white/70" />
                <ArrowUpRight className="size-4 text-white/60 transition-[color,opacity] motion-duration-fast motion-ease-enter group-hover:text-white group-focus-within:text-white" />
              </div>
              <p className="line-clamp-2 max-w-[85%] font-serif text-xl font-semibold leading-tight tracking-tight">
                {novel.title}
              </p>
            </div>
          </div>
        </Link>

        <Link href={`/novels/${novel.id}`} className="block cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
          <CardHeader>
            <CardTitle className="line-clamp-1 font-serif text-lg">
              {novel.title}
            </CardTitle>
            <CardDescription className="line-clamp-1">
              {novel.author || "未知作者"}
            </CardDescription>
          </CardHeader>
        </Link>

        <CardContent>
            <div className="mb-3 flex flex-wrap items-center gap-2">
              {novel.genre && (
                <Badge variant="secondary" className="text-xs">
                  {novel.genre}
                </Badge>
              )}
              <span
                title={meta.hint}
                className={`inline-flex h-5 items-center rounded-full px-2 text-xs font-medium ${meta.className}`}
              >
                {meta.label}
              </span>
              <span
                title={
                  indexed
                    ? `已建检索索引（${novel.chunk_count} 块）`
                    : "尚未建检索索引，全局/语义搜索可能搜不到本书"
                }
                className={`inline-flex h-5 items-center rounded-full px-2 text-xs font-medium ${
                  indexed
                    ? "bg-emerald-50 text-emerald-800"
                    : "bg-orange-50 text-orange-800"
                }`}
              >
                {indexed ? "可检索" : "未建索引"}
              </span>
              <span
                title={
                  novel.status === "analyzed"
                    ? "时间线已完成（Phase 08）"
                    : novel.status === "analyzing"
                      ? "时间线任务进行中"
                      : "未做时间线分析：点击「时间线」进入分析页自动启动"
                }
                className={`inline-flex h-5 items-center rounded-full px-2 text-xs font-medium ${
                  analyzed
                    ? "bg-violet-50 text-violet-800"
                    : "bg-slate-100 text-slate-600"
                }`}
              >
                {novel.status === "analyzed"
                  ? "时间线就绪"
                  : novel.status === "analyzing"
                    ? "抽取中"
                    : "未分析"}
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

        <div className="flex min-h-14 items-center gap-2 border-t border-border/60 px-4 py-3">
          {selectionMode && (
            <label className="mr-auto inline-flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
              <input
                type="checkbox"
                checked={selected}
                onChange={(event) => onSelectedChange?.(novel.id, event.target.checked)}
                className="size-4 cursor-pointer rounded border-border accent-primary"
                aria-label={`选择《${novel.title}》`}
              />
              选择
            </label>
          )}
          {!selectionMode && <span className="mr-auto text-xs text-muted-foreground">书籍管理</span>}

          {onRename && (
            <Dialog open={renameOpen} onOpenChange={(open) => {
              setRenameOpen(open);
              if (open) {
                setRenameValue(novel.title);
                setRenameError("");
              }
            }}>
              <DialogTrigger render={<Button type="button" variant="outline" size="icon" className="rounded-xl" aria-label={`重命名《${novel.title}》`}><Pencil className="size-3.5" /></Button>} />
              <DialogContent className="rounded-3xl sm:max-w-md">
                <DialogHeader>
                  <DialogTitle>更改书籍名称</DialogTitle>
                  <DialogDescription>只修改书架显示名称，不会改变章节内容。</DialogDescription>
                </DialogHeader>
                <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); void handleRename(); }}>
                  <label className="block space-y-2 text-sm font-medium">
                    书籍名称
                    <Input
                      value={renameValue}
                      onChange={(event) => setRenameValue(event.target.value)}
                      maxLength={200}
                      autoFocus
                    />
                  </label>
                  {renameError && <p className="text-sm text-destructive" role="alert">{renameError}</p>}
                  <div className="flex justify-end gap-2">
                    <Button type="button" variant="outline" onClick={() => setRenameOpen(false)}>取消</Button>
                    <Button type="submit" disabled={renaming}>{renaming ? "保存中..." : "保存名称"}</Button>
                  </div>
                </form>
              </DialogContent>
            </Dialog>
          )}
          <Button type="button" variant="outline" size="sm" onClick={handleOpenTimeline} className="rounded-xl" title="打开全部分析">全部分析</Button>
          {onDelete && !selectionMode && <Button type="button" variant="outline" size="icon" disabled={deleting} onClick={handleDelete} className="rounded-xl text-destructive hover:bg-destructive/10 hover:text-destructive" title="删除本书" aria-label={`删除《${novel.title}》`}>{deleting ? <LoaderCircle className="size-3.5 animate-spin" /> : <Trash2 className="size-3.5" />}</Button>}
        </div>
      </Card>
    </div>
  );
}
