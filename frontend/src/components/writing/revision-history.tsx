"use client";

/**
 * Phase 36-04 revision history / recovery / rollback panel (REQ-FORK-02 /
 * REQ-CRE-04, D-36-02/D-36-03, D-36-04 gate).
 *
 * The panel is the browser-side half of the append-only revision surface
 * (backend `app/api/derivative_revisions.py`):
 *
 * - a lineage strip re-echoes the sealed namespace / fork / version scope so the
 *   user always sees that edits are Fanfiction Canon only (D-36-03) and bound to
 *   the explicitly chosen fork (D-36-01);
 * - the autosave / conflict state is surfaced next to the current revision so
 *   "saved", "conflict" and "dirty" are never ambiguous;
 * - the newest-first immutable history is fetched only when the panel is opened
 *   (no hidden calls on page load);
 * - a deterministic diff between two selected revisions is fetched live and
 *   rendered with explicit add/delete/context lines;
 * - **恢复草稿** loads a historical snapshot back into the editor; it requires
 *   explicit confirmation when it would discard unsaved changes;
 * - **回滚到此版本** always requires an explicit two-step approval, sends the
 *   current `base_revision` CAS token, and only reports success from the actual
 *   server response — never a fabricated one (T-36-04-02). A stale 409 conflict
 *   is surfaced with the head revision instead of silently succeeding.
 */

import { useCallback, useEffect, useState } from "react";

import axios from "axios";
import {
  Check,
  ChevronDown,
  ChevronUp,
  CircleAlert,
  GitBranch,
  History,
  Loader2,
  RotateCcw,
  Undo2,
} from "lucide-react";

import { derivativeApi } from "@/lib/derivative-api";
import type {
  DerivativeConflictDetail,
  DerivativeDiffResponse,
  DerivativeProjectView,
  DerivativeRevisionSummary,
  DerivativeRevisionView,
  DerivativeChapterView,
} from "@/lib/derivative-api";
import type { EditorSaveState } from "./markdown-editor";

interface RevisionHistoryProps {
  novelId: number;
  project: DerivativeProjectView;
  /** The currently selected chapter in the editor; null when none selected. */
  chapter: DerivativeChapterView | null;
  /** Current editor autosave state so the panel mirrors it in one place. */
  saveState: EditorSaveState;
  /** Load a historical revision's canonical Markdown back into the editor. */
  onRecoverDraft: (markdown: string) => void;
  /** Apply the server-returned chapter after a rollback (head + CAS token). */
  onRollbackApplied: (chapter: DerivativeChapterView, message: string | null) => void;
  /** Archived projects/chapters are read-only for recover/rollback. */
  readOnly?: boolean;
}

const KIND_LABELS: Record<string, string> = {
  create: "创建",
  autosave: "自动保存",
  rollback: "回滚",
};

function checksumPrefix(checksum: string): string {
  return `${checksum.slice(0, 10)}…`;
}

export function RevisionHistory({
  novelId,
  project,
  chapter,
  saveState,
  onRecoverDraft,
  onRollbackApplied,
  readOnly = false,
}: RevisionHistoryProps) {
  const [open, setOpen] = useState(false);
  const [revisions, setRevisions] = useState<DerivativeRevisionSummary[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);

  // Diff selectors (both ids in revision space).
  const [baseRevisionId, setBaseRevisionId] = useState<number | null>(null);
  const [targetRevisionId, setTargetRevisionId] = useState<number | null>(null);
  const [diff, setDiff] = useState<DerivativeDiffResponse | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [diffError, setDiffError] = useState<string | null>(null);

  // Draft recovery (explicit confirmation when it would discard edits).
  const [pendingRecover, setPendingRecover] = useState<DerivativeRevisionSummary | null>(
    null
  );
  const [recovering, setRecovering] = useState(false);
  const [recoverError, setRecoverError] = useState<string | null>(null);

  // Rollback (two-step approval, never a fabricated success).
  const [pendingRollback, setPendingRollback] = useState<DerivativeRevisionSummary | null>(
    null
  );
  const [rollbackReason, setRollbackReason] = useState("");
  const [rollingBack, setRollingBack] = useState(false);
  const [rollbackError, setRollbackError] = useState<string | null>(null);
  const [rollbackResult, setRollbackResult] = useState<{
    message: string | null;
    revision: DerivativeRevisionView;
  } | null>(null);

  const chapterId = chapter?.id ?? null;

  // Reset all panel state when the selected chapter changes.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- switching chapters must clear the previous chapter's panel state before the next render
    setRevisions(null);
    setDiff(null);
    setDiffError(null);
    setBaseRevisionId(null);
    setTargetRevisionId(null);
    setPendingRecover(null);
    setPendingRollback(null);
    setRollbackResult(null);
    setRollbackError(null);
    setRecoverError(null);
    setRollbackReason("");
  }, [chapterId]);

  // Load the newest-first immutable history when the panel opens (no hidden
  // calls while the editor loads).
  useEffect(() => {
    if (!open || !chapter) return;
    if (revisions !== null) return;
    let cancelled = false;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the open panel must flip to the loading state exactly when history is fetched
    setLoading(true);
    setHistoryError(null);
    derivativeApi
      .listRevisions(novelId, project.id, chapter.id)
      .then((res) => {
        if (cancelled) return;
        const items = res.data.items;
        setRevisions(items);
        // Default diff selection: earliest -> current head (full change set).
        if (items.length >= 2) {
          setBaseRevisionId(items[items.length - 1].id);
          setTargetRevisionId(items[0].id);
        }
      })
      .catch(() => {
        if (!cancelled) setHistoryError("历史加载失败，请稍后重试。");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, chapter, novelId, project.id, revisions]);

  // Live deterministic diff whenever both selectors are chosen.
  useEffect(() => {
    if (!open || !chapter) return;
    if (baseRevisionId == null || targetRevisionId == null) return;
    if (baseRevisionId === targetRevisionId) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- an identical pair yields no meaningful diff; clear it in the same pass
      setDiff(null);
      setDiffError(null);
      return;
    }
    let cancelled = false;
    setDiffLoading(true);
    setDiffError(null);
    derivativeApi
      .diffRevisions(novelId, project.id, chapter.id, baseRevisionId, targetRevisionId)
      .then((res) => {
        if (!cancelled) setDiff(res.data);
      })
      .catch(() => {
        if (!cancelled) setDiffError("差异加载失败，请稍后重试。");
      })
      .finally(() => {
        if (!cancelled) setDiffLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, chapter, novelId, project.id, baseRevisionId, targetRevisionId]);

  const toggleOpen = useCallback(() => {
    const next = !open;
    setOpen(next);
    if (next) {
      setRevisions(null);
      setDiff(null);
    }
  }, [open]);

  const confirmRecover = useCallback(
    async (revision: DerivativeRevisionSummary) => {
      if (!chapter) return;
      setRecovering(true);
      setRecoverError(null);
      try {
        let content: string;
        if (revision.revision_number === chapter.revision) {
          content = chapter.markdown;
        } else {
          const detail = await derivativeApi.getRevision(
            novelId,
            project.id,
            chapter.id,
            revision.id
          );
          content = detail.data.content;
        }
        onRecoverDraft(content);
        setPendingRecover(null);
      } catch {
        setRecoverError("草稿载入失败，请稍后重试。");
      } finally {
        setRecovering(false);
      }
    },
    [chapter, novelId, project.id, onRecoverDraft]
  );

  const requestRecover = useCallback(
    (revision: DerivativeRevisionSummary) => {
      if (!chapter || readOnly) return;
      // Head recovery is a plain reload of the server snapshot; any historical
      // draft load that could discard unsaved edits needs explicit approval.
      if (revision.revision_number === chapter.revision && saveState !== "dirty") {
        void confirmRecover(revision);
      } else {
        setPendingRecover(revision);
      }
    },
    // confirmRecover is stable (same chapter/onRecoverDraft), so it is safe to
    // keep it out of the dependency list.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [chapter, readOnly, saveState]
  );

  const requestRollback = useCallback((revision: DerivativeRevisionSummary) => {
    if (readOnly) return;
    setRollbackError(null);
    setRollbackResult(null);
    setRollbackReason("");
    setPendingRollback(revision);
  }, [readOnly]);

  const cancelRollback = useCallback(() => {
    if (rollingBack) return;
    setPendingRollback(null);
    setRollbackError(null);
  }, [rollingBack]);

  const confirmRollback = useCallback(async () => {
    if (!chapter || !pendingRollback) return;
    setRollingBack(true);
    setRollbackError(null);
    try {
      const res = await derivativeApi.rollbackChapter(
        novelId,
        project.id,
        chapter.id,
        {
          target_revision_id: pendingRollback.id,
          reason: rollbackReason.trim() || null,
          base_revision: chapter.revision,
        }
      );
      // Success is reported only from the actual server response (T-36-04-02).
      setRollbackResult({
        message: res.data.message,
        revision: res.data.revision,
      });
      onRollbackApplied(res.data.chapter, res.data.message);
      setPendingRollback(null);
      // Refetch history so the new rollback child row appears as the head.
      setRevisions(null);
      setDiff(null);
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 409) {
        const detail = err.response.data?.detail as DerivativeConflictDetail | undefined;
        setPendingRollback(null);
        setRollbackError(
          `检测到更新版本（revision ${detail?.current_revision_number ?? "?"}）。` +
            "回滚目标已过期，未做任何修改；请重新载入章节后再试。"
        );
      } else {
        setRollbackError("回滚失败，请稍后重试。");
      }
    } finally {
      setRollingBack(false);
    }
  }, [chapter, pendingRollback, rollbackReason, novelId, project.id, onRollbackApplied]);

  const oldestFirst = revisions ? [...revisions].reverse() : [];
  const isReadOnly = readOnly;

  return (
    <section
      className="rounded-2xl border border-border/80 bg-background/40"
      aria-label="修订历史与恢复"
      data-testid="revision-history"
    >
      <button
        type="button"
        onClick={toggleOpen}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left"
        aria-expanded={open}
        data-testid="revision-history-toggle"
      >
        <span className="inline-flex items-center gap-2 text-sm font-semibold">
          <History className="size-4 text-primary" />
          修订历史与恢复
        </span>
        <span className="inline-flex items-center gap-2 text-xs text-muted-foreground">
          {chapter ? (
            <span data-testid="revision-history-summary">
              当前 v{chapter.revision} · {saveState}
            </span>
          ) : (
            "未选择章节"
          )}
          {open ? (
            <ChevronUp className="size-4" />
          ) : (
            <ChevronDown className="size-4" />
          )}
        </span>
      </button>

      {open && (
        <div className="border-t border-border/70 px-4 py-4">
          {/* Namespace / fork / version lineage (D-36-01/D-36-03) */}
          <dl
            className="grid grid-cols-2 gap-x-6 gap-y-1.5 text-xs sm:grid-cols-4"
            data-testid="revision-history-lineage"
          >
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
              <dt className="text-muted-foreground">Autosave</dt>
              <dd
                className={
                  saveState === "conflict" || saveState === "error"
                    ? "font-semibold text-amber-600"
                    : saveState === "saved"
                      ? "font-semibold text-emerald-600"
                      : "font-semibold"
                }
                data-testid="revision-history-savestate"
              >
                {saveState}
              </dd>
            </div>
          </dl>

          {!chapter ? (
            <p className="mt-4 rounded-xl bg-secondary/50 p-4 text-sm text-muted-foreground">
              选择或新增一个章节后查看修订历史。
            </p>
          ) : (
            <>
              <div className="mt-4 space-y-4">
                {loading && (
                  <p className="inline-flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="size-4 animate-spin" /> 加载历史中…
                  </p>
                )}
                {historyError && (
                  <div
                    role="alert"
                    className="flex items-center gap-2 rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-600"
                  >
                    <CircleAlert className="size-4 shrink-0" /> {historyError}
                  </div>
                )}

                {revisions !== null && revisions.length === 0 && !loading && (
                  <p className="rounded-xl bg-secondary/50 p-4 text-sm text-muted-foreground">
                    还没有修订记录。
                  </p>
                )}

                {revisions !== null && revisions.length > 0 && (
                  <div
                    className="space-y-2"
                    aria-label="修订历史列表"
                    data-testid="revision-history-list"
                  >
                    {revisions.map((revision) => {
                      const isHead = revision.revision_number === chapter.revision;
                      const canRollback = !isHead && !isReadOnly;
                      const canRecover = !isReadOnly;
                      return (
                        <div
                          key={revision.id}
                          className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border border-border/70 bg-secondary/40 px-3 py-2 text-sm"
                          data-revision-id={revision.id}
                          data-testid="revision-row"
                        >
                          <span className="font-mono text-xs font-semibold text-primary">
                            v{revision.revision_number}
                          </span>
                          <span className="rounded-full border border-border bg-background px-2 py-0.5 text-[11px]">
                            {KIND_LABELS[revision.kind] ?? revision.kind}
                          </span>
                          {revision.kind === "rollback" && (
                            <span className="text-[11px] text-muted-foreground">
                              {revision.approval_state}
                            </span>
                          )}
                          <code className="font-mono text-[11px] text-muted-foreground">
                            {checksumPrefix(revision.content_checksum)}
                          </code>
                          {revision.reason && (
                            <span className="truncate text-xs text-muted-foreground">
                              “{revision.reason}”
                            </span>
                          )}
                          <span className="ml-auto inline-flex items-center gap-2">
                            <button
                              type="button"
                              className="inline-flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-xs font-semibold disabled:opacity-40"
                              onClick={() => requestRecover(revision)}
                              disabled={!canRecover || recovering}
                              data-testid="revision-recover"
                            >
                              <RotateCcw className="size-3" /> 载入草稿
                            </button>
                            {canRollback && (
                              <button
                                type="button"
                                className="inline-flex items-center gap-1 rounded-lg border border-amber-500/50 px-2 py-1 text-xs font-semibold text-amber-600 disabled:opacity-40"
                                onClick={() => requestRollback(revision)}
                                disabled={rollingBack}
                                data-testid="revision-rollback"
                              >
                                <Undo2 className="size-3" /> 回滚到此版本
                              </button>
                            )}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Draft recovery confirmation */}
              {pendingRecover && (
                <div
                  className="mt-4 rounded-xl border border-border bg-secondary/60 p-4 text-sm"
                  data-testid="recover-confirm"
                >
                  <p className="font-semibold">
                    载入 v{pendingRecover.revision_number} 的草稿内容？
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {saveState === "dirty"
                      ? "当前有未保存修改，载入将丢弃它们。"
                      : "载入后编辑器将切换为该版本内容。"}
                  </p>
                  {recoverError && (
                    <p className="mt-2 text-xs text-red-600" role="alert">
                      {recoverError}
                    </p>
                  )}
                  <div className="mt-3 flex gap-2">
                    <button
                      type="button"
                      className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground disabled:opacity-50"
                      onClick={() => void confirmRecover(pendingRecover)}
                      disabled={recovering}
                    >
                      {recovering ? (
                        <Loader2 className="size-3 animate-spin" />
                      ) : (
                        <Check className="size-3" />
                      )}
                      确认载入
                    </button>
                    <button
                      type="button"
                      className="rounded-lg border border-border px-3 py-1.5 text-xs font-semibold"
                      onClick={() => {
                        setPendingRecover(null);
                        setRecoverError(null);
                      }}
                      disabled={recovering}
                    >
                      取消
                    </button>
                  </div>
                </div>
              )}

              {/* Rollback two-step approval */}
              {pendingRollback && (
                <div
                  className="mt-4 rounded-xl border border-amber-500/40 bg-amber-500/5 p-4 text-sm"
                  data-testid="rollback-confirm"
                >
                  <p className="flex items-center gap-2 font-semibold text-amber-700">
                    <Undo2 className="size-4" />
                    回滚到 v{pendingRollback.revision_number}？
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    将把该版本作为新的不可变修订（kind=rollback）写入，历史不会被覆盖；
                    当前基线为 v{chapter.revision}。
                  </p>
                  <label className="mt-3 block text-xs font-semibold text-muted-foreground">
                    回滚原因（可选）
                    <input
                      className="mt-1 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
                      value={rollbackReason}
                      onChange={(event) => setRollbackReason(event.target.value)}
                      placeholder="例如：误删段落，恢复上一稿"
                      aria-label="回滚原因"
                      disabled={rollingBack}
                    />
                  </label>
                  <div className="mt-3 flex gap-2">
                    <button
                      type="button"
                      className="inline-flex items-center gap-1.5 rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white disabled:opacity-50"
                      onClick={() => void confirmRollback()}
                      disabled={rollingBack}
                      data-testid="rollback-confirm-button"
                    >
                      {rollingBack ? (
                        <Loader2 className="size-3 animate-spin" />
                      ) : (
                        <Undo2 className="size-3" />
                      )}
                      确认回滚
                    </button>
                    <button
                      type="button"
                      className="rounded-lg border border-border px-3 py-1.5 text-xs font-semibold"
                      onClick={cancelRollback}
                      disabled={rollingBack}
                    >
                      取消
                    </button>
                  </div>
                </div>
              )}

              {/* Rollback failure: shown even after the confirm block closes so a
                  stale 409 conflict is never masked by a fabricated success. */}
              {rollbackError && (
                <div
                  className="mt-4 flex items-start gap-2 rounded-xl border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-600"
                  role="alert"
                  data-testid="rollback-error"
                >
                  <CircleAlert className="mt-0.5 size-4 shrink-0" />
                  <span>{rollbackError}</span>
                </div>
              )}

              {/* Rollback result (only from the actual server response) */}
              {rollbackResult && (
                <div
                  className="mt-4 flex items-start gap-2 rounded-xl border border-emerald-600/40 bg-emerald-600/5 px-3 py-2 text-sm text-emerald-700"
                  role="status"
                  data-testid="rollback-result"
                >
                  <Check className="mt-0.5 size-4 shrink-0" />
                  <span>
                    已回滚为新的 v{rollbackResult.revision.revision_number}
                    {rollbackResult.message ? `：${rollbackResult.message}` : ""}
                  </span>
                </div>
              )}

              {/* Deterministic diff */}
              {revisions !== null && revisions.length > 1 && (
                <div className="mt-4" data-testid="revision-diff">
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <label className="font-semibold text-muted-foreground">
                      基线
                      <select
                        className="ml-1 rounded-lg border border-border bg-background px-2 py-1"
                        value={baseRevisionId ?? ""}
                        onChange={(event) =>
                          setBaseRevisionId(
                            event.target.value ? Number(event.target.value) : null
                          )
                        }
                        data-testid="diff-base"
                      >
                        {oldestFirst.map((revision) => (
                          <option key={revision.id} value={revision.id}>
                            v{revision.revision_number}
                          </option>
                        ))}
                      </select>
                    </label>
                    <span className="text-muted-foreground">→</span>
                    <label className="font-semibold text-muted-foreground">
                      对比
                      <select
                        className="ml-1 rounded-lg border border-border bg-background px-2 py-1"
                        value={targetRevisionId ?? ""}
                        onChange={(event) =>
                          setTargetRevisionId(
                            event.target.value ? Number(event.target.value) : null
                          )
                        }
                        data-testid="diff-target"
                      >
                        {oldestFirst.map((revision) => (
                          <option key={revision.id} value={revision.id}>
                            v{revision.revision_number}
                          </option>
                        ))}
                      </select>
                    </label>
                    {diffLoading && (
                      <Loader2 className="size-3 animate-spin text-muted-foreground" />
                    )}
                    {diff && (
                      <span className="text-muted-foreground">
                        +{diff.additions} / −{diff.deletions}
                      </span>
                    )}
                  </div>
                  {diffError && (
                    <p className="mt-2 text-xs text-red-600" role="alert">
                      {diffError}
                    </p>
                  )}
                  {diff && diff.hunks.length > 0 && (
                    <pre className="mt-2 max-h-64 overflow-auto rounded-xl border border-border/70 bg-background p-3 font-mono text-xs leading-5">
                      {diff.hunks.map((hunk, hunkIndex) => (
                        <div key={hunkIndex} className="mb-1">
                          {hunk.lines.map((line, lineIndex) => (
                            <div
                              key={`${hunkIndex}-${lineIndex}`}
                              className={
                                line.op === "add"
                                  ? "bg-emerald-600/10 text-emerald-700"
                                  : line.op === "delete"
                                    ? "bg-red-600/10 text-red-600"
                                    : "text-muted-foreground"
                              }
                            >
                              {line.op === "add" ? "+ " : line.op === "delete" ? "- " : "  "}
                              {line.text}
                            </div>
                          ))}
                        </div>
                      ))}
                    </pre>
                  )}
                  {diff && diff.hunks.length === 0 && !diffLoading && (
                    <p className="mt-2 text-xs text-muted-foreground">两个版本内容一致。</p>
                  )}
                </div>
              )}
            </>
          )}

          <p className="mt-4 flex items-center gap-1.5 text-[11px] text-muted-foreground">
            <GitBranch className="size-3" />
            编辑只形成 Fanfiction Canon 草稿；发布动作在后续阶段由确定性服务执行，
            本页面不提供发布入口。
          </p>
        </div>
      )}
    </section>
  );
}
