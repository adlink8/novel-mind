"use client";

import { useEffect, useRef, useState } from "react";
import { Bookmark as BookmarkIcon, LoaderCircle, Plus, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { Chapter, ReaderBookmark } from "@/lib/api";
import { novelsApi } from "@/lib/api";
import { useDismissableLayer } from "@/lib/use-dismissable-layer";
import { cn } from "@/lib/utils";

interface ReaderBookmarksProps {
  novelId: string;
  chapters: Chapter[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 跳转到书签所在章节 + 章内位置 */
  onNavigate: (bookmark: ReaderBookmark) => void;
  /** 当前阅读章节（用于「在此添加书签」） */
  currentChapterId: number;
  /** 当前章内进度百分比 0-100 */
  currentPercent: number;
}

export function ReaderBookmarks({
  novelId,
  chapters,
  open,
  onOpenChange,
  onNavigate,
  currentChapterId,
  currentPercent,
}: ReaderBookmarksProps) {
  const layerRef = useRef<HTMLElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [bookmarks, setBookmarks] = useState<ReaderBookmark[]>([]);
  const [loading, setLoading] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  /** 添加书签表单（label / note） */
  const [adding, setAdding] = useState(false);
  const [saving, setSaving] = useState(false);
  const [label, setLabel] = useState("");
  const [note, setNote] = useState("");
  const { present, closing } = useDismissableLayer({
    open,
    onDismiss: () => onOpenChange(false),
    layerRef,
    triggerRef,
  });

  useEffect(() => {
    if (!open) return;
    let active = true;
    // Opening the panel starts an external request; reset its request state.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    setError(null);
    novelsApi
      .listBookmarks(novelId)
      .then((response) => {
        if (active) setBookmarks(response.data);
      })
      .catch((requestError: unknown) => {
        if (!active) return;
        const status = (
          requestError as { response?: { status?: number } }
        ).response?.status;
        if (status === 401) {
          setError("登录状态已失效，请重新登录");
        } else if (status === 404) {
          setError("后端书签接口未更新，请重启后端服务");
        } else {
          setError("书签加载失败，请确认后端服务正常");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [novelId, open]);

  const chapterTitle = (chapterId: number): string =>
    chapters.find((item) => item.id === chapterId)?.title ??
    `第 ${chapterId} 章`;

  const handleDelete = async (bookmarkId: number) => {
    setDeletingId(bookmarkId);
    setError(null);
    try {
      await novelsApi.deleteBookmark(novelId, bookmarkId);
      setBookmarks((current) =>
        current.filter((item) => item.id !== bookmarkId)
      );
    } catch {
      setError("书签删除失败，请重试");
    } finally {
      setDeletingId(null);
    }
  };

  const handleAdd = async () => {
    if (!currentChapterId) return;
    setSaving(true);
    setError(null);
    try {
      const res = await novelsApi.createBookmark(novelId, {
        chapter_id: currentChapterId,
        position_percent: Math.round(currentPercent * 10) / 10,
        label: label.trim() || null,
        note: note.trim() || null,
      });
      setBookmarks((current) => [res.data, ...current]);
      setLabel("");
      setNote("");
      setAdding(false);
    } catch {
      setError("书签保存失败，请重试");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="relative">
      <Button
        ref={triggerRef}
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => onOpenChange(!open)}
        aria-expanded={open}
        aria-controls="reader-bookmarks-panel"
        aria-label="书签"
        title="书签"
        data-testid="reader-bookmarks-open"
      >
        <BookmarkIcon className="size-4" />
      </Button>

      {present ? (
        <section
          ref={layerRef}
          id="reader-bookmarks-panel"
          aria-label="书签列表"
          aria-hidden={closing || undefined}
          className={cn(
            "absolute right-0 top-[calc(100%+0.6rem)] z-50 w-[min(24rem,calc(100vw-2rem))] rounded-2xl border border-border bg-card p-3 text-card-foreground shadow-2xl transition-[opacity,transform] motion-duration-spatial motion-ease-enter",
            open && !closing
              ? "translate-y-0 opacity-100"
              : "pointer-events-none -translate-y-1 opacity-0 motion-ease-exit"
          )}
        >
          <div className="mb-2 flex items-center justify-between px-1">
            <div>
              <h2 className="text-sm font-semibold">书签</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                保存当前阅读位置
              </p>
            </div>
            <span className="text-xs tabular-nums text-muted-foreground">
              {bookmarks.length}
            </span>
          </div>

          {error ? (
            <p className="mb-2 rounded-xl bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {error}
            </p>
          ) : null}

          {/* 添加书签表单 */}
          {adding ? (
            <div className="mb-2 space-y-2 rounded-xl border border-border/70 p-2.5">
              <Input
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="标签（可选）"
                aria-label="书签标签"
                maxLength={200}
              />
              <Textarea
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="备注（可选）"
                aria-label="书签备注"
                rows={2}
                className="min-h-12 resize-none text-sm"
              />
              <div className="flex items-center justify-between gap-2">
                <p className="truncate text-xs text-muted-foreground">
                  {currentChapterId
                    ? `${chapterTitle(currentChapterId)} · ${Math.round(currentPercent)}%`
                    : "当前位置"}
                </p>
                <div className="flex shrink-0 gap-1">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setAdding(false);
                      setLabel("");
                      setNote("");
                    }}
                    disabled={saving}
                  >
                    <X className="size-3.5" />
                    取消
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    onClick={() => void handleAdd()}
                    disabled={saving || !currentChapterId}
                  >
                    {saving ? (
                      <LoaderCircle className="size-3.5 animate-spin" aria-hidden />
                    ) : (
                      <Plus className="size-3.5" />
                    )}
                    保存
                  </Button>
                </div>
              </div>
            </div>
          ) : (
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="mb-2 w-full"
              onClick={() => setAdding(true)}
              disabled={!currentChapterId}
              data-testid="reader-bookmarks-add-open"
            >
              <Plus className="size-3.5" />
              在当前位置添加书签
            </Button>
          )}

          {loading ? (
            <div className="flex items-center justify-center gap-2 py-8 text-xs text-muted-foreground">
              <LoaderCircle className="size-4 animate-spin" aria-hidden />
              加载中
            </div>
          ) : bookmarks.length ? (
            <div className="max-h-[min(24rem,60vh)] space-y-1 overflow-y-auto pr-1">
              {bookmarks.map((bookmark) => (
                <div
                  key={bookmark.id}
                  className="flex items-stretch gap-1 rounded-xl border border-transparent hover:border-border/70 hover:bg-muted/50"
                >
                  <button
                    type="button"
                    className="min-w-0 flex-1 cursor-pointer rounded-xl px-3 py-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    onClick={() => {
                      onNavigate(bookmark);
                      onOpenChange(false);
                    }}
                    aria-label={`跳转到${chapterTitle(bookmark.chapter_id)} ${Math.round(bookmark.position_percent)}%`}
                  >
                    <span className="block truncate text-xs font-semibold text-primary">
                      {chapterTitle(bookmark.chapter_id)}
                      <span className="ml-2 font-normal text-muted-foreground">
                        {Math.round(bookmark.position_percent)}%
                      </span>
                    </span>
                    {bookmark.label ? (
                      <span className="mt-1 block truncate text-sm font-medium text-foreground/90">
                        {bookmark.label}
                      </span>
                    ) : null}
                    {bookmark.note ? (
                      <span className="mt-0.5 block line-clamp-2 text-xs text-foreground/70">
                        {bookmark.note}
                      </span>
                    ) : null}
                  </button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    className="my-1 mr-1 shrink-0 self-start text-muted-foreground hover:text-destructive"
                    disabled={deletingId === bookmark.id}
                    onClick={() => void handleDelete(bookmark.id)}
                    aria-label={`删除书签：${bookmark.label ?? chapterTitle(bookmark.chapter_id)}`}
                    title="删除书签"
                  >
                    {deletingId === bookmark.id ? (
                      <LoaderCircle className="size-3.5 animate-spin" aria-hidden />
                    ) : (
                      <Trash2 className="size-3.5" />
                    )}
                  </Button>
                </div>
              ))}
            </div>
          ) : (
            <p className="rounded-xl border border-dashed border-border/70 px-3 py-6 text-center text-xs text-muted-foreground">
              点击「在当前位置添加书签」保存阅读位置。
            </p>
          )}
        </section>
      ) : null}
    </div>
  );
}
