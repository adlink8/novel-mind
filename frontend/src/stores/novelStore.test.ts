import { describe, it, expect, vi, beforeEach } from "vitest";

const { novelsApi } = vi.hoisted(() => ({
  novelsApi: {
    list: vi.fn(),
    get: vi.fn(),
    delete: vi.fn(),
    update: vi.fn(),
    bulkDelete: vi.fn(),
  },
}));

vi.mock("@/lib/api", () => ({
  novelsApi,
}));

import { useNovelStore } from "./novelStore";

const novelA = {
  id: 1,
  title: "玄幻世界",
  author: "张三",
  description: null,
  genre: null,
  word_count: 5000,
  chapter_count: 20,
  status: "ready",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const novelB = {
  id: 2,
  title: "都市传奇",
  author: "李四",
  description: null,
  genre: null,
  word_count: 2000,
  chapter_count: 10,
  status: "ready",
  created_at: "2026-02-01T00:00:00Z",
  updated_at: "2026-02-01T00:00:00Z",
};

describe("useNovelStore", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useNovelStore.setState({
      novels: [],
      currentNovel: null,
      loading: false,
      error: null,
    });
  });

  it("fetchNovels 成功时填充列表", async () => {
    novelsApi.list.mockResolvedValue({
      data: { items: [novelA, novelB], total: 2, skip: 0, limit: 20 },
    });
    await useNovelStore.getState().fetchNovels();
    const state = useNovelStore.getState();
    expect(state.novels).toHaveLength(2);
    expect(state.loading).toBe(false);
    expect(state.error).toBeNull();
  });

  it("fetchNovels 失败时记录错误", async () => {
    novelsApi.list.mockRejectedValue(new Error("boom"));
    await useNovelStore.getState().fetchNovels();
    const state = useNovelStore.getState();
    expect(state.error).toMatch(/boom/);
    expect(state.loading).toBe(false);
  });

  it("fetchNovel 成功时设置 currentNovel", async () => {
    novelsApi.get.mockResolvedValue({ data: novelA });
    await useNovelStore.getState().fetchNovel("1");
    const state = useNovelStore.getState();
    expect(state.currentNovel?.id).toBe(1);
    expect(state.loading).toBe(false);
  });

  it("fetchNovel 失败时记录错误", async () => {
    novelsApi.get.mockRejectedValue(new Error("missing"));
    await useNovelStore.getState().fetchNovel("99");
    expect(useNovelStore.getState().error).toMatch(/missing/);
  });

  it("deleteNovel 成功时过滤列表并清空被删的 currentNovel", async () => {
    useNovelStore.setState({ novels: [novelA, novelB], currentNovel: novelA });
    novelsApi.delete.mockResolvedValue({ data: {} });
    await useNovelStore.getState().deleteNovel("1");
    const state = useNovelStore.getState();
    expect(state.novels).toEqual([novelB]);
    expect(state.currentNovel).toBeNull();
  });

  it("deleteNovel 删除非当前小说时保留 currentNovel", async () => {
    useNovelStore.setState({ novels: [novelA, novelB], currentNovel: novelA });
    novelsApi.delete.mockResolvedValue({ data: {} });
    await useNovelStore.getState().deleteNovel("2");
    expect(useNovelStore.getState().currentNovel?.id).toBe(1);
  });

  it("deleteNovel 失败时记录错误", async () => {
    novelsApi.delete.mockRejectedValue(new Error("forbidden"));
    await useNovelStore.getState().deleteNovel("1");
    expect(useNovelStore.getState().error).toMatch(/forbidden/);
  });

  it("deleteNovels 空数组直接返回", async () => {
    await useNovelStore.getState().deleteNovels([]);
    expect(novelsApi.bulkDelete).not.toHaveBeenCalled();
  });

  it("deleteNovels 成功时按 deleted_ids 过滤", async () => {
    useNovelStore.setState({ novels: [novelA, novelB], currentNovel: novelB });
    novelsApi.bulkDelete.mockResolvedValue({
      data: { deleted_ids: [2], skipped_ids: [] },
    });
    await useNovelStore.getState().deleteNovels([2]);
    const state = useNovelStore.getState();
    expect(state.novels).toEqual([novelA]);
    expect(state.currentNovel).toBeNull();
  });

  it("deleteNovels 未删除当前小说时保留 currentNovel", async () => {
    useNovelStore.setState({ novels: [novelA, novelB], currentNovel: novelA });
    novelsApi.bulkDelete.mockResolvedValue({
      data: { deleted_ids: [2], skipped_ids: [] },
    });
    await useNovelStore.getState().deleteNovels([2]);
    expect(useNovelStore.getState().currentNovel?.id).toBe(1);
  });

  it("deleteNovels 失败时记录错误并 rethrow", async () => {
    novelsApi.bulkDelete.mockRejectedValue(new Error("bulk failed"));
    await expect(useNovelStore.getState().deleteNovels([1])).rejects.toThrow(
      "bulk failed"
    );
    expect(useNovelStore.getState().error).toMatch(/bulk failed/);
  });

  it("renameNovel 成功时更新列表与 currentNovel", async () => {
    useNovelStore.setState({ novels: [novelA, novelB], currentNovel: novelA });
    novelsApi.update.mockResolvedValue({
      data: { ...novelA, title: "新书名" },
    });
    await useNovelStore.getState().renameNovel(1, "新书名");
    const state = useNovelStore.getState();
    expect(state.novels[0].title).toBe("新书名");
    expect(state.currentNovel?.title).toBe("新书名");
  });

  it("renameNovel 未重命名当前小说时 currentNovel 不变", async () => {
    useNovelStore.setState({ novels: [novelA, novelB], currentNovel: novelA });
    novelsApi.update.mockResolvedValue({
      data: { ...novelB, title: "改后的书" },
    });
    await useNovelStore.getState().renameNovel(2, "改后的书");
    expect(useNovelStore.getState().currentNovel?.title).toBe("玄幻世界");
  });

  it("renameNovel 失败时记录错误并 rethrow", async () => {
    novelsApi.update.mockRejectedValue(new Error("rename failed"));
    await expect(useNovelStore.getState().renameNovel(1, "x")).rejects.toThrow(
      "rename failed"
    );
    expect(useNovelStore.getState().error).toMatch(/rename failed/);
  });

  it("clearError 清空错误", () => {
    useNovelStore.setState({ error: "stale" });
    useNovelStore.getState().clearError();
    expect(useNovelStore.getState().error).toBeNull();
  });

  it("setCurrentNovel 设置当前小说", () => {
    useNovelStore.getState().setCurrentNovel(novelA);
    expect(useNovelStore.getState().currentNovel?.id).toBe(1);
    useNovelStore.getState().setCurrentNovel(null);
    expect(useNovelStore.getState().currentNovel).toBeNull();
  });

  it("fetchNovels 非 Error 拒绝使用默认文案", async () => {
    novelsApi.list.mockRejectedValue("boom");
    await useNovelStore.getState().fetchNovels();
    expect(useNovelStore.getState().error).toBe("Failed to fetch novels");
  });

  it("fetchNovel 非 Error 拒绝使用默认文案", async () => {
    novelsApi.get.mockRejectedValue("missing");
    await useNovelStore.getState().fetchNovel("99");
    expect(useNovelStore.getState().error).toBe("Failed to fetch novel");
  });

  it("deleteNovel 非 Error 拒绝使用默认文案", async () => {
    novelsApi.delete.mockRejectedValue("forbidden");
    await useNovelStore.getState().deleteNovel("1");
    expect(useNovelStore.getState().error).toBe("Failed to delete novel");
  });

  it("deleteNovels 非 Error 拒绝使用默认文案并 rethrow", async () => {
    novelsApi.bulkDelete.mockRejectedValue("bulk");
    await expect(useNovelStore.getState().deleteNovels([1])).rejects.toBe("bulk");
    expect(useNovelStore.getState().error).toBe("Failed to delete novels");
  });

  it("renameNovel 非 Error 拒绝使用默认文案并 rethrow", async () => {
    novelsApi.update.mockRejectedValue("rename");
    await expect(useNovelStore.getState().renameNovel(1, "x")).rejects.toBe(
      "rename"
    );
    expect(useNovelStore.getState().error).toBe("Failed to rename novel");
  });
});
