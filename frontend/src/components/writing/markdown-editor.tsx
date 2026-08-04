"use client";

/**
 * Phase 36-02 Markdown editor (REQ-FORK-02 / REQ-CRE-03, D-36-02/D-36-03).
 *
 * Renders one derivative project's ordered chapter plan and a Markdown draft
 * editor with an explicit, always-visible fork/version/cutoff scope header:
 *
 * - the namespace is always ``fanfiction_canon`` (D-36-03) — this surface never
 *   offers an Original Canon or User Interpretation write entry point;
 * - every save carries the chapter's ``base_revision`` optimistic-concurrency
 *   token; a stale write is surfaced as a **conflict** with a reload option
 *   instead of silently overwriting newer content;
 * - editor state is explicit: dirty / saving / saved / error / conflict /
 *   blocked (archived project or archived chapter);
 * - the fork is never inferred from a reading page — the parent page passes the
 *   project bound to the explicitly chosen fork (D-36-01).
 */

import { useCallback, useEffect, useRef, useState } from "react";

import axios from "axios";
import { ArrowDown, ArrowUp, Check, CircleAlert, Loader2, Plus, RefreshCw } from "lucide-react";

import { derivativeApi } from "@/lib/derivative-api";
import type {
  DerivativeChapterView,
  DerivativeProjectView,
} from "@/lib/derivative-api";

export type EditorSaveState =
  | "idle"
  | "dirty"
  | "saving"
  | "saved"
  | "error"
  | "conflict";

interface MarkdownEditorProps {
  novelId: number;
  project: DerivativeProjectView;
  chapters: DerivativeChapterView[];
  /** Stable parent callback that replaces the loaded chapter list. */
  onChaptersChange: (chapters: DerivativeChapterView[]) => void;
}

/** Extract `revision <n>` / `checksum <hex>` from the server 409 detail. */
function parseConflictDetail(detail: string): { revision: number | null; checksum: string | null } {
  const revisionMatch = detail.match(/revision (\d+)/);
  const checksumMatch = detail.match(/checksum ([a-f0-9]{64})/);
  return {
    revision: revisionMatch ? Number(revisionMatch[1]) : null,
    checksum: checksumMatch ? checksumMatch[1] : null,
  };
}

const SAVE_DEBOUNCE_MS = 900;

export function MarkdownEditor({
  novelId,
  project,
  chapters,
  onChaptersChange,
}: MarkdownEditorProps) {
  const [selectedId, setSelectedId] = useState<number | null>(chapters[0]?.id ?? null);
  const [titleDraft, setTitleDraft] = useState(chapters[0]?.title ?? "");
  const [markdownDraft, setMarkdownDraft] = useState(chapters[0]?.markdown ?? "");
  const [saveState, setSaveState] = useState<EditorSaveState>("idle");
  const [errorText, setErrorText] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  // Refs keep the debounced autosave on the latest draft/chapter without
  // recreating the effect every keystroke. Values are written after render (in
  // an effect), so the refs are always fresh when a timer/button reads them.
  const titleRef = useRef(titleDraft);
  const markdownRef = useRef(markdownDraft);
  const saveStateRef = useRef<EditorSaveState>(saveState);
  const chaptersRef = useRef<DerivativeChapterView[]>(chapters);
  const selectedRef = useRef<DerivativeChapterView | null>(
    chapters.find((c) => c.id === selectedId) ?? null
  );

  const selected = chapters.find((c) => c.id === selectedId) ?? null;

  useEffect(() => {
    titleRef.current = titleDraft;
    markdownRef.current = markdownDraft;
    saveStateRef.current = saveState;
    chaptersRef.current = chapters;
    selectedRef.current = selected;
  });

  // Keep a default selection as the plan loads / changes.
  useEffect(() => {
    if (selectedId == null && chapters.length > 0) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- pick the first chapter when the plan arrives
      setSelectedId(chapters[0].id);
    } else if (selectedId != null && !chapters.some((c) => c.id === selectedId)) {
      setSelectedId(chapters[0]?.id ?? null);
    }
  }, [chapters, selectedId]);

  // Load drafts into the editor whenever the selection changes.
  useEffect(() => {
    if (!selected) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reset the draft editor state when the selected chapter changes
    setTitleDraft(selected.title);
    setMarkdownDraft(selected.markdown);
    setSaveState("idle");
    setErrorText(null);
    // Keyed on the chapter id only: a successful save mutates `chapters` but
    // must not clobber the editor's in-flight draft.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  const blocked = project.status === "archived";
  const selectedArchived = selected?.status === "archived";
  const readOnly = blocked || selectedArchived;

  const persist = useCallback(async () => {
    const chapter = selectedRef.current;
    if (!chapter || saveStateRef.current === "saving") return;
    setSaveState("saving");
    setErrorText(null);
    try {
      const res = await derivativeApi.patchChapter(novelId, project.id, chapter.id, {
        title: titleRef.current.trim() || chapter.title,
        markdown: markdownRef.current,
        base_revision: chapter.revision,
      });
      const next = res.data;
      const updated = (chaptersRef.current ?? []).map((c) =>
        c.id === next.id ? next : c
      );
      chaptersRef.current = updated;
      onChaptersChange(updated);
      setSaveState("saved");
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 409) {
        setSaveState("conflict");
        const { revision, checksum } = parseConflictDetail(
          String(err.response.data?.detail ?? "")
        );
        setErrorText(
          `检测到更新版本（revision ${revision ?? "?"} / checksum ${checksum?.slice(0, 12) ?? "?"}…）。` +
            "已阻止覆盖，重新加载后可继续编辑。"
        );
      } else {
        setSaveState("error");
        setErrorText("保存失败，请稍后重试。");
      }
    }
  }, [novelId, project.id, onChaptersChange]);

  // Debounced autosave: only fires for genuine dirty edits.
  useEffect(() => {
    if (saveState !== "dirty" || blocked) return;
    const timer = window.setTimeout(() => {
      void persist();
    }, SAVE_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [titleDraft, markdownDraft, saveState, blocked, persist]);

  const selectChapter = (id: number) => {
    if (saveState === "saving") return;
    setSelectedId(id);
  };

  const reloadChapter = useCallback(async () => {
    const chapter = selectedRef.current;
    if (!chapter) return;
    try {
      const res = await derivativeApi.getChapter(novelId, project.id, chapter.id);
      const next = res.data;
      const updated = (chaptersRef.current ?? []).map((c) => (c.id === next.id ? next : c));
      chaptersRef.current = updated;
      onChaptersChange(updated);
      setTitleDraft(next.title);
      setMarkdownDraft(next.markdown);
      setSaveState("idle");
      setErrorText(null);
    } catch {
      setSaveState("error");
      setErrorText("重新加载失败，请稍后重试。");
    }
  }, [novelId, project.id, onChaptersChange]);

  const createChapter = async () => {
    if (blocked) return;
    setCreating(true);
    try {
      const res = await derivativeApi.createChapter(novelId, project.id, {
        title: `第 ${chapters.length + 1} 章`,
        markdown: "",
      });
      const next = res.data.chapter;
      const updated = [...(chaptersRef.current ?? []), next];
      chaptersRef.current = updated;
      onChaptersChange(updated);
      setSelectedId(next.id);
    } catch {
      setSaveState("error");
      setErrorText("新建章节失败，请稍后重试。");
    } finally {
      setCreating(false);
    }
  };

  const moveChapter = async (id: number, direction: -1 | 1) => {
    if (blocked) return;
    const index = chapters.findIndex((c) => c.id === id);
    const swapIndex = index + direction;
    if (index < 0 || swapIndex < 0 || swapIndex >= chapters.length) return;
    const reordered = [...chapters];
    [reordered[index], reordered[swapIndex]] = [reordered[swapIndex], reordered[index]];
    try {
      const res = await derivativeApi.reorderChapters(
        novelId,
        project.id,
        reordered.map((c) => c.id)
      );
      chaptersRef.current = res.data.items;
      onChaptersChange(res.data.items);
    } catch {
      setSaveState("error");
      setErrorText("调整章节顺序失败，请稍后重试。");
    }
  };

  const deleteChapter = async (id: number) => {
    if (blocked) return;
    try {
      await derivativeApi.deleteChapter(novelId, project.id, id);
      const remaining = (chaptersRef.current ?? []).filter((c) => c.id !== id);
      chaptersRef.current = remaining;
      onChaptersChange(remaining);
      setSelectedId(remaining[0]?.id ?? null);
    } catch {
      setSaveState("error");
      setErrorText("删除章节失败，请稍后重试。");
    }
  };

  const scope = (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-xs sm:grid-cols-4">
      <div>
        <dt className="text-muted-foreground">Namespace</dt>
        <dd className="font-semibold text-primary">{project.space}</dd>
      </div>
      <div>
        <dt className="text-muted-foreground">Fork</dt>
        <dd className="font-mono font-semibold">{project.fork_key}</dd>
      </div>
      <div>
        <dt className="text-muted-foreground">Version</dt>
        <dd className="font-mono font-semibold">{project.source_version_key}</dd>
      </div>
      <div>
        <dt className="text-muted-foreground">Cutoff</dt>
        <dd className="font-mono font-semibold">
          第 {project.through_chapter} 章 · {project.cutoff_snapshot_hash.slice(0, 8)}…
        </dd>
      </div>
    </dl>
  );

  return (
    <section
      className="paper-surface rounded-3xl p-5 sm:p-7"
      aria-label={`Markdown 编辑器 · ${project.name}`}
    >
      <div className="flex flex-col gap-4 border-b border-border/60 pb-5 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">
            Derivative studio · Fanfiction Canon only
          </p>
          <h2 className="mt-2 font-serif text-2xl font-semibold">{project.name}</h2>
          <div className="mt-3">{scope}</div>
        </div>
        <div className="flex flex-col items-start gap-2 text-xs text-muted-foreground lg:items-end">
          {blocked ? (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-500/40 bg-amber-500/10 px-3 py-1 font-semibold text-amber-600">
              <CircleAlert className="size-3.5" /> 已归档 · 只读
            </span>
          ) : (
            <span className="rounded-full border border-border bg-secondary px-3 py-1 font-semibold">
              草稿 · 未发布
            </span>
          )}
          <span data-testid="editor-save-state" className="font-mono">
            {saveState === "idle" && "idle"}
            {saveState === "dirty" && "dirty · 有未保存修改"}
            {saveState === "saving" && (
              <span className="inline-flex items-center gap-1">
                <Loader2 className="size-3 animate-spin" /> saving
              </span>
            )}
            {saveState === "saved" && (
              <span className="inline-flex items-center gap-1 text-emerald-600">
                <Check className="size-3" /> saved
              </span>
            )}
            {saveState === "error" && <span className="text-red-600">error</span>}
            {saveState === "conflict" && <span className="text-amber-600">conflict</span>}
          </span>
        </div>
      </div>

      {errorText && (
        <div
          role="alert"
          className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-border bg-secondary/70 px-4 py-3 text-sm"
        >
          <span className="flex items-center gap-2">
            <CircleAlert className="size-4 shrink-0 text-amber-600" />
            {errorText}
          </span>
          {saveState === "conflict" && (
            <button
              type="button"
              className="inline-flex items-center gap-1.5 rounded-lg border border-primary px-3 py-1.5 text-xs font-semibold text-primary"
              onClick={() => void reloadChapter()}
            >
              <RefreshCw className="size-3" /> 重新加载
            </button>
          )}
        </div>
      )}

      <div className="mt-6 grid gap-5 lg:grid-cols-[240px_minmax(0,1fr)]">
        <aside className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="font-serif text-lg font-semibold">章节规划</h3>
            <button
              type="button"
              className="inline-flex items-center gap-1 rounded-lg border border-border px-2.5 py-1.5 text-xs font-semibold"
              onClick={() => void createChapter()}
              disabled={blocked || creating}
            >
              <Plus className="size-3" /> 新增章节
            </button>
          </div>
          <div className="space-y-2" aria-label="章节计划列表">
            {chapters.length === 0 ? (
              <p className="rounded-xl bg-secondary/60 p-4 text-sm text-muted-foreground">
                还没有章节，先新增一个计划条目。
              </p>
            ) : (
              chapters.map((chapter, index) => (
                <div
                  key={chapter.id}
                  className={`flex items-center gap-1 rounded-xl border px-3 py-2 text-left text-sm transition-colors ${
                    selectedId === chapter.id
                      ? "border-primary bg-primary/10"
                      : "border-border hover:bg-secondary/60"
                  }`}
                >
                  <button
                    type="button"
                    className="min-w-0 flex-1 text-left"
                    onClick={() => selectChapter(chapter.id)}
                    aria-pressed={selectedId === chapter.id}
                  >
                    <span className="block truncate">
                      <span className="mr-1 font-mono text-xs text-muted-foreground">
                        {chapter.position + 1}.
                      </span>
                      {chapter.title || "未命名"}
                    </span>
                    <span className="block text-[11px] text-muted-foreground">
                      v{chapter.revision} · {chapter.status}
                    </span>
                  </button>
                  <span className="flex shrink-0 flex-col gap-0.5">
                    <button
                      type="button"
                      aria-label={`上移 ${chapter.title}`}
                      className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                      disabled={blocked || index === 0}
                      onClick={() => void moveChapter(chapter.id, -1)}
                    >
                      <ArrowUp className="size-3.5" />
                    </button>
                    <button
                      type="button"
                      aria-label={`下移 ${chapter.title}`}
                      className="text-muted-foreground hover:text-foreground disabled:opacity-30"
                      disabled={blocked || index === chapters.length - 1}
                      onClick={() => void moveChapter(chapter.id, 1)}
                    >
                      <ArrowDown className="size-3.5" />
                    </button>
                  </span>
                  <button
                    type="button"
                    aria-label={`删除 ${chapter.title}`}
                    className="ml-1 shrink-0 text-xs text-muted-foreground hover:text-red-600 disabled:opacity-30"
                    disabled={blocked}
                    onClick={() => void deleteChapter(chapter.id)}
                  >
                    ✕
                  </button>
                </div>
              ))
            )}
          </div>
        </aside>

        <div className="min-w-0 space-y-3">
          {selected ? (
            <>
              <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
                <input
                  className="rounded-xl border border-border bg-background px-3 py-2 font-serif text-lg disabled:opacity-60"
                  value={titleDraft}
                  onChange={(event) => {
                    setTitleDraft(event.target.value);
                    setSaveState("dirty");
                  }}
                  aria-label="章节标题"
                  disabled={readOnly}
                />
                <div className="flex gap-2">
                  <button
                    type="button"
                    className="inline-flex items-center gap-1.5 rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50"
                    onClick={() => void persist()}
                    disabled={blocked || saveState === "saving" || (saveState === "idle" && !blocked)}
                  >
                    {saveState === "saving" ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Check className="size-4" />
                    )}
                    保存
                  </button>
                </div>
              </div>
              <textarea
                className="min-h-[420px] w-full rounded-2xl border border-border bg-background p-4 font-mono text-sm leading-6 disabled:opacity-60"
                value={markdownDraft}
                onChange={(event) => {
                  setMarkdownDraft(event.target.value);
                  setSaveState("dirty");
                }}
                aria-label="章节 Markdown 内容"
                placeholder="用 Markdown 写下这一章…"
                disabled={readOnly}
              />
              <p className="text-xs text-muted-foreground">
                编辑内容只形成 Fanfiction Canon 草稿；保存前不会发布，也不会进入原作检索、评测或 Narrative Memory。
              </p>
            </>
          ) : (
            <div className="rounded-2xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
              选择或新增一个章节开始写作。
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
