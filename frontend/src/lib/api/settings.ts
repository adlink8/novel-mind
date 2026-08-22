/**
 * 用户设置 API。
 */

import { api } from "./client";

/** 智能路由策略偏好 */
export type RoutingPreference = "quality" | "balanced" | "budget";

export interface RoutingPreferenceResponse {
  preference: RoutingPreference;
}

export interface AgentTaskModelBindings {
  qa: number | null;
  deep_analysis: number | null;
  continuation: number | null;
  illustration: number | null;
  rag_eval: number | null;
  embedding: number | null;
}

export interface AgentSettingsPayload {
  auto_deep_analysis: boolean;
  memory_enabled: boolean;
  memory_retention_days: number | null;
  show_analysis_progress: boolean;
  notify_analysis_complete: boolean;
  auto_create_candidate_artifacts: boolean;
  task_model_bindings: AgentTaskModelBindings;
}

export interface UserPreferenceMemory {
  id: number;
  source_message_id: number;
  kind: string;
  value: string;
  confidence: number;
  explicit: boolean;
  created_at: string;
  expires_at: string | null;
}

export interface UserPreferenceMemoryList {
  items: UserPreferenceMemory[];
  total: number;
}

export const settingsApi = {
  getRouting: () => api.get<RoutingPreferenceResponse>("/settings/routing"),
  putRouting: (preference: RoutingPreference) =>
    api.put<RoutingPreferenceResponse>("/settings/routing", { preference }),
  getAgent: () => api.get<AgentSettingsPayload>("/settings/agent"),
  putAgent: (settings: AgentSettingsPayload) =>
    api.put<AgentSettingsPayload>("/settings/agent", settings),
  listMemoryPreferences: () =>
    api.get<UserPreferenceMemoryList>("/memory/preferences"),
  deleteMemoryPreference: (memoryId: number) =>
    api.delete<void>(`/memory/preferences/${memoryId}`),
  clearMemoryPreferences: () => api.delete<void>("/memory/preferences"),
};
