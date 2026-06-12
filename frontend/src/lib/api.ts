/**
 * API 客户端模块
 *
 * 基于 Axios 封装所有后端 API 调用，按业务域组织:
 * - novelsApi     : 小说 CRUD（列表、详情、上传、删除、章节）
 * - analysisApi   : 剧情分析
 * - timelineApi   : 时间线事件
 * - charactersApi : 人物关系
 * - fanfictionApi : 同人文
 * - aiModelsApi   : AI 模型配置
 *
 * 基础配置:
 * - baseURL: 通过 NEXT_PUBLIC_API_URL 环境变量配置，默认 "/api"（走 Next.js rewrite 代理）
 * - timeout: 30 秒
 * - Content-Type: application/json（上传时自动切换为 multipart/form-data）
 *
 * 每个 API 模块导出一组函数，返回 AxiosPromise，组件中通过 .data 获取响应体。
 */

import axios from "axios";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "/api";

export const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
});

export interface AuthUser {
  id: number;
  username: string;
  email: string;
  is_active: boolean;
}

export const authApi = {
  me: () => api.get<AuthUser>("/auth/me"),
  login: (username: string, password: string) =>
    api.post("/auth/login", { username, password }),
  register: (username: string, email: string, password: string) =>
    api.post<AuthUser>("/auth/register", { username, email, password }),
  logout: () => api.post("/auth/logout"),
};

// ==================== 小说 API ====================

/** 小说基础信息（列表展示用） */
export interface Novel {
  id: number;
  title: string;
  author: string | null;
  description: string | null;
  genre: string | null;
  word_count: number;
  chapter_count: number;
  status: "importing" | "ready" | "analyzing" | "analyzed";
  created_at: string;
  updated_at: string;
}

/** 章节信息 */
export interface Chapter {
  id: number;
  novel_id: number;
  chapter_number: number;
  title: string;
  content: string;       // 章节完整正文内容
  summary?: string;
  word_count: number;
  created_at: string;
  updated_at: string;
}

/** 小说列表分页响应 */
export interface NovelListResponse {
  items: Novel[];
  total: number;
  skip: number;
  limit: number;
}

/** 小说上传响应 */
export interface NovelUploadResponse {
  id: number;
  title: string;
  status: Novel["status"];
  message: string;
  chapter_count: number;
  word_count: number;
}

/** 导入进度状态 */
export interface ImportStatus {
  novel_id: number;
  stage: string;       // uploading / detecting / parsing / saving / ready / error
  percent: number;     // 0-100
  message: string;
}

export const novelsApi = {
  list: () => api.get<NovelListResponse>("/novels"),
  get: (id: string) => api.get<Novel>(`/novels/${id}`),
  upload: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return api.post<NovelUploadResponse>("/novels/upload", formData, {
      headers: { "Content-Type": "multipart/form-data" },
    });
  },
  delete: (id: string) => api.delete(`/novels/${id}`),
  getChapters: (id: string) => api.get<Chapter[]>(`/novels/${id}/chapters`),
  getChapter: (novelId: string, chapterId: string) =>
    api.get<Chapter>(`/novels/${novelId}/chapters/${chapterId}`),
  updateProgress: (novelId: string, chapterId: number, progressPercent: number) =>
    api.patch(`/novels/${novelId}/progress`, { chapter_id: chapterId, progress_percent: progressPercent }),
  getImportStatus: (novelId: string) => api.get<ImportStatus>(`/novels/${novelId}/import-status`),
};

// ==================== 分析 API ====================

/** 分析结果结构 */
export interface AnalysisResult {
  summary: string;
  characters: Array<{ name: string; role: string; description: string }>;
  key_events: Array<{ title: string; description: string; chapter: number }>;
  themes: string[];
}

export const analysisApi = {
  analyze: (novelId: string) => api.post<AnalysisResult>(`/analysis/${novelId}/analyze`),
  getAnalysis: (novelId: string) => api.get<AnalysisResult>(`/analysis/${novelId}`),
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
  getTimeline: (novelId: string) => api.get<TimelineEvent[]>(`/timeline/${novelId}`),
  extractTimeline: (novelId: string) => api.post<TimelineEvent[]>(`/timeline/${novelId}/extract`),
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
  getCharacters: (novelId: string) => api.get<Character[]>(`/characters/${novelId}`),
  getRelations: (novelId: string) => api.get<CharacterRelation[]>(`/characters/${novelId}/relations`),
  extractCharacters: (novelId: string) => api.post(`/characters/${novelId}/extract`),
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
  id: number;
  name: string;
  provider: "openai" | "anthropic" | "ollama" | "custom";
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

export const aiModelsApi = {
  list: () => api.get<AIModelConfig[]>("/models"),
  create: (data: AIModelConfigCreate) => api.post<AIModelConfig>("/models", data),
  test: (id: number) => api.post<AIModelTestResponse>(`/models/${id}/test`),
  setDefault: (id: number) => api.post(`/models/${id}/default`),
  delete: (id: number) => api.delete(`/models/${id}`),
};

// ==================== 搜索 API ====================

/** 单条搜索结果 */
export interface SearchResult {
  novel_id: number;
  novel_title: string | null;
  chapter_id: number | null;
  chapter_title: string | null;
  chunk_id: number;
  chunk_index: number;
  content_snippet: string;
  score: number;
}

/** 搜索响应 */
export interface SearchResponse {
  results: SearchResult[];
  total: number;
  query: string;
}

export const searchApi = {
  /** 全局搜索（跨所有小说） */
  global: (query: string, topK: number = 10) =>
    api.post<SearchResponse>("/search", { query, top_k: topK }),

  /** 指定小说内搜索 */
  inNovel: (novelId: number, query: string, topK: number = 10) =>
    api.post<SearchResponse>(`/search/novels/${novelId}`, { query, top_k: topK }),
};
