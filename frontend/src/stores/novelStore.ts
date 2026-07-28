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
  deleteNovels: (ids: number[]) => Promise<void>;
  renameNovel: (id: number, title: string) => Promise<void>;
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
      set({ novels: res.data.items, loading: false });
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
        novels: state.novels.filter((n) => String(n.id) !== id),
        currentNovel: String(state.currentNovel?.id) === id ? null : state.currentNovel,
        loading: false,
      }));
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Failed to delete novel";
      set({ error: message, loading: false });
    }
  },

  deleteNovels: async (ids: number[]) => {
    if (ids.length === 0) return;
    set({ loading: true, error: null });
    try {
      const res = await novelsApi.bulkDelete(ids);
      const deleted = new Set(res.data.deleted_ids);
      set((state) => ({
        novels: state.novels.filter((novel) => !deleted.has(novel.id)),
        currentNovel: state.currentNovel && deleted.has(state.currentNovel.id)
          ? null
          : state.currentNovel,
        loading: false,
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to delete novels";
      set({ error: message, loading: false });
      throw err;
    }
  },

  renameNovel: async (id: number, title: string) => {
    set({ error: null });
    try {
      const res = await novelsApi.update(id, { title });
      set((state) => ({
        novels: state.novels.map((novel) => novel.id === id ? { ...novel, ...res.data } : novel),
        currentNovel: state.currentNovel?.id === id
          ? { ...state.currentNovel, ...res.data }
          : state.currentNovel,
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to rename novel";
      set({ error: message });
      throw err;
    }
  },

  clearError: () => set({ error: null }),

  setCurrentNovel: (novel: Novel | null) => set({ currentNovel: novel }),
}));
