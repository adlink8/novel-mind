import type { TimelineRun } from "@/lib/api";

const labels: Record<string, string> = {
  empty: "尚未生成",
  pending: "等待开始",
  queued: "等待开始",
  running: "正在分析",
  partial: "已有部分结果",
  paused: "已暂停",
  paused_dependency: "依赖未就绪 / 模型调用暂停",
  paused_budget: "预算不足，已暂停",
  failed: "分析失败",
  completed: "分析完成",
  cancelled: "已暂停",
};

const ACTIVE = new Set(["pending", "queued", "running", "partial"]);
const RESUMABLE = new Set([
  "cancelled",
  "paused",
  "paused_dependency",
  "paused_budget",
  "failed",
]);

export function TimelineStatus({
  run,
  hasEvents,
  onPause,
  onResume,
  onStart,
  actionBusy,
}: {
  run: TimelineRun | null;
  hasEvents: boolean;
  onPause?: () => void;
  onResume?: () => void;
  /** 尚未开始 / 无 run 时手动启动 */
  onStart?: () => void;
  actionBusy?: boolean;
}) {
  const status = run?.status ?? (hasEvents ? "completed" : "empty");
  const progress = run?.progress ?? {};
  const completed = Number(
    progress.completed_chapters ?? progress.chapters_completed ?? 0
  );
  const total = Number(progress.total_chapters ?? progress.chapters_total ?? 0);
  const percent =
    total > 0
      ? Math.min(100, Math.round((completed / total) * 100))
      : status === "completed"
        ? 100
        : 0;

  const showProgress =
    ACTIVE.has(status) ||
    status === "paused_budget" ||
    status === "cancelled" ||
    status === "partial";

  // 未开始 / 无 run：开始分析。已完成也可再点（后端按策略续跑或返回已完成）
  const canStart =
    Boolean(onStart) &&
    (status === "empty" || !run || status === "completed");
  const canResume =
    Boolean(onResume) && RESUMABLE.has(status);

  return (
    <div
      className="grid gap-2 rounded-2xl border bg-card p-4"
      role="status"
      aria-live="polite"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-semibold">{labels[status] ?? status}</span>
        <div className="flex flex-wrap items-center gap-2">
          {run?.updated_at && (
            <time
              className="text-xs text-muted-foreground"
              dateTime={run.updated_at}
            >
              更新于 {new Date(run.updated_at).toLocaleString("zh-CN")}
            </time>
          )}
          {ACTIVE.has(status) && onPause && (
            <button
              type="button"
              disabled={actionBusy}
              onClick={onPause}
              className="rounded-lg border border-amber-400/80 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-950 hover:bg-amber-100 disabled:opacity-50"
            >
              暂停
            </button>
          )}
          {canResume && (
            <button
              type="button"
              disabled={actionBusy}
              onClick={onResume}
              className="rounded-lg bg-foreground px-3 py-1.5 text-xs font-medium text-background hover:opacity-90 disabled:opacity-50"
            >
              继续分析
            </button>
          )}
          {canStart && (
            <button
              type="button"
              disabled={actionBusy}
              onClick={onStart}
              className="rounded-lg bg-foreground px-3 py-1.5 text-xs font-medium text-background hover:opacity-90 disabled:opacity-50"
            >
              {hasEvents || status === "completed" ? "重新分析" : "开始分析"}
            </button>
          )}
        </div>
      </div>
      {showProgress && (
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-[width] motion-duration-fast motion-ease-enter"
              style={{ width: `${percent}%` }}
              role="progressbar"
              aria-valuenow={percent}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label="分析进度"
            />
          </div>
          <span aria-live="polite">
            {total ? `${completed}/${total} 章` : "准备中"}
          </span>
        </div>
      )}
      {run?.status_reason && (
        <p className="text-sm text-destructive">{run.status_reason}</p>
      )}
      <p className="text-xs text-muted-foreground">
        {status === "empty"
          ? "已选中小说，但尚未启动分析。确认后请点「开始分析」。"
          : "流程：准备场景层级 → 按章抽取事件 → 跨章调和 → 发布版本。已完成章节会保存，可暂停后续跑。"}
      </p>
    </div>
  );
}
