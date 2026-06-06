import axios from "axios";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  headers: {
    "Content-Type": "application/json",
  },
});

// ==================== 小说 API ====================

export interface Novel {
  id: string;
  title: string;
  author: string;
  description: string;
  genre: string;
  word_count: number;
  chapter_count: number;
  status: "importing" | "ready" | "analyzing" | "analyzed";
  created_at: string;
}

export interface Chapter {
  id: string;
  novel_id: string;
  chapter_number: number;
  title: string;
  content: string;
  summary?: string;
  word_count: number;
}

export const novelsApi = {
  list: () => api.get<Novel[]>("/novels"),
  get: (id: string) => api.get<Novel>(`/novels/${id}`),
  upload: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return api.post<Novel>("/novels/upload", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
  delete: (id: string) => api.delete(`/novels/${id}`),
  getChapters: (id: string) => api.get<Chapter[]>(`/novels/${id}/chapters`),
  getChapter: (novelId: string, chapterId: string) =>
    api.get<Chapter>(`/novels/${novelId}/chapters/${chapterId}`),
};

// ==================== 分析 API ====================

export interface AnalysisResult {
  summary: string;
  characters: Array<{
    name: string;
    role: string;
    description: string;
  }>;
  key_events: Array<{
    title: string;
    description: string;
    chapter: number;
  }>;
  themes: string[];
}

export const analysisApi = {
  analyze: (novelId: string) =>
    api.post<AnalysisResult>(`/analysis/${novelId}/analyze`),
  getAnalysis: (novelId: string) =>
    api.get<AnalysisResult>(`/analysis/${novelId}`),
  analyzeChapter: (novelId: string, chapterId: string) =>
    api.post(`/analysis/${novelId}/chapters/${chapterId}/analyze`),
};

// ==================== 时间线 API ====================

export interface TimelineEvent {
  id: string;
  novel_id: string;
  event_title: string;
  description: string;
  event_time: string;
  chapter_id: string;
  event_order: number;
  event_type: string;
  importance: "high" | "medium" | "low";
  characters_involved: string[];
}

export const timelineApi = {
  getTimeline: (novelId: string) =>
    api.get<TimelineEvent[]>(`/timeline/${novelId}`),
  extractTimeline: (novelId: string) =>
    api.post<TimelineEvent[]>(`/timeline/${novelId}/extract`),
  updateEvent: (eventId: string, data: Partial<TimelineEvent>) =>
    api.put(`/timeline/events/${eventId}`, data),
  deleteEvent: (eventId: string) => api.delete(`/timeline/events/${eventId}`),
};

// ==================== 人物 API ====================

export interface Character {
  id: string;
  novel_id: string;
  name: string;
  aliases: string[];
  description: string;
  personality: Record<string, string>;
  role: "protagonist" | "antagonist" | "supporting" | "minor";
  first_appearance: number;
  stats: Record<string, number>;
}

export interface CharacterRelation {
  id: string;
  source_character_id: string;
  target_character_id: string;
  relation_type: string;
  description: string;
  strength: number;
}

export const charactersApi = {
  getCharacters: (novelId: string) =>
    api.get<Character[]>(`/characters/${novelId}`),
  getRelations: (novelId: string) =>
    api.get<CharacterRelation[]>(`/characters/${novelId}/relations`),
  extractCharacters: (novelId: string) =>
    api.post(`/characters/${novelId}/extract`),
};

// ==================== 同人文 API ====================

export interface FanFiction {
  id: string;
  novel_id: string;
  title: string;
  description: string;
  style_config: Record<string, unknown>;
  branch_point: Record<string, unknown>;
  status: "draft" | "writing" | "completed";
}

export const fanfictionApi = {
  list: (novelId: string) => api.get<FanFiction[]>(`/fanfiction/${novelId}`),
  create: (data: Partial<FanFiction>) => api.post<FanFiction>("/fanfiction", data),
  continueWriting: (fanfictionId: string, prompt: string) =>
    api.post(`/fanfiction/${fanfictionId}/continue`, { prompt }),
};

// ==================== AI 模型 API ====================

export interface AIModelConfig {
  id: string;
  provider: "openai" | "anthropic" | "ollama" | "custom";
  model_name: string;
  base_url?: string;
  is_default: boolean;
}

export const aiModelsApi = {
  list: () => api.get<AIModelConfig[]>("/models"),
  create: (data: Partial<AIModelConfig>) => api.post("/models", data),
  test: (id: string) => api.post(`/models/${id}/test`),
  setDefault: (id: string) => api.post(`/models/${id}/default`),
  delete: (id: string) => api.delete(`/models/${id}`),
};
