"use client";

/**
 * Phase 32 — Prompt diff / explicit edit workspace (REQ-VIS-03, D-32-04).
 *
 * Renders the deterministic edit diff between a PromptRevision and its parent
 * and provides the explicit human-edit action:
 *
 * - changed canonical sections side-by-side (original vs current), plus the
 *   changed negative constraints / uncertainties and the prompt-text marker;
 * - a stale banner when the revision was compiled against a superseded Visual
 *   Bible revision or source snapshot — a stale prompt cannot be silently
 *   reused (D-32-03);
 * - an explicit edit action that only touches a `user_interpretation` detail
 *   (or adds a new labeled interpretation); the server decides legality and a
 *   failed edit surfaces the validation error (fail closed);
 * - a no-provider-call marker: Phase 32 preview/diff/edit never invokes an
 *   image provider, and the diff is server-computed, never assembled locally.
 *
 * Loaders are injectable so component tests can drive error/stale/validation
 * states without a backend.
 */

import { useEffect, useState } from "react";

import type {
  PromptDetailResponse,
  PromptDiffResponse,
  PromptEditRequest,
  PromptEditResponse,
  SpecDetailKind,
} from "@/lib/scene-spec-api";
import { promptRevisionsApi } from "@/lib/scene-spec-api";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Labels
// ---------------------------------------------------------------------------

export const EDIT_KIND_LABELS: Record<SpecDetailKind, string> = {
  subject: "主体",
  action: "动作",
  setting: "场景",
  composition: "构图",
  style: "风格",
  continuity: "连续性",
};

export const EDIT_KINDS: SpecDetailKind[] = [
  "subject",
  "action",
  "setting",
  "composition",
  "style",
  "continuity",
];

// ---------------------------------------------------------------------------
// Fetching workspace wrapper (owner/novel/revision scoped)
// ---------------------------------------------------------------------------

export type PromptDiffProps = {
  novelId: string | number;
  revisionId: number;
  diffLoader?: (
    novelId: string | number,
    revisionId: number
  ) => Promise<PromptDiffResponse>;
  detailLoader?: (
    novelId: string | number,
    revisionId: number
  ) => Promise<PromptDetailResponse>;
  editAction?: (
    novelId: string | number,
    revisionId: number,
    body: PromptEditRequest
  ) => Promise<PromptEditResponse>;
  className?: string;
};

const DEFAULT_DIFF_LOADER: NonNullable<PromptDiffProps["diffLoader"]> = (
  novelId,
  revisionId
) => promptRevisionsApi.getRevisionDiff(novelId, revisionId).then((res) => res.data);

const DEFAULT_DETAIL_LOADER: NonNullable<PromptDiffProps["detailLoader"]> = (
  novelId,
  revisionId
) => promptRevisionsApi.getRevision(novelId, revisionId).then((res) => res.data);

const DEFAULT_EDIT_ACTION: NonNullable<PromptDiffProps["editAction"]> = (
  novelId,
  revisionId,
  body
) => promptRevisionsApi.edit(novelId, revisionId, body).then((res) => res.data);

export function PromptDiff({
  novelId,
  revisionId,
  diffLoader = DEFAULT_DIFF_LOADER,
  detailLoader = DEFAULT_DETAIL_LOADER,
  editAction = DEFAULT_EDIT_ACTION,
  className,
}: PromptDiffProps) {
  const [diff, setDiff] = useState<PromptDiffResponse | null>(null);
  const [stale, setStale] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [detailKey, setDetailKey] = useState("");
  const [kind, setKind] = useState<SpecDetailKind>("style");
  const [text, setText] = useState("");
  const [author, setAuthor] = useState("");
  const [rationale, setRationale] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [edited, setEdited] = useState<PromptEditResponse | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [diffResult, detailResult] = await Promise.all([
        diffLoader(novelId, revisionId),
        detailLoader(novelId, revisionId),
      ]);
      setDiff(diffResult);
      setStale(detailResult.stale);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载 Prompt diff 失败");
      setDiff(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [novelId, revisionId]);

  const handleEdit = async () => {
    if (submitting) return;
    setValidationError(null);
    setSubmitting(true);
    try {
      const result = await editAction(novelId, revisionId, {
        prompt_key: `edited-${revisionId}-${Date.now()}`,
        detail_key: detailKey,
        kind,
        text,
        author,
        rationale,
      });
      setEdited(result);
      // Re-load the diff for the new child revision (auditable lineage).
      await load();
    } catch (err) {
      setValidationError(
        err instanceof Error ? err.message : "编辑被服务端拒绝"
      );
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div
        data-testid="prompt-diff-loading"
        className={cn("text-xs text-muted-foreground", className)}
      >
        正在加载 Prompt diff…
      </div>
    );
  }

  if (error) {
    return (
      <div
        data-testid="prompt-diff-error"
        className={cn(
          "rounded-lg border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-xs text-rose-800",
          className
        )}
      >
        无法加载 Prompt diff：{error}
      </div>
    );
  }

  if (!diff) return null;

  const changedSections = diff.changed_sections ?? [];
  const changedConstraints = diff.changed_negative_constraints ?? [];
  const changedUncertainties = diff.changed_uncertainties ?? [];

  return (
    <div
      data-testid="prompt-diff"
      className={cn("space-y-3", className)}
    >
      <header className="flex flex-wrap items-center gap-2 rounded-xl border border-border bg-card p-3">
        <p className="text-xs font-medium">Prompt diff（修订 #{diff.revision_number}）</p>
        <span className="font-mono text-[10px] text-muted-foreground">
          {diff.original_prompt_hash.slice(0, 8)}… → {diff.current_prompt_hash.slice(0, 8)}…
        </span>
        {diff.same ? (
          <span className="rounded-full border border-border bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
            无内容变化
          </span>
        ) : (
          <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-800">
            有内容变化
          </span>
        )}
      </header>

      {/* No-provider-call state: Phase 32 never invokes an image provider. */}
      <div
        data-testid="prompt-diff-no-provider"
        className="rounded-lg border border-border bg-card px-2 py-1.5 text-[11px] text-muted-foreground"
      >
        预览 / diff / 编辑均为服务端纯编译 — provider_calls: 0，不触发生成。
      </div>

      {stale ? (
        <div
          data-testid="prompt-diff-stale"
          className="rounded-lg border border-orange-500/40 bg-orange-500/5 px-2 py-1.5 text-[11px] text-orange-800"
        >
          该 Prompt 已过期（Visual Bible / 源快照已变更）— 静默复用已被拒绝。
        </div>
      ) : null}

      {changedSections.length === 0 &&
      changedConstraints.length === 0 &&
      changedUncertainties.length === 0 &&
      !diff.prompt_text_changed ? (
        <p
          data-testid="prompt-diff-empty"
          className="rounded-lg border border-border bg-card px-3 py-2 text-xs text-muted-foreground"
        >
          该修订与父修订没有可显示的差异。
        </p>
      ) : (
        <div className="space-y-2">
          {changedSections.map((section) => (
            <div
              key={section.section_key}
              data-testid="prompt-diff-section"
              data-section={section.section_key}
              className="grid grid-cols-2 gap-2 rounded-lg border border-border/60 bg-background/60 p-2"
            >
              <div className="min-w-0">
                <p className="mb-1 text-[10px] font-medium text-muted-foreground">
                  {section.section_key} · 原
                </p>
                <pre className="whitespace-pre-wrap break-words text-[11px] text-muted-foreground">
                  {section.original ?? "(无)"}
                </pre>
              </div>
              <div className="min-w-0">
                <p className="mb-1 text-[10px] font-medium text-muted-foreground">
                  {section.section_key} · 新
                </p>
                <pre className="whitespace-pre-wrap break-words text-[11px] text-foreground/85">
                  {section.current ?? "(无)"}
                </pre>
              </div>
            </div>
          ))}

          {changedConstraints.length > 0 ? (
            <div className="rounded-lg border border-border/60 bg-background/60 p-2">
              <p className="mb-1 text-[10px] font-medium text-muted-foreground">
                负面约束变化
              </p>
              <ul className="space-y-1">
                {changedConstraints.map((item) => (
                  <li
                    key={item.item}
                    data-testid="prompt-diff-constraint"
                    className="text-[11px] text-muted-foreground"
                  >
                    {item.item}（原 {item.original_count ?? 0} → 现 {item.current_count ?? 0}）
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {changedUncertainties.length > 0 ? (
            <div className="rounded-lg border border-border/60 bg-background/60 p-2">
              <p className="mb-1 text-[10px] font-medium text-muted-foreground">
                未解析项变化
              </p>
              <ul className="space-y-1">
                {changedUncertainties.map((item) => (
                  <li
                    key={item.item}
                    data-testid="prompt-diff-uncertainty"
                    className="text-[11px] text-muted-foreground"
                  >
                    {item.item}（原 {item.original_count ?? 0} → 现 {item.current_count ?? 0}）
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      )}

      {edited ? (
        <div
          data-testid="prompt-diff-edited"
          className="rounded-lg border border-emerald-500/40 bg-emerald-500/5 px-2 py-1.5 text-[11px] text-emerald-800"
        >
          已生成新候选修订 #{edited.revision.revision_number}（{edited.revision.prompt_key}），
          diff 已保留；该候选未经审批不会进入生成。
        </div>
      ) : null}

      {validationError ? (
        <div
          data-testid="prompt-diff-validation-error"
          className="rounded-lg border border-rose-500/40 bg-rose-500/5 px-2 py-1.5 text-[11px] text-rose-800"
        >
          编辑被拒绝：{validationError}
        </div>
      ) : null}

      {/* Explicit edit action (D-32-04): only user_interpretation details are
          editable; the server decides legality and returns the validation error. */}
      <form
        data-testid="prompt-diff-edit-form"
        className="space-y-2 rounded-lg border border-border bg-card p-3"
        onSubmit={(event) => {
          event.preventDefault();
          void handleEdit();
        }}
      >
        <p className="text-[10px] font-medium text-muted-foreground">
          显式编辑（仅 user_interpretation 细节可改；生成新候选修订）
        </p>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <label className="flex flex-col gap-0.5 text-[10px] text-muted-foreground">
            细节 key
            <input
              data-testid="prompt-diff-edit-detail-key"
              value={detailKey}
              placeholder="user-lighting"
              onChange={(event) => setDetailKey(event.target.value)}
              className="h-7 rounded-md border border-border bg-background px-1.5 text-[11px] text-foreground placeholder:text-muted-foreground"
            />
          </label>
          <label className="flex flex-col gap-0.5 text-[10px] text-muted-foreground">
            类型
            <select
              data-testid="prompt-diff-edit-kind"
              value={kind}
              onChange={(event) => setKind(event.target.value as SpecDetailKind)}
              className="h-7 rounded-md border border-border bg-background px-1.5 text-[11px] text-foreground"
            >
              {EDIT_KINDS.map((kindValue) => (
                <option key={kindValue} value={kindValue}>
                  {EDIT_KIND_LABELS[kindValue]}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label className="flex flex-col gap-0.5 text-[10px] text-muted-foreground">
          文本
          <textarea
            data-testid="prompt-diff-edit-text"
            value={text}
            onChange={(event) => setText(event.target.value)}
            className="min-h-16 rounded-md border border-border bg-background px-1.5 py-1 text-[11px] text-foreground placeholder:text-muted-foreground"
          />
        </label>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <label className="flex flex-col gap-0.5 text-[10px] text-muted-foreground">
            作者
            <input
              data-testid="prompt-diff-edit-author"
              value={author}
              placeholder="test-editor"
              onChange={(event) => setAuthor(event.target.value)}
              className="h-7 rounded-md border border-border bg-background px-1.5 text-[11px] text-foreground placeholder:text-muted-foreground"
            />
          </label>
          <label className="flex flex-col gap-0.5 text-[10px] text-muted-foreground">
            理由
            <input
              data-testid="prompt-diff-edit-rationale"
              value={rationale}
              placeholder="人工补充解读"
              onChange={(event) => setRationale(event.target.value)}
              className="h-7 rounded-md border border-border bg-background px-1.5 text-[11px] text-foreground placeholder:text-muted-foreground"
            />
          </label>
        </div>
        <button
          type="submit"
          data-testid="prompt-diff-edit-submit"
          disabled={submitting || !detailKey.trim() || !text.trim()}
          className="rounded-full border border-primary bg-primary px-3 py-1 text-[11px] text-primary-foreground transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "提交中…" : "提交编辑"}
        </button>
      </form>
    </div>
  );
}
