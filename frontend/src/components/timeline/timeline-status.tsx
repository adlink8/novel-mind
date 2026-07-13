import type { TimelineRun } from "@/lib/api";

const labels: Record<string, string> = {
  empty: "尚未生成",
  queued: "等待开始",
  running: "正在分析",
  partial: "已有部分结果",
  paused: "已暂停",
  paused_budget: "预算不足，已暂停",
  failed: "分析失败",
  completed: "分析完成",
  cancelled: "已取消",
};

export function TimelineStatus({ run, hasEvents }: { run: TimelineRun | null; hasEvents: boolean }) {
  const status = run?.status ?? (hasEvents ? "completed" : "empty");
  const progress = run?.progress ?? {};
  const completed = Number(progress.completed_chapters ?? progress.chapters_completed ?? 0);
  const total = Number(progress.total_chapters ?? progress.chapters_total ?? 0);
  const percent = total > 0 ? Math.min(100, Math.round((completed / total) * 100)) : status === "completed" ? 100 : 0;
  return (
    <div className="grid gap-2 rounded-2xl border bg-card p-4" role="status" aria-live="polite">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-semibold">{labels[status] ?? status}</span>
        {run?.updated_at && <time className="text-xs text-muted-foreground" dateTime={run.updated_at}>更新于 {new Date(run.updated_at).toLocaleString("zh-CN")}</time>}
      </div>
      {(status === "running" || status === "partial" || status === "paused_budget") && (
        <div className="flex items-center gap-3 text-xs text-muted-foreground">
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${percent}%` }} /></div>
          <span>{total ? `${completed}/${total} 章` : "准备中"}</span>
        </div>
      )}
      {run?.status_reason && <p className="text-sm text-destructive">{run.status_reason}</p>}
    </div>
  );
}
