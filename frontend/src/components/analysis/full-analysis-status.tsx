import type { FullAnalysisRun } from "@/lib/api";

const STAGES: Array<[string, string]> = [
  ["indexing", "分块与层级"],
  ["timeline_extract", "时间线提取"],
  ["timeline_reconcile", "时间线归并"],
  ["relationship_judgment", "人物关系判决"],
  ["clue_judgment", "线索判决"],
  ["nm_chapter_state", "章节叙事记忆"],
  ["nm_arc_plan", "故事弧规划"],
  ["nm_aggregate", "叙事记忆聚合"],
];

const ACTIVE = new Set(["pending", "running"]);

function parseProgress(value: string): [number, number] {
  const match = /^(\d+)\/(\d+)$/.exec(value || "");
  return match ? [Number(match[1]), Number(match[2])] : [0, 0];
}

function labelFor(stage: string): string {
  return STAGES.find(([key]) => key === stage)?.[1] || stage || "准备中";
}

function statusLabel(status: string): string {
  switch (status) {
    case "completed":
      return "已完成";
    case "failed":
      return "失败";
    case "paused_dependency":
      return "等待依赖恢复";
    case "paused_budget":
      return "预算已暂停";
    case "cancelled":
      return "已停止";
    default:
      return "进行中";
  }
}

export function FullAnalysisStatus({
  run,
  busy,
  onStart,
  onCancel,
}: {
  run: FullAnalysisRun | null;
  busy?: boolean;
  onStart: () => void;
  onCancel: () => void;
}) {
  const status = run?.status ?? "empty";
  const [completed, total] = parseProgress(run?.progress ?? "0/0");
  const stageIndex = Math.max(0, run?.stage_index ?? 0);
  const stageTotal = run?.stage_total || STAGES.length;
  const inner = total > 0 ? Math.min(1, completed / total) : 0;
  const percent = run
    ? Math.min(100, Math.round(((stageIndex - 1 + inner) / stageTotal) * 100))
    : 0;
  const active = ACTIVE.has(status);
  const failed =
    status === "failed" ||
    status === "paused_budget" ||
    status === "paused_dependency";

  return (
    <div
      className="grid gap-2 rounded-xl border border-primary/20 bg-primary/[0.04] p-3"
      role="status"
      aria-live="polite"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-semibold">全部分析</p>
          <p className="text-xs text-muted-foreground">
            {run
              ? `${labelFor(run.stage)} · ${statusLabel(run.status)}`
              : "按依赖顺序运行所有分析管线"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {active ? (
            <button
              type="button"
              disabled={busy}
              onClick={onCancel}
              className="rounded-md px-2.5 py-1 text-xs text-amber-900 hover:bg-amber-50 disabled:opacity-50"
            >
              停止
            </button>
          ) : (
            <button
              type="button"
              disabled={busy}
              onClick={onStart}
              className="rounded-md bg-foreground px-2.5 py-1 text-xs text-background hover:opacity-90 disabled:opacity-50"
            >
              {run?.status === "completed" ? "重新全部分析" : "开始全部分析"}
            </button>
          )}
        </div>
      </div>
      {run && (active || failed || status === "completed") && (
        <>
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-[width]"
                style={{ width: `${status === "completed" ? 100 : percent}%` }}
                role="progressbar"
                aria-valuenow={status === "completed" ? 100 : percent}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label="全部分析进度"
              />
            </div>
            <span>{status === "completed" ? "100%" : `${percent}%`}</span>
          </div>
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
            {STAGES.map(([key, label], index) => (
              <span
                key={key}
                className={
                  index + 1 < stageIndex
                    ? "text-emerald-700"
                    : key === run.stage
                      ? "font-medium text-foreground"
                      : undefined
                }
              >
                {index + 1 < stageIndex ? "✓ " : ""}
                {label}
              </span>
            ))}
          </div>
        </>
      )}
      {run?.status_reason && (
        <p className="text-xs text-destructive">{run.status_reason}</p>
      )}
    </div>
  );
}
