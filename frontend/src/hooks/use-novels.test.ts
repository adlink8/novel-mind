import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

const { upload, storeState } = vi.hoisted(() => {
  const fetchNovels = vi.fn().mockResolvedValue(undefined);
  const fetchNovel = vi.fn().mockResolvedValue(undefined);
  const deleteNovel = vi.fn().mockResolvedValue(undefined);
  const deleteNovels = vi.fn().mockResolvedValue(undefined);
  const renameNovel = vi.fn().mockResolvedValue(undefined);
  const clearError = vi.fn();
  const setCurrentNovel = vi.fn();

  const novels = [
    {
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
    },
    {
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
    },
  ];

  return {
    upload: vi.fn(),
    storeState: {
      novels,
      currentNovel: novels[0],
      loading: false,
      error: null,
      fetchNovels,
      fetchNovel,
      deleteNovel,
      deleteNovels,
      renameNovel,
      clearError,
      setCurrentNovel,
    },
  };
});

vi.mock("@/stores/novelStore", () => ({
  useNovelStore: () => storeState,
}));

vi.mock("@/lib/api", () => ({
  novelsApi: { upload },
}));

import { useNovels } from "./use-novels";

const uploadResponse = {
  id: 99,
  job_id: 99,
  novel_id: 1,
  title: "新上传",
  status: "ready",
  message: "ok",
  chapter_count: 3,
  word_count: 100,
};

describe("useNovels", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("挂载时自动获取小说列表", () => {
    renderHook(() => useNovels());
    expect(storeState.fetchNovels).toHaveBeenCalled();
  });

  it("暴露 store 状态与动作", () => {
    const { result } = renderHook(() => useNovels());
    expect(result.current.novels).toHaveLength(2);
    expect(result.current.currentNovel?.id).toBe(1);
    expect(result.current.fetchNovel).toBe(storeState.fetchNovel);
    expect(result.current.deleteNovel).toBe(storeState.deleteNovel);
    expect(result.current.deleteNovels).toBe(storeState.deleteNovels);
    expect(result.current.renameNovel).toBe(storeState.renameNovel);
    expect(result.current.clearError).toBe(storeState.clearError);
    expect(result.current.setCurrentNovel).toBe(storeState.setCurrentNovel);
  });

  it("uploadNovel 上传成功后刷新列表并返回响应", async () => {
    upload.mockResolvedValue({ data: uploadResponse });
    const { result } = renderHook(() => useNovels());
    const file = new File(["content"], "novel.txt", { type: "text/plain" });
    let res;
    await act(async () => {
      res = await result.current.uploadNovel(file);
    });
    expect(upload).toHaveBeenCalledWith(file);
    expect(storeState.fetchNovels).toHaveBeenCalled();
    expect(res).toEqual(uploadResponse);
  });

  it("uploadNovel 失败返回 null", async () => {
    upload.mockRejectedValue(new Error("upload failed"));
    const { result } = renderHook(() => useNovels());
    const callsBefore = storeState.fetchNovels.mock.calls.length;
    let res;
    await act(async () => {
      res = await result.current.uploadNovel(new File(["x"], "x.txt"));
    });
    expect(res).toBeNull();
    // 挂载 effect 已调用一次，失败路径不应触发刷新
    expect(storeState.fetchNovels.mock.calls.length).toBe(callsBefore);
  });

  it("getNovelById 按字符串 ID 查找", () => {
    const { result } = renderHook(() => useNovels());
    expect(result.current.getNovelById("1")?.title).toBe("玄幻世界");
    expect(result.current.getNovelById("missing")).toBeUndefined();
  });

  it("searchNovels 按标题/作者模糊匹配，空 query 返回全部", () => {
    const { result } = renderHook(() => useNovels());
    expect(result.current.searchNovels("").length).toBe(2);
    expect(result.current.searchNovels("   ").length).toBe(2);
    expect(result.current.searchNovels("玄幻").map((n) => n.id)).toEqual([1]);
    expect(result.current.searchNovels("李四").map((n) => n.id)).toEqual([2]);
    expect(result.current.searchNovels("不存在的")).toHaveLength(0);
  });

  it("sortedNovels 按 title/date/wordCount 排序", () => {
    const { result } = renderHook(() => useNovels());
    // zh locale 按拼音排序：都(du) < 玄(xuan)
    expect(result.current.sortedNovels("title").map((n) => n.title)).toEqual([
      "都市传奇",
      "玄幻世界",
    ]);
    expect(result.current.sortedNovels("date").map((n) => n.id)).toEqual([2, 1]);
    expect(result.current.sortedNovels("wordCount").map((n) => n.id)).toEqual([1, 2]);
    // 默认按 date 排序
    expect(result.current.sortedNovels().map((n) => n.id)).toEqual([2, 1]);
  });
});
