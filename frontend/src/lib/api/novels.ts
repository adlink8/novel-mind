/**
 * 小说 API：CRUD（列表、详情、上传、删除、章节）。
 */

import { api } from "./client";

/** 小说基础信息（列表展示用） */
export interface Novel {
  id: number;
  title: string;
  author: string | null;
  description: string | null;
  genre: string | null;
  word_count: number;
  chapter_count: number;
  /** importing | ready | chunking | embedding | analyzing | analyzed */
  status: string;
  /** 检索分块数量；0 表示尚未建索引 */
  chunk_count?: number;
  reading_progress?: {
    chapter_id?: number;
    progress_percent?: number;
    /** 时间线「显示全书」偏好，后端存在 reading_progress JSON 内 */
    timeline_full_book?: boolean;
  } | null;
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
  /** 兼容字段：实际为 job_id，用于轮询导入进度 */
  id: number;
  job_id: number;
  novel_id: number | null;
  title: string;
  status: string;
  message: string;
  chapter_count: number;
  word_count: number;
}

export interface NovelBulkDeleteResponse {
  deleted_ids: number[];
  skipped_ids: number[];
}

/** 导入进度状态 */
export interface ImportStatus {
  job_id?: number | null;
  novel_id?: number | null;
  stage: string;       // uploading / detecting / parsing / saving / ready / failed / error
  percent: number;     // 0-100
  message: string;
}

/** 阅读器书签（reader_bookmarks 表，owner-scoped 章节书签） */
export interface ReaderBookmark {
  id: number;
  owner_id: number;
  novel_id: number;
  chapter_id: number;
  /** 章内阅读位置百分比 0-100 */
  position_percent: number;
  label: string | null;
  note: string | null;
  created_at: string;
  updated_at: string;
}

/** 创建书签请求体 */
export interface ReaderBookmarkCreate {
  chapter_id: number;
  position_percent: number;
  label?: string | null;
  note?: string | null;
}

export const novelsApi = {
  list: () => api.get<NovelListResponse>("/novels"),
  get: (id: string) => api.get<Novel>(`/novels/${id}`),
  upload: (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    // Do not set Content-Type manually — axios/browser must add multipart boundary.
    // Large novels can take minutes to parse; extend timeout beyond default 30s.
    return api.post<NovelUploadResponse>("/novels/upload", formData, {
      timeout: 10 * 60 * 1000,
    });
  },
  delete: (id: string) => api.delete(`/novels/${id}`),
  update: (id: string | number, data: { title: string }) =>
    api.patch<Novel>(`/novels/${id}`, data),
  bulkDelete: (ids: number[]) =>
    api.delete<NovelBulkDeleteResponse>("/novels/bulk", {
      data: { novel_ids: ids },
    }),
  getChapters: (id: string) => api.get<Chapter[]>(`/novels/${id}/chapters`),
  getChapter: (novelId: string, chapterId: string) =>
    api.get<Chapter>(`/novels/${novelId}/chapters/${chapterId}`),
  updateProgress: (novelId: string, chapterId: number, progressPercent: number) =>
    api.patch(`/novels/${novelId}/progress`, { chapter_id: chapterId, progress_percent: progressPercent }),
  listBookmarks: (novelId: string | number) =>
    api.get<ReaderBookmark[]>(`/novels/${novelId}/bookmarks`),
  createBookmark: (novelId: string | number, data: ReaderBookmarkCreate) =>
    api.post<ReaderBookmark>(`/novels/${novelId}/bookmarks`, data),
  deleteBookmark: (novelId: string | number, bookmarkId: number) =>
    api.delete(`/novels/${novelId}/bookmarks/${bookmarkId}`),
  /** @deprecated 上传后应使用 getImportJobStatus(job_id) */
  getImportStatus: (novelId: string) => api.get<ImportStatus>(`/novels/${novelId}/import-status`),
  getImportJobStatus: (jobId: string | number) =>
    api.get<ImportStatus>(`/novels/import-jobs/${jobId}`),
};
