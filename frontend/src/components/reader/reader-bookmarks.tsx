"use client";

import { useEffect, useRef, useState } from "react";
import { Bookmark as BookmarkIcon, LoaderCircle, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { Bookmark, Chapter } from "@/lib/api";
import { novelsApi } from "@/lib/api";
import { useDismissableLayer } from "@/lib/use-dismissable-layer";
import { cn } from "@/lib/utils";

interface ReaderBookmarksProps {
  novelId: string;
  chapters: Chapter[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onNavigate: (bookmark: Bookmark) => void;
}

export function ReaderBookmarks({
  novelId,
  chapters,
  open,
  onOpenChange,
  onNavigate,
}: ReaderBookmarksProps) {
  const layerRef = useRef<HTMLElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [bookmarks, setBookmarks] = useState<Bookmark[]>([]);
  const [loading, setLoading] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
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
          setError("书签加载失败，请确认数据库迁移已完成");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [novelId, open]);

  const handleDelete = async (bookmarkId: number) => {
    setDeletingId(bookmarkId);
    setError(null);
    try {
      await novelsApi.deleteBookmark(novelId, bookmarkId);
      setBookmarks((current) => current.filter((item) => item.id !== bookmarkId));
    } catch {
      setError("书签删除失败，请重试");
    } finally {
      setDeletingId(null);
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
        aria-label="打开书签"
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
                保存的正文选区
              </p>
            </div>
            <span className="text-xs tabular-nums text-muted-foreground">
              {bookmarks.length}
            </span>
          </div>

          {error ? (
            <p className="rounded-xl bg-destructive/10 px-3 py-2 text-xs text-destructive">
              {error}
            </p>
          ) : null}

          {loading ? (
            <div className="flex items-center justify-center gap-2 py-8 text-xs text-muted-foreground">
              <LoaderCircle className="size-4 animate-spin" aria-hidden />
              加载中
            </div>
          ) : bookmarks.length ? (
            <div className="max-h-[min(24rem,60vh)] space-y-1 overflow-y-auto pr-1">
              {bookmarks.map((bookmark) => {
                const chapter = chapters.find((item) => item.id === bookmark.chapter_id);
                const chapterLabel = chapter?.title ?? `第 ${bookmark.chapter_id} 章`;
                return (
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
                      aria-label={`跳转到${chapterLabel}：${bookmark.selected_text}`}
                    >
                      <span className="block truncate text-xs font-semibold text-primary">
                        {chapterLabel}
                      </span>
                      <span className="mt-1 block line-clamp-2 text-sm text-foreground/85">
                        {bookmark.selected_text}
                      </span>
                    </button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-xs"
                      className="my-1 mr-1 shrink-0 self-start text-muted-foreground hover:text-destructive"
                      disabled={deletingId === bookmark.id}
                      onClick={() => void handleDelete(bookmark.id)}
                      aria-label={`删除书签：${bookmark.selected_text}`}
                      title="删除书签"
                    >
                      {deletingId === bookmark.id ? (
                        <LoaderCircle className="size-3.5 animate-spin" aria-hidden />
                      ) : (
                        <Trash2 className="size-3.5" />
                      )}
                    </Button>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="rounded-xl border border-dashed border-border/70 px-3 py-6 text-center text-xs text-muted-foreground">
              选中文字后，点击选区浮层的书签图标保存。
            </p>
          )}
        </section>
      ) : null}
    </div>
  );
}
