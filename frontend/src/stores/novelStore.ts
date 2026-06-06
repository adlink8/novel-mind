import { create } from "zustand";
import { novelsApi, type Novel } from "@/lib/api";

interface NovelState {
  novels: Novel[];
  currentNovel: Novel | null;
  loading: boolean;
  error: string | null;

  fetchNovels: () => Promise<void>;
  fetchNovel: (id: string) => Promise<void>;
  deleteNovel: (id: string) => Promise<void>;
  clearError: () => void;
  setCurrentNovel: (novel: Novel | null) => void;
}

export const useNovelStore = create<NovelState>((set, get) => ({
  novels: [],
  currentNovel: null,
  loading: false,
  error: null,

  fetchNovels: async () => {
    set({ loading: true, error: null });
    try {
      const res = await novelsApi.list();
      set({ novels: res.data, loading: false });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to fetch novels";
      set({ error: message, loading: false });
    }
  },

  fetchNovel: async (id: string) => {
    set({ loading: true, error: null });
    try {
      const res = await novelsApi.get(id);
      set({ currentNovel: res.data, loading: false });
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to fetch novel";
      set({ error: message, loading: false });
    }
  },

  deleteNovel: async (id: string) => {
    set({ loading: true, error: null });
    try {
      await novelsApi.delete(id);
      set((state) => ({
        novels: state.novels.filter((n) => n.id !== id),
        currentNovel: state.currentNovel?.id === id ? null : state.currentNovel,
        loading: false,
      }));
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to delete novel";
      set({ error: message, loading: false });
    }
  },

  clearError: () => set({ error: null }),

  setCurrentNovel: (novel: Novel | null) => set({ currentNovel: novel }),
}));
