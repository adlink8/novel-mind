/**
 * AI 模型 API。
 */

import { api } from "./client";

export type AIModelProvider =
  | "openai"
  | "anthropic"
  | "ollama"
  | "custom"
  | "gemini"
  | "google";

export interface AIModelConfig {
  id: number;
  name: string;
  /** 后端存字符串；前端已知值见 AIModelProvider */
  provider: AIModelProvider | string;
  model_id: string;
  base_url?: string;
  tier: "quality" | "balanced" | "budget";
  max_tokens: number;
  temperature: number;
  is_default: boolean;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface AIModelConfigCreate {
  name: string;
  provider: AIModelConfig["provider"];
  model_id: string;
  api_key?: string;
  base_url?: string;
  tier?: AIModelConfig["tier"];
  max_tokens?: number;
  temperature?: number;
  is_default?: boolean;
}

export interface AIModelTestResponse {
  success: boolean;
  model_name: string;
  latency_ms: number;
  response_text?: string;
  error?: string;
}

export interface AIModelDiscoveryRequest {
  provider: AIModelConfig["provider"];
  base_url: string;
  api_key?: string;
}

export interface AIModelDiscoveryItem {
  id: string;
  name: string;
}

export interface AIModelDiscoveryResponse {
  models: AIModelDiscoveryItem[];
}

export interface AIModelProviderProfile {
  id: AIModelConfig["provider"];
  label: string;
  default_base_url?: string;
  credential_kind: "api_key" | "oauth_token" | "none" | string;
  credential_required: boolean;
}

export const aiModelsApi = {
  list: () => api.get<AIModelConfig[]>("/models"),
  providers: () => api.get<AIModelProviderProfile[]>("/models/providers"),
  discover: (data: AIModelDiscoveryRequest) =>
    api.post<AIModelDiscoveryResponse>("/models/discover", data),
  create: (data: AIModelConfigCreate) => api.post<AIModelConfig>("/models", data),
  test: (id: number) => api.post<AIModelTestResponse>(`/models/${id}/test`),
  setDefault: (id: number) => api.post(`/models/${id}/default`),
  delete: (id: number) => api.delete(`/models/${id}`),
};
