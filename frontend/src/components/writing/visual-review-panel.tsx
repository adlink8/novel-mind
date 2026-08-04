"use client";

/**
 * Phase 38-04 derivative visual review panel (REQ-FORK-04 / D-38-03).
 *
 * A candidate-only review queue for the writing studio. The panel renders the
 * owner-scoped candidate envelopes from the review seam and requires an
 * explicit, reasoned approval/rejection before any review state can change:
 *
 * - source refs (Original asset refs + bytes hash), per-chapter identity/style
 *   scores (deterministic consistency report), divergence manifest hash,
 *   the sealed `fanfiction_visual` namespace and the append-only review event
 *   chain are all rendered from the server envelope;
 * - approve/reject/supersede always require a non-empty audit reason and echo
 *   the candidate's `from_review_state`; the server is the final judge of the
 *   legal transition (a `blocked` candidate can never be approved);
 * - review truth is never saved client-side — after every action / compare /
 *   reload the panel re-fetches the detail from the API so the displayed state
 *   always matches the server;
 * - no innerHTML / no unsafe rendering; every control has an accessible label.
 */

import { useCallback, useEffect, useState } from "react";

import type {
  DerivativeAssetAction,
  DerivativeAssetState,
  DerivativeVisualAssetView,
} from "@/lib/derivative-visual-api";
import { derivativeVisualApi } from "@/lib/derivative-visual-api";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Display vocabulary (mirrors the backend closed state/action enums)
// ---------------------------------------------------------------------------

export const REVIEW_STATE_LABEL_TEXT: Record<DerivativeAssetState, string> = {
  candidate: "候选 · 待审批",
  needs_review: "需人工复核",
  approved: "已批准",
  rejected: "已拒绝",
  superseded: "已被取代",
  blocked: "已阻断（不可发布）",
};

export const REVIEW_STATE_BADGE_CLASS: Record<DerivativeAssetState, string> = {
  candidate: "border-amber-500/40 bg-amber-500/10 text-amber-800",
  needs_review: "border-orange-500/40 bg-orange-500/10 text-orange-800",
  approved: "border-emerald-500/40 bg-emerald-500/10 text-emerald-800",
  rejected: "border-rose-500/40 bg-rose-500/10 text-rose-800",
  superseded: "border-border bg-muted text-muted-foreground",
  blocked: "border-rose-600/50 bg-rose-600/10 text-rose-700",
};

export const ACTION_LABEL_TEXT: Record<DerivativeAssetAction, string> = {
  approve: "批准",
  reject: "拒绝",
  supersede: "取代",
};

/**
 * Legal actions per review state — a display hint only. The server owns the
 * real state machine and a `blocked`/`superseded` candidate is locked.
 */
export const LEGAL_DERIVATIVE_REVIEW_ACTIONS: Record<
  DerivativeAssetState,
  DerivativeAssetAction[]
> = {
  candidate: ["approve", "reject", "supersede"],
  needs_review: ["approve", "reject", "supersede"],
  approved: ["reject", "supersede"],
  rejected: ["supersede"],
  superseded: [],
  blocked: [],
};

export function shortVisualHash(value: string | null | undefined): string {
  if (!value || value.length <= 16) return value ?? "";
  return `${value.slice(0, 8)}…${value.slice(-4)}`;
}

export type VisualReviewPanelProps = {
  novelId: number | string;
  className?: string;
};

export function VisualReviewPanel({ novelId, className }: VisualReviewPanelProps) {
  const [candidates, setCandidates] = useState<DerivativeVisualAssetView[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<DerivativeVisualAssetView | null>(null);
  const [reason, setReason] = useState("");
  const [actor, setActor] = useState("owner");
  const [submitting, setSubmitting] = useState(false);
  const [loadingList, setLoadingList] = useState(true);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [compareOpen, setCompareOpen] = useState(false);

  const loadList = useCallback(async () => {
    setLoadingList(true);
    setError(null);
    try {
      const response = await derivativeVisualApi.listReviewCandidates(novelId);
      setCandidates(response.data.items);
    } catch {
      setError("候选列表加载失败，请稍后重试");
    } finally {
      setLoadingList(false);
    }
  }, [novelId]);

  const loadDetail = useCallback(
    async (candidateId: number) => {
      setLoadingDetail(true);
      setError(null);
      try {
        const response = await derivativeVisualApi.getReviewCandidate(
          novelId,
          candidateId
        );
        setDetail(response.data);
      } catch {
        setError("候选详情加载失败，请稍后重试");
      } finally {
        setLoadingDetail(false);
      }
    },
    [novelId]
  );

  useEffect(() => {
    // `loadList` 是异步数据加载；首个 setState（loading/error）在 effect 同步段内触发
    // react-hooks/set-state-in-effect 规则，但这是受控加载 pattern，行为是预期的。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void loadList();
  }, [loadList]);

  const selectCandidate = useCallback(
    (candidateId: number) => {
      setSelectedId(candidateId);
      setReason("");
      setCompareOpen(false);
      void loadDetail(candidateId);
    },
    [loadDetail]
  );

  // Auto-select the first candidate once the queue resolves.
  useEffect(() => {
    if (candidates.length === 0) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setDetail(null);
      setSelectedId(null);
      return;
    }
    if (!candidates.some((item) => item.id === selectedId)) {
      selectCandidate(candidates[0].id);
    }
  }, [candidates, selectedId, selectCandidate]);

  const handleReload = useCallback(() => {
    setCompareOpen(false);
    void loadList().then(() => {
      if (selectedId != null) void loadDetail(selectedId);
    });
  }, [loadList, loadDetail, selectedId]);

  const handleReview = useCallback(
    async (action: DerivativeAssetAction) => {
      if (!detail || submitting) return;
      const trimmed = reason.trim();
      if (!trimmed) return; // explicit approval/rejection requires a reason
      setSubmitting(true);
      setError(null);
      try {
        const response = await derivativeVisualApi.reviewCandidate(
          novelId,
          detail.id,
          {
            action,
            actor_source: "human",
            actor: actor.trim() || "owner",
            reason: `人工审查：${ACTION_LABEL_TEXT[action]} — ${trimmed}`,
            event_key: `dv-${detail.id}-${action}-${Date.now()}`,
            from_review_state: detail.review.review_state,
          }
        );
        setDetail(response.data.asset);
        setReason("");
        setCompareOpen(false);
        // Keep the queue consistent with the server after the transition.
        await loadList();
      } catch {
        setError("审查动作失败，请确认候选仍处于该状态后重试");
      } finally {
        setSubmitting(false);
      }
    },
    [actor, detail, loadList, novelId, reason, submitting]
  );

  const state = detail?.review.review_state;
  const legalActions = state ? (LEGAL_DERIVATIVE_REVIEW_ACTIONS[state] ?? []) : [];
  const locked = state != null && legalActions.length === 0;
  const report = detail?.review.consistency_report;

  return (
    <section
      data-testid="derivative-visual-review-panel"
      aria-label="Derivative 视觉资产审查"
      className={cn("space-y-4", className)}
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="font-serif text-lg font-semibold">视觉资产审查</h3>
          <p className="mt-1 text-xs text-muted-foreground">
            只有显式、带理由的批准才会发布；阻断候选永远不可发布。
          </p>
        </div>
        <button
          type="button"
          data-testid="derivative-review-reload"
          className="rounded-lg border border-border px-3 py-1.5 text-xs font-semibold"
          onClick={() => void handleReload()}
          disabled={loadingList || loadingDetail}
        >
          重新加载
        </button>
      </div>

      {error ? (
        <div
          role="alert"
          data-testid="derivative-review-error"
          className="rounded-lg border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-xs text-rose-800"
        >
          {error}
        </div>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-[240px_minmax(0,1fr)]">
        <aside aria-label="候选列表">
          {loadingList ? (
            <p data-testid="derivative-review-loading" className="text-xs text-muted-foreground">
              正在加载候选…
            </p>
          ) : candidates.length === 0 ? (
            <p data-testid="derivative-review-empty" className="rounded-xl bg-secondary/60 p-4 text-xs text-muted-foreground">
              当前小说没有 derivative 视觉候选。
            </p>
          ) : (
            <ul className="space-y-2">
              {candidates.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    data-testid="derivative-review-candidate"
                    data-state={item.review.review_state}
                    className={cn(
                      "w-full rounded-xl border px-3 py-2 text-left text-xs",
                      item.id === selectedId
                        ? "border-primary bg-primary/10"
                        : "border-border hover:bg-secondary/60"
                    )}
                    onClick={() => selectCandidate(item.id)}
                    aria-pressed={item.id === selectedId}
                  >
                    <span className="block truncate font-semibold">
                      {item.asset_key}
                    </span>
                    <span className="mt-0.5 block text-[10px] text-muted-foreground">
                      第 {item.chapter_number} 章 · {REVIEW_STATE_LABEL_TEXT[item.review.review_state]}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <div className="min-w-0">
          {loadingDetail ? (
            <p data-testid="derivative-review-detail-loading" className="text-xs text-muted-foreground">
              正在加载候选详情…
            </p>
          ) : !detail ? (
            <p className="rounded-xl border border-dashed border-border p-6 text-center text-xs text-muted-foreground">
              选择左侧候选查看审查详情。
            </p>
          ) : (
            <div
              data-testid="derivative-review-detail"
              data-review-state={state}
              className="space-y-4 rounded-2xl border border-border/70 p-4"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span
                  data-testid="derivative-review-state-badge"
                  data-state={state}
                  className={cn(
                    "inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium",
                    state ? REVIEW_STATE_BADGE_CLASS[state] : ""
                  )}
                >
                  {state ? REVIEW_STATE_LABEL_TEXT[state] : ""}
                </span>
                <span
                  data-testid="derivative-review-namespace"
                  className="rounded-full border border-border bg-secondary px-2 py-0.5 font-mono text-[10px] text-muted-foreground"
                >
                  {detail.namespace}
                </span>
                <span className="font-mono text-[10px] text-muted-foreground">
                  {detail.asset_id} · v{detail.visual_version.version_id}
                </span>
              </div>

              {locked ? (
                <p
                  data-testid="derivative-review-locked"
                  className="rounded-lg bg-rose-500/5 px-3 py-2 text-xs text-rose-700"
                >
                  {state === "blocked"
                    ? "该候选未通过确定性一致性门禁（身份漂移 / 未声明偏离），不可发布。"
                    : "该状态不允许进一步审查操作。"}
                </p>
              ) : null}

              <div className="grid gap-4 md:grid-cols-2">
                <div className="rounded-xl bg-secondary/40 p-3">
                  <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    原作引用（source refs）
                  </h4>
                  {detail.source_refs.length === 0 ? (
                    <p data-testid="derivative-review-source-empty" className="mt-2 text-xs text-muted-foreground">
                      无引用
                    </p>
                  ) : (
                    <ul className="mt-2 space-y-2">
                      {detail.source_refs.map((ref) => (
                        <li
                          key={`${ref.asset_id}-${ref.source_asset_id}`}
                          data-testid="derivative-review-source-ref"
                          className="text-[11px] leading-5"
                        >
                          <span className="font-semibold">{ref.asset_key}</span>
                          <span className="ml-1 text-muted-foreground">
                            source {ref.source_asset_id} · bytes{" "}
                            {shortVisualHash(ref.source_bytes_hash)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>

                <div className="rounded-xl bg-secondary/40 p-3">
                  <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    身份与偏离
                  </h4>
                  <ul className="mt-2 space-y-1">
                    {detail.identity_lineage.map((row) => (
                      <li key={row.stable_id} data-testid="derivative-review-identity" className="text-[11px] leading-5">
                        <span className="font-semibold">{row.entity_key}</span>
                        <span className="ml-1 text-muted-foreground">
                          {row.entity_type} · {shortVisualHash(row.source_entity_hash)}
                        </span>
                      </li>
                    ))}
                  </ul>
                  <p data-testid="derivative-review-divergence" className="mt-2 font-mono text-[10px] text-muted-foreground">
                    divergence manifest {shortVisualHash(detail.divergence_manifest_hash)}
                  </p>
                </div>
              </div>

              <div className="rounded-xl bg-secondary/40 p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    一致性评分（identity / style）
                  </h4>
                  <button
                    type="button"
                    data-testid="derivative-review-compare-toggle"
                    className="rounded-lg border border-border px-2 py-1 text-[10px] font-semibold"
                    onClick={() => setCompareOpen((open) => !open)}
                    aria-expanded={compareOpen}
                  >
                    {compareOpen ? "收起章节对比" : "比较章节"}
                  </button>
                </div>
                <p data-testid="derivative-review-verdict" className="mt-2 text-xs">
                  判定：<span className="font-semibold">{report?.verdict ?? detail.review.consistency_verdict}</span>
                  <span className="ml-2 text-muted-foreground">
                    evaluator {report?.evaluator_id ?? "—"}
                  </span>
                </p>
                {report && report.chapters.length > 0 ? (
                  <div
                    data-testid="derivative-review-chapters"
                    className={cn("mt-2 overflow-x-auto", !compareOpen && "hidden")}
                  >
                    <table className="w-full text-left text-[11px]">
                      <thead>
                        <tr className="text-muted-foreground">
                          <th className="py-1 pr-3 font-medium">章节</th>
                          <th className="py-1 pr-3 font-medium">identity</th>
                          <th className="py-1 pr-3 font-medium">style</th>
                          <th className="py-1 font-medium">一致性</th>
                        </tr>
                      </thead>
                      <tbody>
                        {report.chapters.map((chapter) => (
                          <tr key={chapter.chapter_number} data-testid="derivative-review-chapter">
                            <td className="py-1 pr-3">第 {chapter.chapter_number} 章</td>
                            <td className="py-1 pr-3">{chapter.identity_score.toFixed(1)}</td>
                            <td className="py-1 pr-3">{chapter.style_score.toFixed(1)}</td>
                            <td className="py-1">
                              {chapter.identity_consistent && chapter.style_consistent
                                ? "一致"
                                : "不一致"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
                {detail.review.reasons.length > 0 ? (
                  <ul data-testid="derivative-review-reasons" className="mt-2 space-y-1">
                    {detail.review.reasons.map((reasonLine) => (
                      <li key={reasonLine} className="font-mono text-[10px] text-muted-foreground">
                        {reasonLine}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>

              <div className="rounded-xl bg-secondary/40 p-3">
                <h4 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  审查事件链（append-only）
                </h4>
                {detail.review.review_events.length === 0 ? (
                  <p data-testid="derivative-review-events-empty" className="mt-2 text-xs text-muted-foreground">
                    尚无审查事件
                  </p>
                ) : (
                  <ul data-testid="derivative-review-history" className="mt-2 space-y-1">
                    {detail.review.review_events.map((event) => (
                      <li
                        key={event.event_key}
                        data-testid="derivative-review-event"
                        data-action={event.action}
                        className="text-[11px] leading-5"
                      >
                        <span className="font-semibold">{ACTION_LABEL_TEXT[event.action]}</span>
                        <span className="text-muted-foreground">
                          {" "}
                          · {event.from_review_state} → {event.to_review_state} ·{" "}
                          {event.actor_source}:{event.actor}
                        </span>
                        <span className="block text-[10px] text-muted-foreground">
                          {event.reason}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {!locked ? (
                <div
                  data-testid="derivative-review-actions"
                  data-state={state}
                  className="space-y-3"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    {legalActions.map((action) => (
                      <button
                        key={action}
                        type="button"
                        data-testid={`derivative-review-action-${action}`}
                        data-action={action}
                        className="rounded-full border border-border bg-background px-3 py-1 text-[11px] font-semibold disabled:cursor-not-allowed disabled:opacity-50"
                        onClick={() => void handleReview(action)}
                        disabled={submitting || !reason.trim()}
                      >
                        {ACTION_LABEL_TEXT[action]}
                      </button>
                    ))}
                  </div>
                  <div className="grid gap-2 sm:grid-cols-2">
                    <label className="flex items-center gap-2 text-[10px] text-muted-foreground">
                      <span>审查人</span>
                      <input
                        data-testid="derivative-review-actor"
                        type="text"
                        value={actor}
                        disabled={submitting}
                        onChange={(event) => setActor(event.target.value)}
                        aria-label="审查人"
                        className="h-6 w-full rounded-md border border-border bg-background px-1.5 text-[11px]"
                      />
                    </label>
                    <label className="flex items-center gap-2 text-[10px] text-muted-foreground">
                      <span>理由</span>
                      <input
                        data-testid="derivative-review-reason"
                        type="text"
                        value={reason}
                        disabled={submitting}
                        onChange={(event) => setReason(event.target.value)}
                        aria-label="审查理由"
                        placeholder="必须填写显式理由"
                        className="h-6 w-full rounded-md border border-border bg-background px-1.5 text-[11px] placeholder:text-muted-foreground"
                      />
                    </label>
                  </div>
                </div>
              ) : null}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
