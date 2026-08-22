"use client";

/**
 * 分析工作台的视图切换与披露确认 UI。
 *
 * 从 app/analysis/page.tsx 拆出的纯展示件：
 * - 顶层视图（对话 | 分析可视化）
 * - 分析切片 facet tabs（时间线 | 人物关系 | 线索与伏笔）
 * - 分析版本 tabs（当前版本 | 候选结果）
 * - 全书剧透确认 dialog
 *
 * 所有状态均通过 props 上提（页面协调），组件自身不持有数据。
 */

import { RefreshCw } from "lucide-react";

import type {
  TimelineEnvelope,
  TimelineRun,
  TimelineVersionSource,
} from "@/lib/api";
import { ACTIVE_RUN } from "@/lib/timeline-source";

/** 顶层视图：对话（默认，含智能体融合）| 分析可视化 */
export type AnalysisPageView = "chat" | "analysis";

export type AnalysisWorkspaceMode = "timeline" | "relationships" | "clues";

export function AnalysisViewTabs({
  pageView,
  onPageViewChange,
}: {
  pageView: AnalysisPageView;
  onPageViewChange: (view: AnalysisPageView) => void;
}) {
  return (
    <div className="flex shrink-0 justify-center">
      <div
        role="tablist"
        aria-label="工作台视图"
        className="inline-flex gap-1 rounded-full border border-border/60 bg-card p-1 shadow-sm"
      >
        {(
          [
            ["chat", "对话"],
            ["analysis", "分析"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={pageView === id}
            data-testid={`analysis-view-tab-${id}`}
            onClick={() => onPageViewChange(id)}
            className={`rounded-full px-4 py-1.5 text-sm transition-colors motion-duration-fast ${
              pageView === id
                ? "bg-foreground font-medium text-background shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}

export function AnalysisFacetTabs({
  workspace,
  onWorkspaceChange,
}: {
  workspace: AnalysisWorkspaceMode;
  onWorkspaceChange: (mode: AnalysisWorkspaceMode) => void;
}) {
  return (
    <div className="flex justify-center">
      <div
        role="tablist"
        aria-label="分析切片"
        className="inline-flex gap-1 rounded-full border border-border/60 bg-card p-1 shadow-sm"
      >
        {(
          [
            ["timeline", "时间线"],
            ["relationships", "人物关系"],
            ["clues", "线索与伏笔"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={workspace === id}
            onClick={() => onWorkspaceChange(id)}
            className={`rounded-full px-4 py-1.5 text-sm transition-colors motion-duration-fast ${
              workspace === id
                ? "bg-foreground font-medium text-background shadow-sm"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}

export function AnalysisVersionTabs({
  envelope,
  source,
  run,
  onSelectSource,
}: {
  envelope: TimelineEnvelope;
  source: TimelineVersionSource;
  run: TimelineRun | null;
  onSelectSource: (source: TimelineVersionSource) => void;
}) {
  return (
    <div
      role="tablist"
      aria-label="分析版本"
      className="flex flex-wrap gap-3 text-xs"
    >
      {envelope.active && (
        <button
          role="tab"
          aria-selected={source === "active"}
          onClick={() => onSelectSource("active")}
          className={`pb-0.5 ${
            source === "active"
              ? "font-medium text-foreground underline underline-offset-4"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          当前版本 · v{envelope.active.version_id}
        </button>
      )}
      {envelope.running_candidate && (
        <button
          role="tab"
          aria-selected={source === "running_candidate"}
          onClick={() => onSelectSource("running_candidate")}
          className={`inline-flex items-center gap-1 pb-0.5 ${
            source === "running_candidate"
              ? "font-medium text-foreground underline underline-offset-4"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          <RefreshCw className="size-3" />
          {ACTIVE_RUN.has(run?.status ?? "")
            ? "正在生成"
            : "候选结果"}{" "}
          · v{envelope.running_candidate.version_id}
          {envelope.running_candidate.events?.length
            ? ` · ${envelope.running_candidate.events.length} 事件`
            : ""}
        </button>
      )}
    </div>
  );
}

export function FullBookConfirmDialog({
  open,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  if (!open) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="确认显示全书"
      className="fixed inset-0 z-50 grid place-items-center bg-black/45 p-4"
    >
      <div className="max-w-md rounded-3xl bg-background p-6 shadow-2xl">
        <h2 className="font-serif text-2xl font-semibold">确认显示全书</h2>
        <p className="mt-3 text-sm text-muted-foreground">
          这会显示阅读进度之后的事件，可能包含重大剧透。偏好将按本书保存。
        </p>
        <div className="mt-6 flex justify-end gap-2">
          <button
            className="rounded-xl border px-4 py-2 text-sm"
            onClick={onCancel}
          >
            取消
          </button>
          <button
            className="rounded-xl bg-foreground px-4 py-2 text-sm text-background"
            onClick={onConfirm}
          >
            确认显示全书
          </button>
        </div>
      </div>
    </div>
  );
}
