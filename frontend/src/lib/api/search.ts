/**
 * 搜索 API。
 */

import { api } from "./client";

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
