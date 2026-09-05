"use client";

/**
 * 仿真书架 — 把已入库的书竖立摆上木框层架（书脊朝外）。
 *
 * - 书厚按字数、书高按标题散列，真实书架的错落感
 * - 悬停抽出一点；悬停/聚焦显示操作条（时间线 / 重命名 / 删除）
 * - 点击：书从架上飞到中央、翻开封面后进入阅读页（reduced-motion 直接跳转）
 * - 批量管理模式下点击 = 勾选
 */

import { useEffect, useRef, useState, type CSSProperties, type MouseEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { BarChart3, Check, LoaderCircle, Pencil, Plus, Trash2 } from "lucide-react";

import { BookLoader } from "@/components/book-loader";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { Novel } from "@/lib/api";
import {
  bookHeight,
  bookThickness,
  sealChar,
  toneOf,
} from "./book-visual";
import styles from "./book-shelf.module.css";

type Props = {
  novels: Novel[];
  onDelete?: (id: number) => Promise<void> | void;
  onRename?: (id: number, title: string) => Promise<void> | void;
  selectionMode?: boolean;
  selectedIds?: Set<number>;
  onSelectedChange?: (id: number, selected: boolean) => void;
};

type OpeningState = {
  novel: Novel;
  rect: { left: number; top: number; width: number; height: number };
};

function isReduceMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export function BookShelf({
  novels,
  onDelete,
  onRename,
  selectionMode = false,
  selectedIds,
  onSelectedChange,
}: Props) {
  const router = useRouter();
  const [opening, setOpening] = useState<OpeningState | null>(null);
  const [stage, setStage] = useState<"fly" | "center" | "open">("fly");
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Novel | null>(null);
  const [renameTarget, setRenameTarget] = useState<Novel | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [renameError, setRenameError] = useState("");
  const timersRef = useRef<number[]>([]);

  // 取出动画编排：先飞到中央（0.5s），再翻开封面（0.55s），随后进入阅读页
  useEffect(() => {
    if (!opening) return;
    const raf = requestAnimationFrame(() => setStage("center"));
    timersRef.current = [
      window.setTimeout(() => setStage("open"), 620),
      window.setTimeout(() => {
        router.push(`/novels/${opening.novel.id}`);
      }, 1250),
    ];
    return () => {
      cancelAnimationFrame(raf);
      for (const t of timersRef.current) window.clearTimeout(t);
    };
  }, [opening, router]);

  function handleBookClick(event: MouseEvent<HTMLAnchorElement>, novel: Novel) {
    if (selectionMode) {
      event.preventDefault();
      onSelectedChange?.(novel.id, !selectedIds?.has(novel.id));
      return;
    }
    if (isReduceMotion()) return; // 直接走 Link 跳转
    event.preventDefault();
    const rect = event.currentTarget.getBoundingClientRect();
    setStage("fly");
    setOpening({
      novel,
      rect: {
        left: rect.left,
        top: rect.top,
        width: rect.width,
        height: rect.height,
      },
    });
  }

  function handleDelete(novel: Novel) {
    if (!onDelete) return;
    setDeleteTarget(novel); // 二次确认：先弹对话框，用户再次点击「确认删除」才执行
  }

  async function handleDeleteConfirm() {
    if (!deleteTarget || !onDelete) return;
    setDeletingId(deleteTarget.id);
    try {
      await onDelete(deleteTarget.id);
      setDeleteTarget(null);
    } finally {
      setDeletingId(null);
    }
  }

  function openRename(novel: Novel) {
    setRenameTarget(novel);
    setRenameValue(novel.title);
    setRenameError("");
  }

  async function handleRenameSubmit() {
    if (!renameTarget || !onRename) return;
    const title = renameValue.trim();
    if (!title) {
      setRenameError("书籍名称不能为空");
      return;
    }
    if (title === renameTarget.title) {
      setRenameTarget(null);
      return;
    }
    setRenaming(true);
    setRenameError("");
    try {
      await onRename(renameTarget.id, title);
      setRenameTarget(null);
    } catch {
      setRenameError("保存失败，请稍后重试");
    } finally {
      setRenaming(false);
    }
  }

  const [coverFrom, coverTo] = opening ? toneOf(opening.novel.title) : ["", ""];

  return (
    <div className={styles.shelfFrame} data-testid="book-shelf">
      <div className={styles.shelfInterior}>
        <div className={styles.shelfRow}>
          {novels.map((novel) => {
            const [from, to] = toneOf(novel.title);
            const selected = selectedIds?.has(novel.id) ?? false;
            return (
              <div key={novel.id} className={styles.slot}>
                {/* 悬停操作条 */}
                {!selectionMode && (
                  <div className={styles.slotActions}>
                    <button
                      type="button"
                      className={styles.actionBtn}
                      title="打开时间线分析"
                      aria-label={`《${novel.title}》时间线分析`}
                      onClick={() => router.push(`/analysis?novel=${novel.id}`)}
                    >
                      <BarChart3 className="size-3.5" />
                    </button>
                    {onRename && (
                      <button
                        type="button"
                        className={styles.actionBtn}
                        title="重命名"
                        aria-label={`重命名《${novel.title}》`}
                        onClick={() => openRename(novel)}
                      >
                        <Pencil className="size-3.5" />
                      </button>
                    )}
                    {onDelete && (
                      <button
                        type="button"
                        className={cn(styles.actionBtn, styles.actionBtnDanger)}
                        title="删除本书"
                        aria-label={`删除《${novel.title}》`}
                        disabled={deletingId === novel.id}
                        onClick={() => void handleDelete(novel)}
                      >
                        {deletingId === novel.id ? (
                          <LoaderCircle className="size-3.5 animate-spin" />
                        ) : (
                          <Trash2 className="size-3.5" />
                        )}
                      </button>
                    )}
                  </div>
                )}

                {/* 勾选标记（批量管理模式） */}
                {selectionMode && (
                  <button
                    type="button"
                    role="checkbox"
                    aria-checked={selected}
                    aria-label={`选择《${novel.title}》`}
                    className={cn(
                      styles.selectBadge,
                      selected && styles.selectBadgeChecked
                    )}
                    onClick={() => onSelectedChange?.(novel.id, !selected)}
                  >
                    <Check className="size-3.5" />
                  </button>
                )}

                {/* 书脊（e2e 依赖 a[href*="/novels/"] + 标题文本） */}
                <Link
                  href={`/novels/${novel.id}`}
                  onClick={(e) => handleBookClick(e, novel)}
                  className={styles.book}
                  title={`${novel.title} · ${novel.author || "未知作者"} · ${novel.chapter_count} 章`}
                  style={
                    {
                      width: bookThickness(novel.word_count),
                      height: bookHeight(novel.title),
                      background: `linear-gradient(165deg, ${from}, ${to})`,
                    } as CSSProperties
                  }
                >
                  <span className={styles.bookTitle}>{novel.title}</span>
                  <span className={styles.bookAuthor}>{novel.author || "未知作者"}</span>
                  <span aria-hidden className={styles.bookSeal}>
                    {sealChar(novel.status)}
                  </span>
                </Link>

                <div aria-hidden className={styles.plank} />
              </div>
            );
          })}

          {/* 虚位「幽灵书」：点击导入新书（复用页面上传对话框触发器） */}
          {!selectionMode && (
            <div className={styles.slot}>
              <button
                type="button"
                className={styles.ghostBook}
                style={{ height: 176 }}
                title="导入新书"
                aria-label="导入新书"
                onClick={() => {
                  document
                    .querySelector<HTMLButtonElement>("[data-upload-trigger]")
                    ?.click();
                }}
              >
                <Plus className="size-4" />
                <span className={styles.ghostLabel}>导入新书</span>
              </button>
              <div aria-hidden className={styles.plank} />
            </div>
          )}
        </div>
      </div>

      {/* 点击取出动画：书飞到中央 → 翻开封面 → 进入阅读页（无遮罩） */}
      {opening && (
        <div className={styles.overlay} aria-hidden>
          <div
            className={styles.flyBook}
            style={
              stage === "fly"
                ? {
                    left: opening.rect.left,
                    top: opening.rect.top,
                    width: opening.rect.width,
                    height: opening.rect.height,
                  }
                : {
                    left: "50%",
                    top: "50%",
                    width: "min(240px, 68vw)",
                    height: "min(320px, 58vh)",
                    transform: "translate(-50%, -50%)",
                  }
            }
          >
            <div className={styles.flyPages}>
              <BookLoader />
              <p className="line-clamp-2 font-serif text-sm font-semibold text-[#4a3a22]">
                {opening.novel.title}
              </p>
            </div>
            <div
              className={cn(styles.flyCover, stage === "open" && styles.flyCoverOpen)}
              style={{ background: `linear-gradient(165deg, ${coverFrom}, ${coverTo})` }}
            >
              <span className="font-serif text-[11px] tracking-[0.3em] text-white/60">
                NOVELMIND
              </span>
              <p className="line-clamp-4 font-serif text-lg font-semibold leading-snug text-[#f3e6c2]">
                {opening.novel.title}
              </p>
              <span className="self-end font-serif text-xs text-white/50">
                {opening.novel.author || "未知作者"}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* 删除二次确认对话框 */}
      <Dialog
        open={deleteTarget !== null}
        onOpenChange={(open) => {
          if (!open && deletingId === null) setDeleteTarget(null);
        }}
      >
        <DialogContent className="rounded-3xl sm:max-w-md">
          <DialogHeader>
            <DialogTitle>删除《{deleteTarget?.title}》</DialogTitle>
            <DialogDescription>
              将同时删除全部章节、分析内容与叙事记忆，此操作不可恢复。
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={deletingId !== null}
              onClick={() => setDeleteTarget(null)}
            >
              取消
            </Button>
            <Button
              type="button"
              variant="destructive"
              disabled={deletingId !== null}
              onClick={() => void handleDeleteConfirm()}
            >
              {deletingId !== null ? "删除中..." : "确认删除"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* 重命名对话框 */}
      <Dialog
        open={renameTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRenameTarget(null);
        }}
      >
        <DialogContent className="rounded-3xl sm:max-w-md">
          <DialogHeader>
            <DialogTitle>更改书籍名称</DialogTitle>
            <DialogDescription>
              只修改书架显示名称，不会改变章节内容。
            </DialogDescription>
          </DialogHeader>
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              void handleRenameSubmit();
            }}
          >
            <label className="block space-y-2 text-sm font-medium">
              书籍名称
              <Input
                value={renameValue}
                onChange={(event) => setRenameValue(event.target.value)}
                maxLength={200}
                autoFocus
              />
            </label>
            {renameError && (
              <p className="text-sm text-destructive" role="alert">
                {renameError}
              </p>
            )}
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => setRenameTarget(null)}
              >
                取消
              </Button>
              <Button type="submit" disabled={renaming}>
                {renaming ? "保存中..." : "保存名称"}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </div>
  );
}
