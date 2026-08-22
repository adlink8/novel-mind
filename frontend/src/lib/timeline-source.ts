/**
 * 时间线版本源解析 —— 从 TimelineEnvelope 推导当前应展示的版本、签名与画布数据。
 * 纯函数模块，供 analysis 页面的 timeline workspace hook 与相关组件复用。
 */

import type {
  TimelineEnvelope,
  TimelineVersionSource,
} from "@/lib/api";

/** While a run is live, always surface the candidate version so chart/list grow live. */
export const ACTIVE_RUN = new Set(["pending", "queued", "running", "partial"]);

export function eventsSignature(
  envelope: TimelineEnvelope,
  source: TimelineVersionSource
): string {
  const view = envelope[source] ?? envelope.active ?? envelope.running_candidate;
  if (!view) return "empty";
  const ids = view.events.map((e) => e.id).join(",");
  // include titles so progressive title fixes also refresh list/chart
  const titles = view.events.map((e) => e.title).join("|");
  return `${source}:${view.version_id}:${view.events.length}:${ids}:${titles}`;
}

export function resolveTimelineSource(
  data: TimelineEnvelope,
  preferred: TimelineVersionSource,
  runStatus: string | null | undefined
): TimelineVersionSource {
  const live = Boolean(runStatus && ACTIVE_RUN.has(runStatus));
  const hasActive = Boolean(data.active?.events?.length);
  const hasCandidate = Boolean(data.running_candidate?.events?.length);
  // Live run always prefers the growing candidate.
  if (live && data.running_candidate) {
    return "running_candidate";
  }
  // Prefer preferred only when it actually has events.
  if (preferred === "active" && hasActive) return "active";
  if (preferred === "running_candidate" && data.running_candidate) {
    return "running_candidate";
  }
  // Cancelled/failed runs often leave a rich candidate with no active pointer.
  if (hasCandidate && !hasActive) return "running_candidate";
  if (hasActive) return "active";
  if (data.running_candidate) return "running_candidate";
  if (data.active) return "active";
  return preferred;
}

export function pickTimelineView(
  envelope: TimelineEnvelope,
  source: TimelineVersionSource
) {
  return (
    envelope[source] ??
    envelope.active ??
    envelope.running_candidate ??
    null
  );
}
