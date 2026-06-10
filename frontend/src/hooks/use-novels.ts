/**
 * 小说管理 Hook
 *
 * 封装 useNovelStore + 便捷工具方法，提供小说页面所需的所有数据和操作。
 * 组件中使用 const { novels, loading, uploadNovel, ... } = useNovels() 获取。
 *
 * 自动行为:
 * - 组件挂载时自动 fetchNovels()（无需手动调用）
 *
 * 额外工具方法（超出 Store 的便捷功能）:
 * - uploadNovel(file)  - 上传小说并刷新列表
 * - getNovelById(id)   - 从缓存中按 ID 查找小说
 * - searchNovels(query)- 本地搜索过滤
 * - sortedNovels(sort) - 本地排序
 */

"use client";

import { useEffect } from "react";
import { useNovelStore } from "@/stores/novelStore";
import { novelsApi, type Novel, type NovelUploadResponse } from "@/lib/api";

export function useNovels() {
  const {
    novels, currentNovel, loading, error,
    fetchNovels, fetchNovel, deleteNovel, clearError, setCurrentNovel,
  } = useNovelStore();

  // 组件挂载时自动加载小说列表
  useEffect(() => {
    fetchNovels();
  }, [fetchNovels]);

  /** 上传小说文件，成功后自动刷新列表 */
  const uploadNovel = async (file: File): Promise<NovelUploadResponse | null> => {
    try {
      const res = await novelsApi.upload(file);
      await fetchNovels();  // 上传成功后刷新列表
      return res.data;
    } catch {
      return null;
    }
  };

  /** 从本地缓存中按 ID 查找小说（不发请求） */
  const getNovelById = (id: string): Novel | undefined => {
    return novels.find((n) => String(n.id) === id);
  };

  /** 本地搜索过滤（标题和作者模糊匹配） */
  const searchNovels = (query: string): Novel[] => {
    const q = query.toLowerCase().trim();
    if (!q) return novels;
    return novels.filter(
      (n) =>
        n.title.toLowerCase().includes(q) ||
        (n.author ?? "").toLowerCase().includes(q)
    );
  };

  /** 本地排序（不发请求） */
  const sortedNovels = (sortBy: "title" | "date" | "wordCount" = "date") => {
    const sorted = [...novels];
    switch (sortBy) {
      case "title":
        sorted.sort((a, b) => a.title.localeCompare(b.title, "zh"));
        break;
      case "date":
        sorted.sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
        break;
      case "wordCount":
        sorted.sort((a, b) => b.word_count - a.word_count);
        break;
    }
    return sorted;
  };

  return {
    novels, currentNovel, loading, error,
    fetchNovels, fetchNovel, deleteNovel, clearError, setCurrentNovel,
    uploadNovel, getNovelById, searchNovels, sortedNovels,
  };
}
