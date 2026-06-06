"use client";

import { useEffect } from "react";
import { useNovelStore } from "@/stores/novelStore";
import { novelsApi, type Novel } from "@/lib/api";

export function useNovels() {
  const {
    novels,
    currentNovel,
    loading,
    error,
    fetchNovels,
    fetchNovel,
    deleteNovel,
    clearError,
    setCurrentNovel,
  } = useNovelStore();

  // Auto-fetch on mount
  useEffect(() => {
    fetchNovels();
  }, [fetchNovels]);

  const uploadNovel = async (file: File): Promise<Novel | null> => {
    try {
      const res = await novelsApi.upload(file);
      await fetchNovels();
      return res.data;
    } catch {
      return null;
    }
  };

  const getNovelById = (id: string): Novel | undefined => {
    return novels.find((n) => n.id === id);
  };

  const searchNovels = (query: string): Novel[] => {
    const q = query.toLowerCase().trim();
    if (!q) return novels;
    return novels.filter(
      (n) =>
        n.title.toLowerCase().includes(q) ||
        n.author.toLowerCase().includes(q)
    );
  };

  const sortedNovels = (sortBy: "title" | "date" | "wordCount" = "date") => {
    const sorted = [...novels];
    switch (sortBy) {
      case "title":
        sorted.sort((a, b) => a.title.localeCompare(b.title, "zh"));
        break;
      case "date":
        sorted.sort(
          (a, b) =>
            new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
        );
        break;
      case "wordCount":
        sorted.sort((a, b) => b.word_count - a.word_count);
        break;
    }
    return sorted;
  };

  return {
    novels,
    currentNovel,
    loading,
    error,
    fetchNovels,
    fetchNovel,
    deleteNovel,
    clearError,
    setCurrentNovel,
    uploadNovel,
    getNovelById,
    searchNovels,
    sortedNovels,
  };
}
