"use client";

/**
 * Phase 33-04 Illustration — candidate vs consistency compare + lineage drawer
 * (REQ-VIS-04, D-33-04).
 *
 * Renders the server review envelope exactly as-is (no re-scoring, no
 * client-computed truth):
 *
 * - candidate side: asset identity (mime/dimensions/bytes hash), frozen
 *   source/prompt/model lineage and spoiler cutoff;
 * - report side: the versioned consistency evidence (verdict, scores,
 *   fixture/evaluator lineage). The score is a review signal — it never
 *   auto-approves and never rewrites the Visual Bible;
 * - lineage drawer: the durable job (status/error/retry), the auditable
 *   provider attempts and the budget/cost evidence (D-33-02).
 */

import type {
  ConsistencyReportView,
  IllustrationAttemptView,
  IllustrationBudgetEvidenceView,
  IllustrationJobView,
} from "@/lib/illustrations-api";
import { shortIllustrationHash } from "@/lib/illustrations-api";
import { cn } from "@/lib/utils";

export const CONSISTENCY_VERDICT_LABEL: Record<string, string> = {
  pass: "一致",
  concern: "关注",
  fail: "不一致",
  unavailable: "不可用",
};

export const JOB_STATUS_LABEL: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  paused_budget: "预算暂停",
  paused_dependency: "依赖暂停",
  succeeded: "成功",
  failed: "失败",
  cancelled: "已取消",
  outcome_unknown: "结果未知",
};

function LineageRow({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-2 text-[10px]">
      <span className="text-muted-foreground">{label}</span>
      <span
        className={cn(
          "truncate text-foreground/80",
          mono && "font-mono"
        )}
        title={value}
      >
        {value}
      </span>
    </div>
  );
}

function AttemptRow({ attempt }: { attempt: IllustrationAttemptView }) {
  return (
    <li
      data-testid="illustration-attempt"
      data-attempt={attempt.attempt_number}
      className="rounded-lg border border-border/60 bg-background/60 p-2 text-[10px] text-muted-foreground"
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
        <span data-testid="illustration-attempt-status">
          尝试 #{attempt.attempt_number} · {attempt.status}
        </span>
        {attempt.error_code ? (
          <span className="rounded-full border border-rose-500/40 bg-rose-500/10 px-1.5 py-0.5 text-rose-800">
            {attempt.error_code}
          </span>
        ) : null}
      </div>
      {attempt.provider_request_id ? (
        <p className="mt-1 font-mono" title={attempt.provider_request_id}>
          req {attempt.provider_request_id}
        </p>
      ) : null}
    </li>
  );
}

export type IllustrationCompareProps = {
  job: IllustrationJobView;
  consistency: ConsistencyReportView | null;
  attempts?: IllustrationAttemptView[];
  budget?: IllustrationBudgetEvidenceView | null;
  onRetry?: () => void | Promise<void>;
  retryDisabled?: boolean;
  className?: string;
};

export function IllustrationCompare({
  job,
  consistency,
  attempts = [],
  budget = null,
  onRetry,
  retryDisabled,
  className,
}: IllustrationCompareProps) {
  const usage = (budget?.settled_usage ?? {}) as Record<string, unknown>;

  return (
    <div
      data-testid="illustration-compare"
      data-job-status={job.status}
      className={cn("space-y-3", className)}
    >
      {/* Compare: candidate evidence vs consistency report */}
      <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
        <section
          data-testid="illustration-compare-candidate"
          className="rounded-lg border border-border bg-card p-2"
        >
          <p className="mb-1 text-[10px] font-medium text-muted-foreground">
            生成候选（candidate-only）
          </p>
          <div className="space-y-1">
            <LineageRow label="job_key" value={job.job_key} mono />
            <LineageRow label="状态" value={JOB_STATUS_LABEL[job.status] ?? job.status} />
            {job.status_reason ? (
              <LineageRow label="状态原因" value={job.status_reason} />
            ) : null}
            {job.error_code ? (
              <LineageRow label="错误码" value={job.error_code} />
            ) : null}
            <LineageRow
              label="prompt 修订"
              value={shortIllustrationHash(job.prompt_revision_hash)}
              mono
            />
            <LineageRow
              label="VB 修订"
              value={shortIllustrationHash(job.visual_bible_revision_hash)}
              mono
            />
            <LineageRow
              label="source snapshot"
              value={job.source_snapshot_id}
              mono
            />
            <LineageRow label="截止章" value={String(job.cutoff_chapter)} />
          </div>
          {job.status === "failed" ||
          job.status === "outcome_unknown" ||
          job.status === "paused_budget" ||
          job.status === "paused_dependency" ? (
            <button
              type="button"
              data-testid="illustration-compare-retry"
              disabled={retryDisabled}
              onClick={onRetry}
              className="mt-2 rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[11px] text-amber-800 transition-colors hover:bg-amber-500/20 disabled:cursor-not-allowed disabled:opacity-50"
            >
              显式重试该任务
            </button>
          ) : null}
        </section>

        <section
          data-testid="illustration-compare-report"
          data-verdict={consistency?.verdict ?? "unavailable"}
          className="rounded-lg border border-border bg-card p-2"
        >
          <p className="mb-1 text-[10px] font-medium text-muted-foreground">
            一致性证据（评分是审查信号，非自动批准）
          </p>
          {consistency ? (
            <>
              <p
                data-testid="illustration-consistency-verdict"
                className="text-[12px] font-semibold"
              >
                {CONSISTENCY_VERDICT_LABEL[consistency.verdict] ??
                  consistency.verdict}
              </p>
              <div className="mt-1 space-y-1">
                <LineageRow
                  label="evaluator"
                  value={`${consistency.evaluator_id}@${consistency.evaluator_version}`}
                />
                <LineageRow
                  label="fixture"
                  value={shortIllustrationHash(consistency.fixture_set_hash)}
                  mono
                />
                {Object.entries(consistency.scores ?? {}).map(([key, value]) => (
                  <LineageRow
                    key={key}
                    label={key}
                    value={
                      typeof value === "number"
                        ? value.toFixed(4)
                        : String(value)
                    }
                  />
                ))}
              </div>
            </>
          ) : (
            <p
              data-testid="illustration-consistency-missing"
              className="text-[11px] text-muted-foreground"
            >
              尚无一致性证据 — 评分缺失时不可自动通过。
            </p>
          )}
        </section>
      </div>

      {/* Lineage drawer: durable job + attempts + budget/cost evidence */}
      <div
        data-testid="illustration-lineage-drawer"
        className="rounded-lg border border-border bg-card p-2"
      >
        <p className="mb-1 text-[10px] font-medium text-muted-foreground">
          持久化谱系（job / attempt / budget）
        </p>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
          <div className="space-y-1">
            <LineageRow label="job" value={String(job.id)} />
            <LineageRow label="重试次数" value={String(job.retry_count)} />
            <LineageRow
              label="idempotency"
              value={shortIllustrationHash(job.idempotency_key)}
              mono
            />
            <LineageRow
              label="config hash"
              value={shortIllustrationHash(job.config_hash)}
              mono
            />
          </div>
          <div className="space-y-1">
            {attempts.length === 0 ? (
              <p className="text-[10px] text-muted-foreground">
                无 provider 尝试记录
              </p>
            ) : (
              <ul className="space-y-1">
                {attempts.map((attempt) => (
                  <AttemptRow key={attempt.id} attempt={attempt} />
                ))}
              </ul>
            )}
          </div>
          <div
            data-testid="illustration-budget-evidence"
            className="space-y-1"
          >
            {budget ? (
              <>
                <LineageRow
                  label="已结算成本"
                  value={
                    budget.settled_cost_usd != null
                      ? `$${budget.settled_cost_usd}`
                      : "未知"
                  }
                />
                <LineageRow
                  label="结算状态"
                  value={budget.reservation_status}
                />
                {usage.usage_unknown === true ? (
                  <LineageRow label="用量" value="未知（显式）" />
                ) : (
                  <LineageRow
                    label="用量"
                    value={`${String(usage.input_tokens ?? 0)} 入 / ${String(
                      usage.output_tokens ?? 0
                    )} 出`}
                  />
                )}
                <LineageRow label="价格快照" value={budget.price_snapshot?.provider as string ?? "mock"} />
              </>
            ) : (
              <p className="text-[10px] text-muted-foreground">
                无预算结算证据
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
