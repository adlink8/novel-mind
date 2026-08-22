"use client";

/**
 * Phase 33-04 Illustration — generation candidate gallery (REQ-VIS-04, D-33-03).
 *
 * Pulls the owner/novel candidate gallery from the server and renders it for
 * explicit human review:
 *
 * - candidate cards with job status/error/reason (failed/unknown/paused stay
 *   explicit), the approval state badge, the fail-closed approval gate reason,
 *   the consistency verdict and the append-only review history;
 * - explicit approval actions (approve/reject/supersede/needs_relink): the
 *   browser only submits the action and the server decides the legal
 *   transition (D-33-03) — review truth is never saved client-side;
 * - explicit retry for terminal/paused jobs (original frozen lineage, 33-02);
 *   a provider failure is never presented as an empty success;
 * - a lineage/compare drawer per card (job/attempt/budget evidence + candidate
 *   vs consistency report, D-33-04).
 *
 * The data-fetching wrapper accepts injectable ``loader`` / ``reviewAction`` /
 * ``retryAction`` / ``envelopeLoader`` so component tests can drive
 * error/partial/empty states without a backend.
 */

import axios from "axios";
import { useEffect, useState } from "react";

import type {
  IllustrationGalleryItemView,
  IllustrationGalleryResponse,
  IllustrationJobView,
  IllustrationReviewAction,
  IllustrationReviewActionRequest,
  IllustrationReviewActionResponse,
  IllustrationReviewEnvelope,
} from "@/lib/illustrations-api";
import { illustrationsApi } from "@/lib/illustrations-api";
import { cn } from "@/lib/utils";

import { IllustrationApprovalActions, IllustrationReviewHistory } from "./approval";
import { ILLUSTRATION_STATE_LABEL_TEXT } from "./approval";
import { CONSISTENCY_VERDICT_LABEL, IllustrationCompare, JOB_STATUS_LABEL } from "./compare";

export type IllustrationGalleryProps = {
  novelId: string | number;
  loader?: (
    novelId: string | number
  ) => Promise<IllustrationGalleryResponse>;
  reviewAction?: (
    novelId: string | number,
    assetId: number,
    body: IllustrationReviewActionRequest
  ) => Promise<IllustrationReviewActionResponse>;
  retryAction?: (
    novelId: string | number,
    jobId: number
  ) => Promise<IllustrationJobView>;
  envelopeLoader?: (
    novelId: string | number,
    assetId: number
  ) => Promise<IllustrationReviewEnvelope>;
  className?: string;
};

const DEFAULT_LOADER: NonNullable<IllustrationGalleryProps["loader"]> = (
  novelId
) => illustrationsApi.gallery(novelId).then((res) => res.data);

const DEFAULT_ENVELOPE_LOADER: NonNullable<
  IllustrationGalleryProps["envelopeLoader"]
> = (novelId, assetId) =>
  illustrationsApi.reviewEnvelope(novelId, assetId).then((res) => res.data);

export function IllustrationGallery({
  novelId,
  loader = DEFAULT_LOADER,
  reviewAction,
  retryAction,
  envelopeLoader = DEFAULT_ENVELOPE_LOADER,
  className,
}: IllustrationGalleryProps) {
  const [gallery, setGallery] = useState<IllustrationGalleryResponse | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [envelopes, setEnvelopes] = useState<Record<number, IllustrationReviewEnvelope>>({});
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await loader(novelId);
      setGallery(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载插图候选失败");
      setGallery(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    // `load` 是异步数据加载；首个 setState（loading/error）在 effect 同步段内触发
    // react-hooks/set-state-in-effect 规则，但这是受控加载 pattern，行为是预期的。
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [novelId]);
  const handleReview = async (
    item: IllustrationGalleryItemView,
    action: IllustrationReviewAction,
    reason?: string
  ) => {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const body: IllustrationReviewActionRequest = {
        // Unique per-click idempotency key for the append-only event; only
        // evaluated inside the click handler, never during render.
        // eslint-disable-next-line react-hooks/purity
        event_key: `ill-${item.asset.id}-${action}-${Date.now()}`,
        action,
        actor_source: "human",
        actor: "owner",
        reason:
          reason ??
          `人工审查：${
            action === "approve"
              ? "批准为提案"
              : action === "reject"
                ? "拒绝"
                : action === "supersede"
                  ? "取代"
                  : "需要重新关联"
          }`,
        from_approval_state: item.asset.approval_state,
      };
      if (reviewAction) {
        await reviewAction(novelId, item.asset.id, body);
      } else {
        await illustrationsApi.reviewAsset(novelId, item.asset.id, body);
      }
      setEnvelopes({});
      setExpanded(null);
      await load();
    } catch (err) {
      setError(
        axios.isAxiosError(err) && typeof err.response?.data?.detail === "string"
          ? err.response.data.detail
          : err instanceof Error
            ? err.message
            : "审查操作失败"
      );
    } finally {
      setSubmitting(false);
    }
  };

  const handleRetry = async (item: IllustrationGalleryItemView) => {
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      if (retryAction) {
        await retryAction(novelId, item.job.id);
      } else {
        await illustrationsApi.retryJob(novelId, item.job.id);
      }
      setEnvelopes({});
      setExpanded(null);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "重试失败");
    } finally {
      setSubmitting(false);
    }
  };

  const handleExpand = async (item: IllustrationGalleryItemView) => {
    const next = expanded === item.asset.id ? null : item.asset.id;
    setExpanded(next);
    if (next !== null && !envelopes[next]) {
      try {
        const envelope = await envelopeLoader(novelId, next);
        setEnvelopes((prev) => ({ ...prev, [next]: envelope }));
      } catch (err) {
        setError(err instanceof Error ? err.message : "加载谱系失败");
      }
    }
  };

  if (loading) {
    return (
      <div
        data-testid="illustration-loading"
        className={cn("text-xs text-muted-foreground", className)}
      >
        正在加载插图候选…
      </div>
    );
  }

  if (error) {
    return (
      <div
        data-testid="illustration-error"
        className={cn(
          "rounded-lg border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-xs text-rose-800",
          className
        )}
      >
        无法加载插图候选：{error}
      </div>
    );
  }

  if (!gallery) return null;

  return (
    <div
      data-testid="illustration-gallery"
      className={cn("space-y-3", className)}
    >
      <div
        data-testid="illustration-candidate-only"
        className="rounded-lg border border-amber-500/40 bg-amber-500/5 px-2 py-1.5 text-[11px] text-amber-800"
      >
        生成候选画廊 — 未批准/未决候选不会进入读者/导出；评分只是审查信号，
        不会自动批准。批准只把候选移动为“提案就绪”（Phase 34 才发布）。
      </div>

      {gallery.items.length === 0 ? (
        <p
          data-testid="illustration-empty"
          className="rounded-lg border border-border bg-card px-3 py-2 text-xs text-muted-foreground"
        >
          没有可审查的插图候选 — 显示为空但不视为成功。
        </p>
      ) : (
        <ul data-testid="illustration-gallery-list" className="space-y-3">
          {gallery.items.map((item) => {
            const envelope = envelopes[item.asset.id] ?? null;
            const showCompare = expanded === item.asset.id;
            return (
              <li
                key={item.asset.id}
                data-testid="illustration-card"
                data-asset-id={item.asset.id}
                data-approval-state={item.asset.approval_state}
                data-job-status={item.job.status}
                className="rounded-xl border border-border bg-card p-3"
              >
                <header className="flex flex-wrap items-center gap-2">
                  <h3 className="text-sm font-semibold">
                    修订 #{item.asset.revision_number}
                  </h3>
                  <span
                    data-testid="illustration-approval-state"
                    data-state={item.asset.approval_state}
                    className="rounded-full border px-1.5 py-0.5 text-[10px] font-medium"
                  >
                    {ILLUSTRATION_STATE_LABEL_TEXT[item.asset.approval_state]}
                  </span>
                  <span
                    data-testid="illustration-job-status"
                    data-status={item.job.status}
                    className="rounded-full border border-border bg-muted px-1.5 py-0.5 text-[10px]"
                  >
                    {JOB_STATUS_LABEL[item.job.status] ?? item.job.status}
                  </span>
                  {item.job.error_code ? (
                    <span
                      data-testid="illustration-job-error"
                      className="rounded-full border border-rose-500/40 bg-rose-500/10 px-1.5 py-0.5 text-[10px] text-rose-800"
                    >
                      {item.job.error_code}
                    </span>
                  ) : null}
                  <span className="font-mono text-[10px] text-muted-foreground">
                    {item.job.job_key}
                  </span>
                </header>

                {item.job.status_reason ? (
                  <p
                    data-testid="illustration-job-reason"
                    className="mt-1 text-[10px] text-muted-foreground"
                  >
                    {item.job.status_reason}
                  </p>
                ) : null}

                {/* Fail-closed approval gate (candidate only) */}
                {item.approval_gate ? (
                  <div
                    data-testid="illustration-approval-gate"
                    data-ok={String(item.approval_gate.ok)}
                    data-reason-code={item.approval_gate.reason_code ?? ""}
                    className={cn(
                      "mt-2 rounded-lg border px-2 py-1.5 text-[11px]",
                      item.approval_gate.ok
                        ? "border-emerald-500/40 bg-emerald-500/5 text-emerald-800"
                        : "border-amber-500/40 bg-amber-500/5 text-amber-800"
                    )}
                  >
                    {item.approval_gate.ok
                      ? "提案门已满足 — 可批准为提案"
                      : `提案门未满足：${item.approval_gate.reason_code ?? "unknown"} — ${item.approval_gate.detail ?? ""}`}
                  </div>
                ) : null}

                {/* Consistency verdict (review signal) */}
                {item.consistency ? (
                  <p
                    data-testid="illustration-consistency-verdict"
                    data-verdict={item.consistency.verdict}
                    className="mt-1 text-[11px] text-muted-foreground"
                  >
                    一致性：{CONSISTENCY_VERDICT_LABEL[item.consistency.verdict] ??
                      item.consistency.verdict}
                  </p>
                ) : (
                  <p
                    data-testid="illustration-consistency-missing"
                    className="mt-1 text-[11px] text-muted-foreground"
                  >
                    无一致性证据（评分缺失时不可自动通过）
                  </p>
                )}

                {/* Explicit approval actions */}
                <div className="mt-2">
                  <IllustrationApprovalActions
                    approvalState={item.asset.approval_state}
                    onReview={(action, reason) => handleReview(item, action, reason)}
                    disabled={submitting}
                  />
                </div>

                {/* Retry (failed/unknown/paused, original lineage) */}
                {item.job.status === "failed" ||
                item.job.status === "outcome_unknown" ||
                item.job.status === "paused_budget" ||
                item.job.status === "paused_dependency" ? (
                  <button
                    type="button"
                    data-testid="illustration-retry"
                    disabled={submitting}
                    onClick={() => handleRetry(item)}
                    className="mt-2 rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-800 transition-colors hover:bg-amber-500/20 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    显式重试该任务
                  </button>
                ) : null}

                {/* Lineage / compare drawer */}
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    data-testid="illustration-expand-lineage"
                    disabled={submitting}
                    onClick={() => handleExpand(item)}
                    className="rounded-full border border-border bg-background px-2 py-0.5 text-[10px] text-foreground transition-colors hover:bg-muted"
                  >
                    {showCompare ? "收起谱系/对比" : "查看谱系/对比"}
                  </button>
                </div>
                {showCompare ? (
                  <div className="mt-2">
                    <IllustrationCompare
                      job={envelope?.job ?? item.job}
                      consistency={envelope?.consistency ?? item.consistency}
                      attempts={envelope?.attempts ?? []}
                      budget={envelope?.budget ?? null}
                      onRetry={() => handleRetry(item)}
                      retryDisabled={submitting}
                    />
                  </div>
                ) : null}

                <div className="mt-2">
                  <IllustrationReviewHistory events={item.review_events} />
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
