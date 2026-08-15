"use client";

/**
 * Phase 28-04 cross-dimension closure progress panel (REQ-NM-03/04).
 *
 * Pure presentational view of the one-click analysis report: every dimension
 * is available/partial/blocked, with durable progress, resume state, and the
 * candidate-only publication badge. This component never stores or rebuilds
 * authoritative state — on reload/reconnect the parent must refetch from the
 * DB-backed analysis endpoints.
 */

import { cn } from "@/lib/utils";

export type DimensionStatusValue = "available" | "partial" | "blocked";

export interface DimensionProgressItem {
  dimension: string;
  status: DimensionStatusValue;
  progress: number;
  blocked_reason?: string | null;
}

export interface NarrativeMemoryProgressProps {
  dimensions?: DimensionProgressItem[];
  /** Overall closure progress 0..1 */
  progress?: number;
  resumable?: boolean;
  resumeCount?: number;
  runStatus?: string | null;
  cutoff?: number;
  manifestChecksum?: string;
  className?: string;
}

const STATUS_LABEL: Record<DimensionStatusValue, string> = {
  available: "可用",
  partial: "部分",
  blocked: "阻塞",
};

const DIMENSION_LABEL: Record<string, string> = {
  timeline: "时间线",
  relationship: "人物关系",
  clue: "线索",
  character: "人物",
  world: "世界观",
};

function percent(value: number | undefined): string {
  const ratio = Number.isFinite(value) ? (value ?? 0) : 0;
  return `${Math.round(Math.min(Math.max(ratio, 0), 1) * 100)}%`;
}

export function NarrativeMemoryProgress({
  dimensions = [],
  progress = 0,
  resumable = false,
  resumeCount = 0,
  runStatus = null,
  cutoff,
  manifestChecksum,
  className,
}: NarrativeMemoryProgressProps) {
  return (
    <section
      data-testid="nm-progress-panel"
      aria-label="跨维度分析进度"
      className={cn(
        "space-y-3 rounded-lg border border-border/40 bg-card/40 p-3",
        className
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-serif text-sm font-semibold tracking-tight">
          一键分析
        </h3>
        <span
          data-testid="nm-candidate-badge"
          className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-700"
        >
          candidate_preview
        </span>
      </div>

      <div data-testid="nm-progress-bar" className="space-y-1">
        <div className="flex items-center justify-between text-[11px] text-muted-foreground">
          <span data-testid="nm-progress-label">整体进度</span>
          <span data-testid="nm-progress-value">{percent(progress)}</span>
        </div>
        <div
          role="progressbar"
          aria-valuenow={Math.round((progress ?? 0) * 100)}
          aria-valuemin={0}
          aria-valuemax={100}
          className="h-1.5 w-full overflow-hidden rounded-full bg-muted/60"
        >
          <div
            data-testid="nm-progress-fill"
            className="h-full rounded-full bg-primary transition-[width] motion-duration-spatial"
            style={{ width: percent(progress) }}
          />
        </div>
      </div>

      <ul data-testid="nm-dimension-list" className="space-y-1.5">
        {dimensions.length === 0 ? (
          <li
            data-testid="nm-dimensions-empty"
            className="rounded-md border border-dashed border-border/50 px-2 py-2 text-[11px] text-muted-foreground"
          >
            尚无维度分析结果
          </li>
        ) : (
          dimensions.map((item) => (
            <li
              key={item.dimension}
              data-testid={`nm-dimension-${item.dimension}`}
              data-status={item.status}
              className="flex items-center justify-between gap-2 rounded-md border border-border/30 bg-background/40 px-2 py-1.5"
            >
              <span className="min-w-0 truncate text-xs font-medium">
                {DIMENSION_LABEL[item.dimension] ?? item.dimension}
              </span>
              <span
                data-testid={`nm-dimension-status-${item.dimension}`}
                className={cn(
                  "shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium",
                  item.status === "available" &&
                    "bg-emerald-500/10 text-emerald-700",
                  item.status === "partial" &&
                    "bg-amber-500/10 text-amber-700",
                  item.status === "blocked" && "bg-rose-500/10 text-rose-700"
                )}
              >
                {STATUS_LABEL[item.status]}
              </span>
              {item.status === "blocked" && item.blocked_reason ? (
                <span
                  data-testid={`nm-dimension-reason-${item.dimension}`}
                  title={item.blocked_reason}
                  className="min-w-0 truncate text-[10px] text-muted-foreground"
                >
                  {item.blocked_reason}
                </span>
              ) : (
                <span className="shrink-0 text-[10px] text-muted-foreground">
                  {percent(item.progress)}
                </span>
              )}
            </li>
          ))
        )}
      </ul>

      <div
        data-testid="nm-progress-meta"
        className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border/30 pt-2 text-[10px] text-muted-foreground"
      >
        {resumable ? (
          <span data-testid="nm-resume-state" className="text-emerald-700">
            可恢复 · DB checkpoint 权威
          </span>
        ) : (
          <span data-testid="nm-resume-state">不可恢复</span>
        )}
        {resumeCount > 0 && (
          <span data-testid="nm-resume-count">resume ×{resumeCount}</span>
        )}
        {runStatus && (
          <span data-testid="nm-run-status">run {runStatus}</span>
        )}
        {typeof cutoff === "number" && cutoff > 0 && (
          <span data-testid="nm-cutoff">cutoff ≤ 第 {cutoff} 章</span>
        )}
        {manifestChecksum && (
          <span
            data-testid="nm-manifest-checksum"
            title={manifestChecksum}
            className="truncate"
          >
            manifest {manifestChecksum.slice(0, 12)}…
          </span>
        )}
      </div>
    </section>
  );
}
