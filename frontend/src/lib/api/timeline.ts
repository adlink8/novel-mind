/**
 * 时间线 API。
 */

import { api } from "./client";

export type TimelineVersionSource = "active" | "running_candidate";
export type TimelineOrdering = "narrative" | "story";

export interface TimelineParticipant {
  mention: string;
  entity_id?: number | null;
}

export interface TimelineEvent {
  id: number;
  logical_event_id: string;
  title: string;
  description: string;
  event_type: string;
  narrative_chapter_number: number;
  source_start: number;
  narrative_index: number;
  story_rank?: number | null;
  time_precision: "exact" | "relative" | "fuzzy" | "unknown";
  time_expression?: string | null;
  confidence: number;
  participants: TimelineParticipant[];
  provenance: Record<string, "machine" | "manual">;
}

export interface TimelineCausalEdge {
  source_event_id: number;
  target_event_id: number;
  edge_type: string;
  confidence: number;
}

export interface TimelineVersionView {
  source: TimelineVersionSource;
  version_id: number;
  status: string;
  progress: Record<string, unknown>;
  events: TimelineEvent[];
  causal_edges: TimelineCausalEdge[];
  counts: { events: number; participants: number; causal_edges: number };
  aggregates: Record<string, number>;
  previews: string[];
}

export interface TimelineEnvelope {
  active: TimelineVersionView | null;
  running_candidate: TimelineVersionView | null;
}

export interface TimelineRun {
  id: number;
  novel_id: number;
  version_id?: number | null;
  status: string;
  status_reason?: string | null;
  progress: Record<string, unknown>;
  cancel_requested: boolean;
  updated_at?: string | null;
}

export interface TimelineQuery {
  ordering?: TimelineOrdering;
  person?: string;
  causal?: boolean;
  full_book?: boolean;
  /** Inclusive structure scope (optional; server intersects spoiler cutoff). */
  chapter_start?: number;
  chapter_end?: number;
}

export const timelineApi = {
  /** 准备 Phase07 层级并启动/恢复时间线（可能较慢，超时 5 分钟） */
  startOrResume: (novelId: string) =>
    api.post<TimelineRun>(`/timeline/${novelId}/start-or-resume`, null, {
      timeout: 300_000,
    }),
  status: (novelId: string) => api.get<TimelineRun>(`/timeline/${novelId}/status`),
  cancel: (novelId: string) => api.post<TimelineRun>(`/timeline/${novelId}/cancel`),
  resume: (novelId: string) =>
    api.post<TimelineRun>(`/timeline/${novelId}/resume`, null, { timeout: 300_000 }),
  getTimeline: (novelId: string, params?: TimelineQuery) =>
    api.get<TimelineEnvelope>(`/timeline/${novelId}`, { params }),
  getVersion: (novelId: string, versionId: number, params?: TimelineQuery) =>
    api.get<TimelineVersionView>(`/timeline/${novelId}/versions/${versionId}`, { params }),
  rollback: (novelId: string, targetVersionId: number, expectedRevision: number) =>
    api.post(`/timeline/${novelId}/rollback`, {
      target_version_id: targetVersionId,
      expected_revision: expectedRevision,
    }),
  updateEvent: (novelId: string, logicalEventId: string, fieldName: "title" | "description" | "event_type" | "time_expression", value: unknown) =>
    api.put(`/timeline/${novelId}/events/${logicalEventId}`, { field_name: fieldName, value }),
  setFullBookPreference: (novelId: string, fullBook: boolean) =>
    api.put(`/timeline/${novelId}/preference`, { full_book: fullBook }),
  /** @deprecated Use startOrResume. */
  extractTimeline: (novelId: string) => api.post<TimelineRun>(`/timeline/${novelId}/extract`),
  /** @deprecated Phase 08 edits require novel scope; retained for legacy callers only. */
  deleteEvent: (eventId: string) => api.delete(`/timeline/events/${eventId}`),
};
