/**
 * 剧情分析 API。
 */

import { api } from "./client";

/** 分析结果结构 */
export interface AnalysisResult {
  summary: string;
  characters: Array<{ name: string; role: string; description: string }>;
  key_events: Array<{ title: string; description: string; chapter: number }>;
  themes: string[];
}

export interface AnalysisRunResult {
  id: number;
  novel_id: number;
  chapter_id?: number | null;
  analysis_type: string;
  result_data: Record<string, unknown>;
  model_used?: string | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  created_at?: string | null;
  status?: string;
}

export interface HierarchyStatus {
  novel_id: number;
  active_build_id: string | null;
  ready: boolean;
  scene_count: number;
  chapter_count: number;
  evidence_count: number;
  sample_scenes?: Array<{
    scene_id: string;
    chapter_number: number;
    preview: string;
    char_count: number;
  }>;
}

export const analysisApi = {
  analyze: (
    novelId: string,
    body?: {
      analysis_type?: string;
      chapter_id?: number;
      use_llm?: boolean;
      rebuild_hierarchy?: boolean;
      model?: string;
    }
  ) => api.post<AnalysisRunResult>(`/analysis/${novelId}/analyze`, body ?? {}),
  getAnalysis: (novelId: string, analysisType?: string) =>
    api.get<{
      novel_id: number;
      status: string;
      items: AnalysisRunResult[];
      latest?: AnalysisRunResult;
      supported_types?: string[];
    }>(`/analysis/${novelId}`, {
      params: analysisType ? { analysis_type: analysisType } : undefined,
    }),
  analyzeChapter: (
    novelId: string,
    chapterId: string,
    body?: { analysis_type?: string; use_llm?: boolean }
  ) =>
    api.post<AnalysisRunResult>(
      `/analysis/${novelId}/chapters/${chapterId}/analyze`,
      body ?? { analysis_type: "chapter_summary" }
    ),
  hierarchy: (novelId: string) =>
    api.get<HierarchyStatus>(`/analysis/${novelId}/hierarchy`),
  rebuildHierarchy: (novelId: string) =>
    api.post<HierarchyStatus & { build_id?: string }>(
      `/analysis/${novelId}/hierarchy/rebuild`
    ),
};
